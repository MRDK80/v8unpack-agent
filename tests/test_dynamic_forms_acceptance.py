"""Mixed static/dynamic acceptance matrix: source facts, not runtime instances."""
import json
from dataclasses import replace

import pytest

from scripts.form_fixture_issue500 import encoded, fixture_pair
from v8unpack_agent.dynamic_forms import OperationKind
from v8unpack_agent.dynamic_forms import ResolutionStatus as R
from v8unpack_agent.dynamic_forms_binding import BindingReason, bind_form_operations
from v8unpack_agent.dynamic_forms_context import (
    DynamicFormAnalysis,
    _DynamicAnalysisCollector,
)
from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import FormEntry

MIXED='''Procedure Build()
Attrs=New Array;
Attrs.Add(New FormAttribute("DynamicData",New TypeDescription("String")));
ChangeAttributes(Attrs);
Command=Commands.Add("DynamicCommand");
Group=Items.Add("DynamicGroup",Type("FormGroup"));
If FeatureEnabled Then
    Field=Items.Insert(Prefix,Type("FormField"),Group);
    Field.DataPath="DynamicData";
    Field.CommandName="DynamicCommand";
    Field.Width=8;
    Field.Width=9;
EndIf;
Items.Move(Group,Items.Declared);
Items.Delete(Group);
Commands.Delete(Command);
For Each Row In Rows Do
    LoopField=Items.Add(Prefix,Type("FormField"));
    LoopField.Height=5;
EndDo;
Helper(ThisObject);
UnknownControl.Visible=True;
EndProcedure
Procedure Helper(Form)
Form.Items.Declared.Width=8;
EndProcedure
'''


def mixed():
    return bind_form_operations(MIXED,'CommonForm/Demo/Module.bsl',
                                declared_elements=('Declared',), declared_attributes=('DataValue',))


def test_mixed_all_eight_kinds_and_explicit_residual():
    result=mixed();operations=result.bound.operations
    assert {o.operation_kind for o in operations}==set(OperationKind)
    assert any(o.operation_resolution==R.EXACT for o in operations)
    assert any(o.operation_resolution==R.PARTIAL for o in operations)
    assert any(o.operation_resolution==R.UNRESOLVED for o in operations)
    assert any(o.conditions for o in operations)
    assert any(d.reason==BindingReason.MULTIPLE_VALUES for c in result.candidates for d in c.diagnostics)
    assert all(c.runtime_truth=='unknown' for c in result.bound.conditions)
    assert any(o.procedure=='Helper' and o.operation_resolution==R.UNRESOLVED for o in operations)


def test_loop_is_one_source_template_with_exact_local_identity():
    result=mixed();conditions={c.condition_id:c for c in result.bound.conditions}
    loop=[o for o in result.bound.operations if o.operation_kind==OperationKind.ELEMENT_ADD
          and any(conditions[g.condition_id].kind.value=='loop' for g in o.conditions)]
    assert len(loop)==1
    assert loop[0].target.resolution==R.EXACT
    assert loop[0].target.name.resolution==R.PARTIAL
    assert loop[0].target.valid_in==loop[0].conditions


@pytest.mark.parametrize('body',[
    '// Items.Add("Fake",Type("FormField"));',
    'Text="Items.Add(""Fake"",Type(""FormField""));";',
    'Rows=New Array; Rows.Add("Fake");',
    'Value.Visible=True;',
])
def test_noise_and_data_objects_never_confirm_gui_operations(body):
    result=bind_form_operations('Procedure Demo()\n'+body+'\nEndProcedure','Form/Module.bsl')
    assert all(o.operation_resolution==R.UNRESOLVED for o in result.bound.operations)


def test_shadowed_builtin_not_confirmed():
    text='Procedure ChangeAttributes(Value)\nEndProcedure\nProcedure Build()\nChangeAttributes(Attrs);\nEndProcedure'
    result=bind_form_operations(text,'Form/Module.bsl')
    assert all(o.operation_resolution==R.UNRESOLVED for o in result.bound.operations)


def test_all_public_views_preserve_status_and_identity_separation():
    index=mixed();analysis=DynamicFormAnalysis('complete',index=index)
    context=FormContext('Demo','','CommonForm','',MIXED,
                        FormSummary(elements=[{'name':'Declared','type':'Field'}]),{},
                        dynamic_analysis=analysis)
    assert {o['operation_resolution'] for o in analysis.projection()['operations']}=={'exact','partial','unresolved'}
    assert analysis.to_dict()['analysis']==index.to_dict()
    assert json.loads(index.to_json())==index.to_dict()
    before=context.summary
    for representation in ['legacy','framed']:
        text=to_llm_prompt_fragment(context,representation=representation)
        assert '## SUMMARY' in text and '## DYNAMIC_FORM_OPERATIONS' in text
    assert context.summary==before
    collector=_DynamicAnalysisCollector();collector.add('form-1',analysis);collector.add('form-1',analysis)
    metrics=collector.summarize()
    assert metrics.forms==1 and metrics.operations==len(index.bound.operations)
    assert metrics.operations==metrics.exact+metrics.partial+metrics.unresolved
    assert metrics.candidates==len(index.candidates)


