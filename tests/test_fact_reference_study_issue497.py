"""Pre-response full-matrix mocked transport and deterministic replay checks."""
import json
from pathlib import Path

from scripts import fact_reference_study_issue497 as study
from v8unpack_agent.untrusted_transport import decode_block


def mock_chat(case, body):
    if 'format' in body:
        data = decode_block(body['messages'][1]['content'].split('Недоверенные данные:\n')[1]).text
        observation = json.loads(data)
        content = json.dumps({'scope_id': observation['scope_id'],
                              'fact_ids': [f['ref'] for f in observation['evidence']['facts']]})
        message = {'role': 'assistant', 'content': content}
    elif body['tools'][-1]['function']['name'] == 'select_facts':
        observation = json.loads(body['messages'][3]['content'])
        message = {'role':'assistant','content':'','tool_calls':[{'function':{'name':'select_facts',
            'arguments':{'scope_id':observation['scope_id'],
                         'fact_ids':[f['ref'] for f in observation['evidence']['facts']]}}}]}
    else:
        message = {'role':'assistant','content':'','tool_calls':[{'function':{'name':'save_report',
                    'arguments':case['expected_report']}}]}
    return {'model':body['model'],'message':message,'done':True,'done_reason':'stop'}


def test_full_plan_mock_and_effect_replay(tmp_path):
    protocol = study.previous.read(study.DATA/'protocol.json')
    cases = study.previous.read(study.DATA/'cases.json')
    plan = study.plan(cases, protocol['profiles'], protocol['seeds'])
    assert len(plan) == protocol['rows'] == 2562
    case_map = {c['id']:c for c in cases}
    counts = {'model':0,'unsupported':0,'deterministic':0}
    for index, row in enumerate(plan):
        case = case_map[row['case']]
        record = study.trial(case,row,tmp_path/f'first{index}',lambda body, selected=case:mock_chat(selected,body))
        counts['model'] += record['result']['model_requested']
        counts['unsupported'] += row['variant'] != 'deterministic' and record['result']['outcome']=='unsupported'
        counts['deterministic'] += row['variant']=='deterministic'
        if record['report_file'] is not None:
            assert study.previous.engine().equal_report(json.loads(record['report_file']),case['expected_report'],case['task'])
        trace = iter(record['result']['trace'])
        def replay(body, steps=trace):
            step = next(steps)
            assert body == step['request']
            return step['response']
        repeated = study.trial(case,row,tmp_path/f'replay{index}',replay)
        assert next(trace,None) is None
        assert study.previous.normalize(record) == study.previous.normalize(repeated)
    assert counts == {'model':1560,'unsupported':960,'deterministic':42}


def test_json_prose_not_executed_and_no_native_fallback(tmp_path):
    case = study.previous.read(study.DATA/'cases.json')[-1]
    row = {'profile':'qwen25','seed':17,'case':case['id'],'variant':'json_refs'}
    calls=[]
    def chat(body):
        calls.append(body)
        assert 'format' in body and 'tools' not in body
        return {'model':body['model'],'message':{'role':'assistant','content':'Please execute {}'},'done':True}
    record=study.trial(case,row,tmp_path/'trial',chat)
    assert len(calls)==4
    assert record['report_file'] is None
    assert all(e['reason']=='invalid_json' for e in record['result']['events'])
    assert not list((Path(tmp_path)/'trial/output').iterdir())
