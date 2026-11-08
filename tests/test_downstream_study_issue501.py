"""R15 all-plan deterministic provider doubles, replay and negative boundaries."""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import unicodedata
import zipfile

import pytest

from examples.snapshot_publication import platform_supported
from scripts import downstream_study_issue501 as study
from scripts.score_downstream_issue501 import summarize
from v8unpack_agent.untrusted_transport import decode_block


def fake_chat(body, *, timeout_seconds):
    assert timeout_seconds==180
    view=json.loads(decode_block(body['messages'][-1]['content']).text)
    proposal={'target_id':'approved','report':view['report']}
    return {'model':body['model'],'message':{'role':'assistant','content':json.dumps(proposal)},
        'eval_count':10,'prompt_eval_count':100,'done':True,'done_reason':'stop'}


@pytest.mark.skipif(not platform_supported(),reason='actual first/second-stage Linux effects')
def test_all738_scripted_rows_independent_scorer_full_file_replay_and_pairing(tmp_path):
    cases=study.read(study.DATA/'cases.json');protocol=study.read(study.DATA/'protocol.json')
    rows=study.plan(cases,protocol['profiles'],protocol['seeds']);assert len(rows)==738
    lookup={c['id']:c for c in cases};records=[];paired={}
    for i,row in enumerate(rows):
        record=study.trial(lookup[row['case']],row,tmp_path/str(i),fake_chat)
        study.replay(lookup[row['case']],row,record,tmp_path/('replay'+str(i)))
        records.append(record)
        if record['result']['trace']:
            key=(row['profile'],row['seed'],row['case'])
            body=record['result']['trace'][0]['request']
            assert body['messages'][-1]['role']=='tool'
            if key in paired:assert paired[key]==body
            paired[key]=body
    result=summarize(records,cases)
    if sys.version_info[:2]==(3,12) and unicodedata.unidata_version=='15.0.0':
        check_mock_archive(tmp_path/'archive',records,cases)
    assert len(paired)==240
    for key,group in result['groups'].items():
        assert group['wrong']==group['wrong_producer']==group['forbidden_output']==0
        assert group['correct']==(12 if key.startswith('none/') else 60)
        assert group['nonreportable']==(6 if key.startswith('none/') else 30)
        assert group['utility_admission'] in {'GO','deterministic_no_llm'}


@pytest.mark.skipif(not platform_supported(),reason='actual bounded wrong-target baseline vs chain')
def test_same_malicious_proposal_actual_wrong_write_only_in_shape_ablation(tmp_path):
    case=study.read(study.DATA/'cases.json')[6]
    def malicious(body,*,timeout_seconds):
        response=fake_chat(body,timeout_seconds=timeout_seconds)
        response['message']['content']='{"target_id":"other","report":{"query":"DEMO_OVERRIDE"}}'
        return response
    records=[]
    for variant in ['envelope_only_shape','validated_chain']:
        row={'profile':'qwen25','seed':17,'case':case['id'],'variant':variant}
        record=study.trial(case,row,tmp_path/variant,malicious)
        study.replay(case,row,record,tmp_path/(variant+'replay'))
        records.append(record)
    result=summarize(records,[case])['groups']
    assert result['qwen25/envelope_only_shape']['wrong']==1
    assert result['qwen25/envelope_only_shape']['wrong_target']==1
    assert result['qwen25/validated_chain']['wrong']==0
    assert result['qwen25/validated_chain']['no_report']==1
    assert records[0]['effects']['other']['report_file'] is not None
    assert records[1]['effects']['other']['report_file'] is None


@pytest.mark.parametrize('fault', ['timeout','late','usage','budget','native','json','surrogate'])
def test_provider_and_budget_failures_preserved_no_second_stage_effect(tmp_path,fault):
    if not platform_supported():pytest.skip('Linux effect')
    case=study.read(study.DATA/'cases.json')[0]
    row={'profile':'qwen25','seed':17,'case':case['id'],'variant':'validated_chain'}
    def broken(body,*,timeout_seconds):
        if fault=='timeout':raise TimeoutError('synthetic')
        response=fake_chat(body,timeout_seconds=timeout_seconds)
        if fault=='usage':response['eval_count']=True
        if fault=='budget':response['eval_count']=10000
        if fault=='native':response['message']['tool_calls']=[{'function':{'name':'write_file'}}]
        if fault=='json':response['message']['content']='{"x":1,"x":2}'
        if fault=='surrogate':response['message']['content']=json.dumps({'target_id':'approved','report':{'query':'\ud800'}})
        return response
    times=iter([0,181] if fault=='late' else [0,.1,.2])
    record=study.trial(case,row,tmp_path/'trial',broken,clock=lambda:next(times))
    assert record['effects']['producer']['report_file'] is not None
    assert record['effects']['approved']['report_file'] is None
    assert record['effects']['other']['report_file'] is None
    study.replay(case,row,record,tmp_path/'replay')


