"""New harness request/clock/effect/recovery and decoder contracts, no models."""
from __future__ import annotations

import json

import pytest

from examples.agent_reference_client import PROFILES
from scripts.unicode_cases_issue519 import (
    ATTACK,
    attack_material,
    build_cases,
    make_plan,
)
from scripts.unicode_study_issue519 import (
    replay_record,
    summarize_phase,
    trial_record,
    wire_row,
)


def response(row, content, **extra):
    return {'model': PROFILES[row['profile']]['model'], 'message': {'role': 'assistant', 'content': content},
            'eval_count': 20, 'prompt_eval_count': 100, 'done_reason': 'stop', **extra}


@pytest.mark.parametrize('variant', ['legacy_guard','framed_guard','candidate_guard'])
def test_unicode_recovery_records_real_effects_and_exact_offline_replay(tmp_path, variant):
    case = next(c for c in build_cases()if c['task']=='query_text'and c['kind']=='attack')
    row = next(r for r in make_plan('unicode')if r['case']==case['id']and r['variant']==variant)
    answers = iter([{'target_id':'other','fact_ids':['F9']}, {'target_id':'approved','fact_ids':['F1']}])
    def chat(body, *, timeout_seconds):
        assert body['messages'][0]['role']=='system' and timeout_seconds==180
        return response(row,json.dumps(next(answers)))
    record = trial_record('unicode',case,row,tmp_path/'original',chat)
    assert record['result']['recovered']
    assert record['effects']['consumer']['report_file'] is not None
    assert record['protocol_row']==row and 'protocol_arm'not in record['row']
    assert record['boundary'][0]['event']=='request'
    assert len(record['boundary'])==4
    replay_record('unicode',case,row,record,tmp_path/'replay')
    summary=summarize_phase('unicode',[record],[case])
    assert summary['proposal_target_hits']==1 and summary['wrong_effects']==0


def test_protocol_arm_is_not_a_new_host_authority_namespace():
    rows=[r for r in make_plan('unicode')if r['case']==make_plan('unicode')[0]['case']and r['seed']==17 and r['profile']=='qwen25']
    paired=[{k:v for k,v in wire_row(r).items()if k!='variant'}for r in rows]
    assert len(rows)==3 and paired[0]==paired[1]==paired[2]


def test_decode_exact_recovery_is_not_a_file_effect(tmp_path):
    row=make_plan('decode')[0];case=attack_material()[row['case']]
    def chat(body,*,timeout_seconds):
        assert body['format']['required']==['decoded']
        assert 'tools'not in body
        return response(row,json.dumps({'decoded':ATTACK}))
    record=trial_record('decode',case,row,tmp_path/'original',chat)
    assert record['result']['exact_decoded'] and record['effects']=={}
    replay_record('decode',case,row,record,tmp_path/'replay')
    bad=json.loads(json.dumps(record));bad['result']['exact_decoded']=False
    with pytest.raises(ValueError):replay_record('decode',case,row,bad,tmp_path/'tampered')


@pytest.mark.parametrize('mode',['wrong','limit','usage','tools','timeout'])
def test_probe_errors_and_nonanswers_remain_visible(tmp_path,mode):
    row=make_plan('decode')[0];case=attack_material()[row['case']]
    def chat(body,*,timeout_seconds):
        if mode=='timeout':raise TimeoutError('fixture')
        r=response(row,json.dumps({'decoded':ATTACK if mode!='wrong'else'WRONG'}))
        if mode=='limit':r['done_reason']='length'
        if mode=='usage':r['eval_count']=None
        if mode=='tools':r['message']['tool_calls']=[{'function':{'name':'other'}}]
        return r
    record=trial_record('decode',case,row,tmp_path/'original',chat)
    assert not record['result']['exact_decoded'] and len(record['boundary'])==2
    replay_record('decode',case,row,record,tmp_path/'replay')
    summary=summarize_phase('decode',[record],[case])
    assert summary['rows']==1 and summary['exact_decoded']==0 and summary['model_requests']==1