@pytest.mark.parametrize('kind,positive,negative',[
    ('element_add','Items.Add("X",Type("FormField"));','Rows.Add("X");'),
    ('element_insert','Items.Insert("X",Type("FormField"));','Rows.Insert(0,"X");'),
    ('element_delete','Items.Delete(Items.Declared);','Rows.Delete(Value);'),
    ('element_move','Items.Move(Items.Declared,Items.Parent);','Rows.Move(Value,Before);'),
    ('attributes_change','ChangeAttributes(Attrs);','CustomChangeAttributes(Attrs);'),
    ('command_add','Commands.Add("X");','Rows.Add("X");'),
    ('command_delete','Commands.Delete(Commands.DeclaredCommand);','Rows.Delete(Value);'),
    ('property_set','Items.Declared.Width=8;','Row.Width=8;'),
])
def test_supported_kind_positive_and_non_gui_negative(kind,positive,negative):
    def analyze(body):
        return bind_form_operations('Procedure Build()\n'+body+'\nEndProcedure','Form/Module.bsl',
                                    declared_elements=('Declared','Parent'),declared_commands=('DeclaredCommand',))
    good=analyze(positive)
    assert any(o.operation_kind.value==kind and o.operation_resolution!=R.UNRESOLVED for o in good.bound.operations)
    bad=analyze('Rows=New Array;\n'+negative)
    assert all(o.operation_resolution==R.UNRESOLVED for o in bad.bound.operations)


def test_mixed_builder_runner_and_report_share_one_index(tmp_path,monkeypatch):
    from v8unpack_agent import runner
    from v8unpack_agent.scan_forms import FormScanIndex
    folder=tmp_path/'CommonForm'/'Demo';folder.mkdir(parents=True)
    metadata,elements=fixture_pair(['DataValue'])
    elements['tree']=[{'name':'Declared','type':'Field'}]
    meta=folder/'Form.json';elem=folder/'Form.elem.json';module=folder/'Module.bsl'
    meta.write_bytes(encoded(metadata));elem.write_bytes(encoded(elements));module.write_bytes(MIXED.encode('utf-8'))
    entry=FormEntry('CommonForm','','','Demo',folder,module,meta,elem_json_path=elem.relative_to(tmp_path))
    context=build_form_context(entry,tmp_path,include_dynamic_analysis=True)
    monkeypatch.setattr(runner,'scan_forms',lambda *a,**kw:FormScanIndex(forms=[entry]))
    result=runner.run_pipeline(runner.RunOptions(tmp_path,include_common_modules=False,
        include_skd=False,include_reference_resolution=False,include_dynamic_analysis=True))
    metrics=context.dynamic_analysis.metrics();summary=result.report.dynamic_analysis.to_dict()
    assert summary['forms']==1 and summary['complete']==1
    assert all(summary[k]==v for k,v in metrics.items())
    assert result.report.summary.found==1
    assert all(type(v) is int for v in summary.values())
    assert replace(context,dynamic_analysis=None).summary==context.summary


@pytest.mark.parametrize('property_name,value',[
    ('Visible','True'),('Enabled','False'),('Title','"Demo"'),('ReadOnly','True'),
    ('DataPath','"DataValue"'),('CommandName','"DeclaredCommand"'),('Type','ControlType'),
    ('Parent','Items.Parent'),('CurrentPage','Items.Page'),('CurrentRow','Undefined'),
    ('ToolTip','"Demo"'),('Width','8'),('Height','9'),
])
def test_each_closed_property_requires_a_proven_gui_receiver(property_name,value):
    def analyze(receiver):
        return bind_form_operations('Procedure Build()\n'+receiver+'.'+property_name+'='+value+';\nEndProcedure',
            'Form/Module.bsl',declared_elements=('Declared','Parent','Page'),
            declared_commands=('DeclaredCommand',),declared_attributes=('DataValue',))
    good=analyze('Items.Declared');assert len(good.bound.operations)==1
    assert good.bound.operations[0].operation_resolution!=R.UNRESOLVED
    assert good.bound.operations[0].target.resolution==R.EXACT
    assert all(o.operation_resolution==R.UNRESOLVED for o in analyze('DataRecord').bound.operations)
