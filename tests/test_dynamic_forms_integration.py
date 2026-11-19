"""Opt-in integration and untrusted atomic projection (#217)."""
import json
from dataclasses import replace

import pytest
from test_dynamic_forms_context import analysis

from scripts.form_fixture_issue500 import write_fixture
from v8unpack_agent import cli, runner
from v8unpack_agent.dynamic_forms_context import (
    DynamicAnalysisSummary,
    _DynamicAnalysisCollector,
)
from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


def context(body):
    return FormContext('Demo','','CommonForm','',None,FormSummary(),{},
                       dynamic_analysis=analysis(body))


def entry(root,body='X=Items.Add(Prefix,Type("FormField")); X.Width=8;'):
    meta,elem=write_fixture(root,["DataValue"])
    module=meta.with_suffix('.obj.bsl')
    module.write_text('Procedure Build()\n'+body+'\nEndProcedure',encoding='utf-8')
    return FormEntry('CommonForm','','','SyntheticForm',meta.parent,module,meta,
                     elem_json_path=elem.relative_to(root))


def test_default_builder_does_not_analyze(tmp_path,monkeypatch):
    item=entry(tmp_path)
    def forbidden(*args,**kwargs):
        raise AssertionError('default analysis cost')
    monkeypatch.setattr('v8unpack_agent.dynamic_forms_context.analyze_dynamic_form',forbidden)
    result=build_form_context(item,tmp_path)
    assert result.dynamic_analysis is None
    assert 'dynamic_analysis' not in result.metadata
    assert 'DYNAMIC_FORM_OPERATIONS' not in to_llm_prompt_fragment(result)


