"""Dynamic analysis records and namespace adapter contracts (#217)."""
import pytest

from v8unpack_agent.dynamic_forms_binding import bind_form_operations
from v8unpack_agent.dynamic_forms_context import DynamicFormAnalysis


def analysis(body):
    return DynamicFormAnalysis('complete', index=bind_form_operations(
        'Procedure Build()\n'+body+'\nEndProcedure', 'Form/Module.bsl'))


def test_no_raw_bound_double_count():
    value=analysis('X=Items.Add("Demo",Type("FormField")); X.Width=8;')
    metrics=value.metrics()
    assert metrics['operations']==2
    assert metrics['exact']+metrics['partial']+metrics['unresolved']==2
    assert len(value.index.raw.operations)==len(value.index.bound.operations)==2
    assert len(value.projection()['operations'])==2


def test_batch_attributes_not_counted_per_candidate():
    value=analysis('Attrs=New Array; Attrs.Add(New FormAttribute("One",New TypeDescription("String"))); Attrs.Add(New FormAttribute("Two",New TypeDescription("String"))); ChangeAttributes(Attrs);')
    assert value.metrics()['operations']==1
    assert value.metrics()['candidates']==3
    assert sum(c.reference.name is not None and c.reference.name.value in {'One','Two'} for c in value.index.candidates)==2


def test_exact_reference_and_partial_name_are_separate():
    value=analysis('X=Items.Add(Prefix,Type("FormField")); X.Width=8;')
    rows=value.projection()['operations']
    add=next(o for o in rows if o['operation_kind']=='element_add')
    setter=next(o for o in rows if o['operation_kind']=='property_set')
    assert add['target']['resolution']=='exact'
    assert add['target']['name']['resolution']=='partial'
    assert setter['target']['resolution']=='exact'
    assert setter['operation_resolution']=='exact'


def test_conditional_is_orthogonal():
    value=analysis('If Flag Then X=Items.Add(Prefix,Type("FormField")); X.Width=8; EndIf;')
    metrics=value.metrics();assert metrics['operations']==metrics['conditional']==2
    assert metrics['partial']==1 and metrics['exact']==1
    assert all(row['runtime_execution']=='unknown' and row['conditional'] for row in value.projection()['operations'])
    assert all(c['expression_resolution']=='exact' and c['runtime_truth']=='unknown' for c in value.projection()['conditions'])


@pytest.mark.parametrize('status',['incomplete','unsupported','unavailable'])
def test_failure_never_empty_complete(status):
    value=DynamicFormAnalysis(status,'source_unavailable')
    assert value.metrics()['operations']==0
    assert value.to_dict()['analysis'] is None
    assert value.projection()['status']==status


@pytest.mark.parametrize('status,index,reason',[
    ('complete',None,None),('complete','invalid',None),('unavailable',None,None),
    ('invalid',None,'source_unavailable'),
])
def test_invalid_records_rejected(status,index,reason):
    with pytest.raises((TypeError,ValueError)):
        DynamicFormAnalysis(status,reason,index)


def test_data_containers_do_not_become_gui_operations():
    value=analysis('Rows=New Array; Rows.Add("Demo");')
    assert value.metrics()['operations']==0


def test_adapter_managed_metadata_and_root_attribute_shadow(tmp_path):
    from scripts.form_fixture_issue500 import write_fixture
    from v8unpack_agent.dynamic_forms_context import analyze_dynamic_form
    meta,elem=write_fixture(tmp_path,['ReadOnly'])
    result=analyze_dynamic_form('Procedure Build()\nReadOnly=True; X=Items.Add("Field",Type("FormField"));\nEndProcedure','Form/Module.bsl',meta,elem)
    assert result.status=='complete'
    assert result.metrics()['operations']==1
    assert result.index.bound.operations[0].operation_kind.value=='element_add'


def test_adapter_missing_metadata_not_empty_complete(tmp_path):
    from v8unpack_agent.dynamic_forms_context import analyze_dynamic_form
    result=analyze_dynamic_form('', 'Form/Module.bsl',tmp_path/'missing.json',tmp_path/'missing.elem.json')
    assert result.status=='incomplete' and result.index is None
