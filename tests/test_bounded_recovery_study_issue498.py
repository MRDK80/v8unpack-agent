"""Complete pre-response mock/replay and unchanged initial R11 request checks."""
import json

from scripts import bounded_recovery_study_issue498 as study
from scripts import fact_reference_study_v2_issue497 as historical
from scripts.score_bounded_recovery_issue498 import summarize
from v8unpack_agent.untrusted_transport import decode_block


def mock(body,*,timeout_seconds=None):
    if len(body['messages'])==2:
        observation=json.loads(decode_block(body['messages'][1]['content'].split('Недоверенные данные:\n')[1]).text)
        key='fact_id'
    else:
        observation=json.loads(body['messages'][-1]['content']);key='ref'
    proposal={'scope_id':observation['scope_id'],
        'fact_ids':[f[key] for f in observation['evidence']['facts']]}
    return {'model':body['model'],'message':{'role':'assistant','content':json.dumps(proposal)},
            'eval_count':8,'prompt_eval_count':100,'done':True,'done_reason':'stop'}


def valid_first(body,*,timeout_seconds=None):
    value=mock(body,timeout_seconds=timeout_seconds)
    observation=json.loads(decode_block(body['messages'][1]['content'].split('Недоверенные данные:\n')[1]).text)
    proposal={'scope_id':observation['scope_id'],'fact_ids':[f['ref'] for f in observation['evidence']['facts']]}
    value['message']['content']=json.dumps(proposal)
    return value


def test_complete_mock_matrix_and_all_clock_effect_replay(tmp_path):
    cases=study.ledger.read(study.DATA/'cases.json');p=study.ledger.read(study.DATA/'protocol.json')
    plan=study.plan(cases,p['profiles'],p['seeds']);case_map={c['id']:c for c in cases}
    assert len(plan)==p['rows']==882
    totals={'model':0,'unsupported':0,'correct':0,'recoveries':0}
    records=[]
    for index,row in enumerate(plan):
        case=case_map[row['case']]
        record=study.trial(case,row,tmp_path/f'first{index}',mock)
        records.append(record)
        study.replay(case,row,record,tmp_path/f'again{index}')
        result=record['result']
        totals['model']+=result['model_requested']
        totals['unsupported']+=result['outcome']=='unsupported'
        totals['recoveries']+=result['outcome']=='committed_after_block'
        assert len(result['trace'])<=3
        if record['report_file'] is not None:
            assert study.ledger.engine().equal_report(json.loads(record['report_file']),case['expected_report'],case['task'])
            totals['correct']+=1
    assert totals=={'model':520,'unsupported':336,'correct':546,'recoveries':500}
    scored=summarize(records,cases)
    assert sum(g['live_correct_after_block'] for g in scored['groups'].values())==500
    assert sum(g['namespace_recovery'] for g in scored['groups'].values())==500
    assert sum(g['wrong'] for g in scored['groups'].values())==0
    assert sum(g['false_host_selection_rejects'] for g in scored['groups'].values())==0
    assert scored['groups']['qwen25/exports/json_recovery']['utility_admission']=='GO'
    assert scored['groups']['qwen25/form_fields/json_recovery']['utility_admission']=='NO-GO'


def test_first_request_body_identical_to_r11_json(tmp_path):
    cases=study.ledger.read(study.DATA/'cases.json');profiles=study.ledger.read(study.DATA/'protocol.json')['profiles']
    for index,case in enumerate(cases):
        for profile in profiles:
            row={'profile':profile,'seed':17,'case':case['id'],'variant':'json_recovery'}
            new=study.trial(case,row,tmp_path/f'new{index}{profile}',valid_first)
            old=historical.trial(case,dict(row,variant='json_refs'),tmp_path/f'old{index}{profile}',valid_first)
            assert new['result']['model_requested']==old['result']['model_requested']
            if new['result']['model_requested']:
                assert new['result']['trace'][0]['request']==old['result']['trace'][0]['request']


def test_late_correct_response_clock_decision_replays(tmp_path):
    case=study.ledger.read(study.DATA/'cases.json')[0]
    row={'profile':'qwen25','seed':17,'case':case['id'],'variant':'json_recovery'}
    ticks=iter([0,0,0,600])
    record=study.trial(case,row,tmp_path/'first',valid_first,clock=lambda:next(ticks))
    assert record['result']['stop_reason']=='time_budget' and record['report_file'] is None
    assert record['result']['usage']['generated_tokens']==8
    study.replay(case,row,record,tmp_path/'again')


def test_scoring_distinguishes_valid_host_reject_from_namespace_error(tmp_path):
    case=study.ledger.read(study.DATA/'cases.json')[0]
    row={'profile':'qwen25','seed':17,'case':case['id'],'variant':'json_recovery'}
    record=study.trial(case,row,tmp_path/'first',valid_first)
    assert record['result']['events'][0]['admission_checked']
    # Synthetic host-fault fixture, not a real model outcome or archived run.
    record['result']['events'][0].update(status='blocked',reason='incomplete_or_foreign_selection')
    record['report_file']=None;record['output_files']=[]
    scored=summarize([record],[case])['groups']['qwen25/exports/json_recovery']
    assert scored['false_host_selection_rejects']==1 and scored['utility_admission']=='NO-GO'
    record['result']['events'][0]['proposal']['fact_ids']=['not-an-allowed-ref']
    scored=summarize([record],[case])['groups']['qwen25/exports/json_recovery']
    assert scored['false_host_selection_rejects']==0