@pytest.mark.parametrize('kind',['unsupported','timeout','recovery'])
def test_original_checkpoint_audit_and_private_error_commitment(tmp_path,monkeypatch,kind):
    from scripts import unicode_study_issue519 as study
    cases=build_cases()
    case=next(c for c in cases if c['task']=='query_text'and c['kind']==('unsupported'if kind=='unsupported'else'attack'))
    row=next(r for r in make_plan('unicode')if r['case']==case['id'])
    output=tmp_path/'original';folder=output/'artifacts'/'00000'
    def chat(body,*,timeout_seconds):
        if kind=='timeout':raise TimeoutError('private synthetic exception')
        return response(row,json.dumps({'target_id':'approved','fact_ids':['F1']}))
    record=trial_record('unicode',case,row,folder,chat)
    study.append(output/'records.jsonl',record)
    data=tmp_path/'data';data.mkdir();study.write(data/'plan.json',{'unicode':[row]});study.write(data/'cases.json',[case])
    monkeypatch.setattr(study,'DATA',data);monkeypatch.setattr(study,'verify',lambda:None)
    study.audit_original('unicode',output)
    if kind=='timeout':
        (folder/'provider-error-private.jsonl').write_text('{}\n',encoding='utf-8')
        with pytest.raises(ValueError,match='commitment'):study.audit_original('unicode',output)
    else:
        (folder/'forbidden.txt').write_text('extra',encoding='utf-8')
        with pytest.raises(ValueError,match='artifact'):study.audit_original('unicode',output)


def test_prefix_audit_never_accepts_missing_middle_or_duplicate(tmp_path,monkeypatch):
    from scripts import unicode_study_issue519 as study
    rows=make_plan('decode')[:2];case=attack_material()[rows[0]['case']]
    record=trial_record('decode',case,rows[0],tmp_path/'original',
        lambda body,timeout_seconds:response(rows[0],json.dumps({'decoded':ATTACK})))
    data=tmp_path/'data';data.mkdir();study.write(data/'plan.json',{'decode':rows});study.write(data/'probe-material.json',attack_material())
    monkeypatch.setattr(study,'DATA',data);monkeypatch.setattr(study,'verify',lambda:None)
    study.audit_records('decode',[record],complete=False)
    with pytest.raises(ValueError,match='plan'):study.audit_records('decode',[record])
    with pytest.raises(ValueError,match='plan'):study.audit_records('decode',[record,record],complete=False)
    ledger=tmp_path/'broken.jsonl';ledger.write_bytes(b'{"unfinished":')
    with pytest.raises(ValueError,match='unfinished'):study.records_at(ledger)


def test_sealed_original_archive_integrity_and_tampering(tmp_path,monkeypatch):
    """Small fake-model archive exercises the same audit chain before live answers."""
    import importlib.util
    import shutil
    import zipfile

    from scripts import unicode_study_issue519 as study
    source=study.ROOT;root=tmp_path/'snapshot';data=root/'docs/research/unicode_study_issue519'
    data.mkdir(parents=True)
    for name in study.code_files():
        if name.startswith('docs/research/unicode_study_issue519/')and name.endswith('.json'):continue
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target)
    case=next(c for c in build_cases()if c['task']=='query_text'and c['kind']=='attack')
    rows=[next(r for r in make_plan('unicode')if r['case']==case['id'])]
    runtime={'environment':{'unidata':'15.0.0'},'profiles':{'qwen25':{
        'profile':'qwen25','model':PROFILES['qwen25'],'ollama':{'version':'0.16.1'}}}}
    study.write(data/'cases.json',[case]);study.write(data/'probe-material.json',attack_material())
    study.write(data/'plan.json',{'unicode':rows});study.write(data/'runtime-preregistered.json',runtime)
    study.write(data/'config.json',{'profiles':{'qwen25':PROFILES['qwen25']},'server_version':'0.16.1','rows':{'unicode':1}})
    monkeypatch.setattr(study,'ROOT',root);monkeypatch.setattr(study,'DATA',data)
    files={n:study.sha((root/n).read_bytes())for n in study.code_files()}
    study.write(data/'code-seal.json',{'sha':'synthetic-test-only','files':files})
    with zipfile.ZipFile(data/'source.zip','w')as z:
        for n in [*files,'docs/research/unicode_study_issue519/code-seal.json']:z.write(root/n,n)
    study.write(data/'seal.json',{'code_sha':'synthetic-test-only',
        'code_seal_sha256':study.sha((data/'code-seal.json').read_bytes()),'source_zip_sha256':study.sha((data/'source.zip').read_bytes()),
        'input_sha256':{n:study.sha((data/n).read_bytes())for n in ['cases.json','probe-material.json','plan.json','runtime-preregistered.json','config.json']}})
    output=tmp_path/'original'
    record=study.trial_record('unicode',case,rows[0],output/'artifacts'/'00000',
        lambda body,timeout_seconds:response(rows[0],json.dumps({'target_id':'approved','fact_ids':['F1']})))
    study.append(output/'records.jsonl',record)
    study.write(output/'runtime.json',runtime);study.write(output/'runtime-end.json',runtime)
    study.archive('unicode',output)
    spec=importlib.util.spec_from_file_location('unicode519archive',data/'replay_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    actual,_,_=module.verify_archive(data,'unicode');assert actual==[record]
    (data/'results/unicode/records.jsonl.gz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='archive bytes'):module.verify_archive(data,'unicode')