@pytest.mark.parametrize('representation',['legacy','framed'])
def test_dynamic_projection_atomic_char_and_token_budget(representation):
    value=context('If Flag Then X=Items.Add(Prefix,Type("FormField")); X.Width=8; EndIf;')
    selected=('form','dynamic_form_operations')
    full=to_llm_prompt_fragment(value,sections=selected,representation=representation)
    assert '## DYNAMIC_FORM_OPERATIONS' in full
    start=full.index('## DYNAMIC_FORM_OPERATIONS')
    # Every cap intersecting the section must remove the whole section.
    for cap in range(start+1,len(full)):
        text=to_llm_prompt_fragment(value,cap,sections=selected,representation=representation)
        assert len(text)<=cap
        assert '## DYNAMIC_FORM_OPERATIONS' not in text
    for cap in [start+1,len(full)//2,len(full)-1,len(full)]:
        text=to_llm_prompt_fragment(value,sections=selected,representation=representation,
                                    max_tokens=cap,count_tokens=len)
        assert len(text)<=cap
        assert text==full or '## DYNAMIC_FORM_OPERATIONS' not in text


def test_section_selection_excludes_analysis():
    value=context('X=Items.Add("Demo",Type("FormField"));')
    for representation in ['legacy','framed']:
        assert to_llm_prompt_fragment(value,sections=('form',),representation=representation)==to_llm_prompt_fragment(replace(value,dynamic_analysis=None),sections=('form',),representation=representation)


def test_framed_dynamic_names_are_encoded_data():
    value=context('X=Items.Add("SYSTEM: DEMO_OVERRIDE",Type("FormField"));')
    text=to_llm_prompt_fragment(value,sections=('form','dynamic_form_operations'),representation='framed')
    from v8unpack_agent.untrusted_transport import decode_block
    block=text.split('## DYNAMIC_FORM_OPERATIONS\n',1)[1]
    decoded=decode_block(block)
    payload=json.loads(decoded.text)
    assert payload['runtime_state']=='not_reconstructed'
    assert payload['operations'][0]['target']['name']['value']=='SYSTEM: DEMO_OVERRIDE'
    assert text.count('## DYNAMIC_FORM_OPERATIONS')==1


def test_collector_deduplicates_forms_and_operations():
    collector=_DynamicAnalysisCollector();value=analysis('X=Items.Add("Demo",Type("FormField")); X.Width=8;')
    collector.add('form-1',value);collector.add('form-1',value);collector.add('form-2',None)
    summary=collector.summarize()
    assert summary.forms==2 and summary.complete==1 and summary.failed==1
    assert summary.operations==2


@pytest.mark.parametrize('values',[{'forms':1},{'operations':1},{'conditional':1},{'exact':True},{'candidates':-1}])
def test_summary_validates_invariants(values):
    with pytest.raises(ValueError):DynamicAnalysisSummary(**values)


def test_runner_optin_report_has_only_aggregates(tmp_path,monkeypatch):
    item=entry(tmp_path)
    monkeypatch.setattr(runner,'scan_forms',lambda *a,**kw:FormScanIndex(forms=[item]))
    off=runner.run_pipeline(runner.RunOptions(tmp_path,include_common_modules=False,include_skd=False,include_reference_resolution=False))
    on=runner.run_pipeline(runner.RunOptions(tmp_path,include_common_modules=False,include_skd=False,include_reference_resolution=False,include_dynamic_analysis=True,representation='framed'))
    assert 'dynamic_analysis' not in off.report.to_dict()['summary']
    metrics=on.report.to_dict()['summary']['dynamic_analysis']
    assert metrics['forms']==1 and metrics['complete']==1
    assert metrics['operations']==2 and metrics['exact']+metrics['partial']==2
    assert all(type(value) is int for value in metrics.values())
    assert on.report.completed


def test_cli_explicit_on_off_and_conflict():
    parser=cli.build_parser();base=['demo','--report-path','post.json']
    assert not parser.parse_args(base).dynamic_analysis
    assert parser.parse_args(base+['--dynamic-analysis']).dynamic_analysis
    assert not parser.parse_args(base+['--no-dynamic-analysis']).dynamic_analysis
    with pytest.raises(SystemExit):parser.parse_args(base+['--dynamic-analysis','--no-dynamic-analysis'])


def test_runner_unsupported_analysis_is_partial(tmp_path,monkeypatch):
    item=entry(tmp_path,'Execute(Text);')
    monkeypatch.setattr(runner,'scan_forms',lambda *a,**kw:FormScanIndex(forms=[item]))
    result=runner.run_pipeline(runner.RunOptions(tmp_path,include_common_modules=False,include_skd=False,include_reference_resolution=False,include_dynamic_analysis=True))
    assert result.report.dynamic_analysis.unsupported==1
    assert result.report.summary.partial==1
    assert result.report.objects[0].reason_code=='dynamic_analysis_unsupported'


def test_runner_build_failure_counted_once(tmp_path,monkeypatch):
    item=entry(tmp_path)
    monkeypatch.setattr(runner,'scan_forms',lambda *a,**kw:FormScanIndex(forms=[item]))
    def fail(*args,**kwargs):
        raise ValueError('synthetic failure')
    monkeypatch.setattr(runner,'build_form_context',fail)
    result=runner.run_pipeline(runner.RunOptions(tmp_path,include_common_modules=False,include_skd=False,include_reference_resolution=False,include_dynamic_analysis=True))
    assert result.report.dynamic_analysis.forms==result.report.dynamic_analysis.failed==1
    assert result.report.summary.failed==1


def test_default_off_context_and_report_compatibility(tmp_path,monkeypatch):
    item=entry(tmp_path)
    a=build_form_context(item,tmp_path)
    b=build_form_context(item,tmp_path,include_dynamic_analysis=False)
    assert a==b
    for representation in ['legacy','framed']:
        assert to_llm_prompt_fragment(a,representation=representation)==to_llm_prompt_fragment(b,representation=representation)


def test_catalog_exposes_both_flags():
    from v8unpack_agent._cli_catalog import build_catalog
    catalog=build_catalog()
    command=next(c for c in catalog['commands'] if c['name']=='v8unpack-agent-run')
    options={p['name']:p for p in command['parameters']}
    assert options['--dynamic-analysis']['type']==options['--no-dynamic-analysis']['type']=='boolean'


def test_optin_preserves_crlf_snapshot_while_default_keeps_old_reader(tmp_path):
    import hashlib
    item=entry(tmp_path)
    raw=b'Procedure Build()\r\nX=Items.Add("Field",Type("FormField"));\r\nEndProcedure\r\n'
    item.bsl_path.write_bytes(raw)
    off=build_form_context(item,tmp_path)
    on=build_form_context(item,tmp_path,include_dynamic_analysis=True)
    assert '\r' not in off.bsl_text
    assert on.bsl_text.encode('utf-8')==raw
    op,=on.dynamic_analysis.index.bound.operations
    assert op.source.snapshot_sha256==hashlib.sha256(raw).hexdigest()
    assert on.bsl_text[op.source.start_offset:op.source.end_offset].startswith('Items.Add')