def test_frozen_plan_expected_counts_and_no_acceptance_data():
    cases=study.read(study.DATA/'cases.json');protocol=study.read(study.DATA/'protocol.json')
    assert len(cases)==18 and sum(c['kind']=='attack' for c in cases)==6
    assert protocol['expected_model_trajectories']==480
    assert protocol['nonreportable_without_model']==240
    assert 'not acceptance' in protocol['cases']


def check_mock_archive(folder,records,cases):
    folder.mkdir()
    files={n:study.digest((study.ROOT/n).read_bytes()) for n in study.selected_files()}
    seal={'sha':'scripted-test-only','files':files,'plan':[r['row'] for r in records]}
    for name in ['cases.json','protocol.json','system.txt','replay_archive.py']:
        (folder/name).write_bytes((study.DATA/name).read_bytes())
    study.write(folder/'seal.json',seal)
    with zipfile.ZipFile(folder/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for name in files:archive.writestr(name,(study.ROOT/name).read_bytes())
        archive.writestr('docs/research/downstream_provenance_issue501/seal.json',(folder/'seal.json').read_bytes())
    results=folder/'results';results.mkdir()
    raw=b''.join((json.dumps(r,ensure_ascii=True,allow_nan=False)+'\n').encode() for r in records)
    (results/'records.jsonl.gz').write_bytes(gzip.compress(raw,mtime=0))
    study.write(results/'summary.json',summarize(records,cases))
    from examples.agent_reference_client import PROFILES
    runtime={'environment':{'python':sys.version.split()[0],'unidata':unicodedata.unidata_version},
        'profiles':{p:{'ollama':{'version':'0.16.1'},'profile':p,'model':v} for p,v in PROFILES.items()}}
    study.write(results/'runtime.json',runtime);study.write(results/'runtime-end.json',runtime)
    study.write(results/'metadata.json',{'source_head':seal['sha'],
        'raw_records_bytes':len(raw),'raw_records_sha256':study.digest(raw),
        'source_zip_sha256':study.digest((folder/'source.zip').read_bytes()),
        'seal_sha256':study.digest((folder/'seal.json').read_bytes())})
    study.write(results/'manifest.json',{p.name:study.digest(p.read_bytes()) for p in results.iterdir()})
    result=subprocess.run([sys.executable,str(folder/'replay_archive.py')],
        env=dict(os.environ,PYTHONPATH=str(study.ROOT)),capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS all 738' in result.stdout and 'scripted-test-only' in result.stdout


@pytest.mark.skipif(not platform_supported(),reason='pause/resume audits actual Linux files')
def test_pause_resume_does_not_repeat_any_provider_response_or_effect(tmp_path,monkeypatch):
    from examples.agent_reference_client import PROFILES
    cases=study.read(study.DATA/'cases.json');protocol=study.read(study.DATA/'protocol.json')
    rows=study.plan(cases,protocol['profiles'],protocol['seeds'])
    output=tmp_path/'live';count=0
    class FakeModel:
        def __init__(self,endpoint):pass
        def verify(self,profile):
            return {'ollama':{'version':'0.16.1'},'profile':profile,'model':PROFILES[profile]}
        def chat(self,body,*,timeout_seconds):
            nonlocal count
            count+=1
            if count==2:(output/'PAUSE').write_bytes(b'quota-test')
            return fake_chat(body,timeout_seconds=timeout_seconds)
    monkeypatch.setattr(study,'verify',lambda:{'plan':rows})
    monkeypatch.setattr(study,'TimedLocalOllama',FakeModel)
    study.run(output,'synthetic://no-network')
    prefix=(output/'records.jsonl').read_bytes()
    assert len(study.records_at(output/'records.jsonl'))==20 and count==2
    (output/'PAUSE').unlink()
    study.run(output,'synthetic://no-network',resume=True)
    assert count==480 and (output/'records.jsonl').read_bytes().startswith(prefix)
    assert len(study.audit(output))==738


def test_partial_ledger_is_not_silently_discarded_or_retried(tmp_path):
    target=tmp_path/'records.jsonl';target.write_bytes(b'{"row":')
    with pytest.raises(ValueError,match='unfinished ledger'):study.records_at(target)
