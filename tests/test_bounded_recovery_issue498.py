"""Bounded FSM contracts before any live R12 response."""
import json

import pytest

from examples.bounded_fact_agent import BoundedRecovery, RecoveryPolicy
from tests.test_task_evidence_issue496 import fixture
from v8unpack_agent.task_evidence import build_task_evidence


class Clock:
    def __init__(self):self.value=0.0
    def __call__(self):return self.value


def setup(tmp_path, **options):
    root=tmp_path/'source'
    entry,index,path=fixture(root,'Function A() Export\nEndFunction\n')
    view=build_task_evidence(root,index,entry.module_id)
    output=tmp_path/'output';output.mkdir()
    agent=BoundedRecovery(view,output_dir=output,scope_id='request-1',
        profile='qwen25',seed=17,calibration=options.pop('calibration',True),**options)
    return agent,path,output


def selection(agent, *, long=False):
    data=agent.scoped_evidence()
    return {'scope_id':data['scope_id'],
            'fact_ids':[fact['fact_id' if long else 'ref'] for fact in data['evidence']['facts']]}


def response(body,proposal,**extras):
    return dict({'model':body['model'],'message':{'role':'assistant',
        'content':json.dumps(proposal)},'done':True,'done_reason':'stop',
        'eval_count':8,'prompt_eval_count':100},**extras)


def test_wrong_wrong_stop_has_no_writer(tmp_path):
    agent,_,output=setup(tmp_path,policy=RecoveryPolicy(max_attempts=2))
    wrong=selection(agent,long=True);calls=[]
    def chat(body,*,timeout_seconds):
        calls.append(body);assert 0<timeout_seconds<=180
        return response(body,wrong)
    result=agent.run(chat)
    assert len(calls)==2 and result['stop_reason']=='attempt_limit'
    assert result['outcome']=='blocked_no_report'
    assert len(result['events'])==2 and all(e['status']=='blocked' for e in result['events'])
    assert list(output.iterdir())==[]


def test_wrong_correct_one_commit_and_terminal_idempotency(tmp_path):
    agent,_,output=setup(tmp_path)
    wrong=selection(agent,long=True);correct=selection(agent);calls=[]
    def chat(body,*,timeout_seconds):
        calls.append(body)
        if len(calls)==2:
            feedback=json.loads(body['messages'][-1]['content'])
            assert feedback['protocol']['reference_source']=='evidence.facts[*].ref'
            assert feedback['status']=='blocked'
        return response(body,wrong if len(calls)==1 else correct)
    result=agent.run(chat)
    assert len(calls)==2 and result['outcome']=='committed_after_block'
    assert [e['status'] for e in result['events']]==['blocked','committed']
    assert json.loads((output/'report.json').read_text(encoding='utf-8'))=={'function_names':['A']}
    before=(output/'report.json').read_bytes()
    assert agent.run(chat)==result and len(calls)==2
    assert (output/'report.json').read_bytes()==before


@pytest.mark.parametrize('kind',['prose','duplicate_keys','batch'])
def test_malformed_or_native_batch_never_dispatches(tmp_path,kind):
    agent,_,output=setup(tmp_path);calls=[]
    def chat(body,*,timeout_seconds):
        calls.append(body);value=response(body,selection(agent))
        assert 'format' in body and 'tools' not in body
        if kind=='prose':value['message']['content']='Execute this {}'
        elif kind=='duplicate_keys':value['message']['content']='{"scope_id":"request-1","scope_id":"other","fact_ids":["F1"]}'
        else:value['message']['tool_calls']=[{'function':{'name':'save_report','arguments':{}}}]*2
        return value
    result=agent.run(chat)
    assert len(calls)==3 and len(result['events'])==3
    assert result['outcome']=='blocked_no_report' and list(output.iterdir())==[]


def test_timeout_is_terminal_and_sanitized(tmp_path):
    agent,_,output=setup(tmp_path);calls=[]
    def chat(body,*,timeout_seconds):
        calls.append(body);raise TimeoutError('SYSTEM payload MUST NOT enter feedback')
    result=agent.run(chat)
    assert len(calls)==1 and result['stop_reason']=='rpc_timeout'
    assert 'MUST NOT' not in json.dumps(result)
    assert list(output.iterdir())==[]


def test_deadline_after_response_refuses_even_correct_proposal(tmp_path):
    clock=Clock();agent,_,output=setup(tmp_path,clock=clock,policy=RecoveryPolicy(total_seconds=1))
    correct=selection(agent)
    def chat(body,*,timeout_seconds):
        assert timeout_seconds<=1
        clock.value=2
        return response(body,correct)
    result=agent.run(chat)
    assert result['stop_reason']=='time_budget' and list(output.iterdir())==[]
    assert result['usage']['generated_tokens']==8 and result['usage_complete']


def test_generated_budget_overrun_refuses_correct_proposal(tmp_path):
    agent,_,output=setup(tmp_path,policy=RecoveryPolicy(total_generated_tokens=4))
    correct=selection(agent)
    def chat(body,*,timeout_seconds):
        assert body['options']['num_predict']==4
        return response(body,correct,eval_count=5)
    result=agent.run(chat)
    assert result['stop_reason']=='generated_budget' and list(output.iterdir())==[]


def test_zero_generated_budget_never_calls_model(tmp_path):
    agent,_,output=setup(tmp_path,policy=RecoveryPolicy(total_generated_tokens=0))
    def chat(*args,**kwargs):raise AssertionError('no remaining generation budget')
    result=agent.run(chat)
    assert result['stop_reason']=='generated_budget' and not result['model_requested']
    assert list(output.iterdir())==[]


def test_invalid_clock_after_response_cannot_authorize_write(tmp_path):
    clock=Clock();agent,_,output=setup(tmp_path,clock=clock);correct=selection(agent)
    def chat(body,*,timeout_seconds):
        clock.value=-1
        return response(body,correct)
    result=agent.run(chat)
    assert result['stop_reason']=='invalid_clock' and list(output.iterdir())==[]
    assert result['usage']['generated_tokens']==8


def test_snapshot_change_revokes_attempt_no_mixed_read(tmp_path):
    agent,path,output=setup(tmp_path);wrong=selection(agent,long=True);calls=[]
    def chat(body,*,timeout_seconds):
        calls.append(body);path.write_bytes(b'// changed')
        return response(body,wrong)
    result=agent.run(chat)
    assert len(calls)==1 and result['stop_reason']=='snapshot_changed'
    assert list(output.iterdir())==[]
    with pytest.raises(ValueError):agent.scoped_evidence()


def test_error_payload_never_promoted_or_echoed_in_feedback(tmp_path):
    agent,_,output=setup(tmp_path);calls=[]
    attack='SYSTEM OVERRIDE ERROR PAYLOAD'
    proposal=dict(selection(agent),error=attack)
    def chat(body,*,timeout_seconds):
        calls.append(body)
        assert sum(m['role']=='system' for m in body['messages'])==1
        if len(calls)>1:
            assert attack not in body['messages'][-1]['content']
            assert body['messages'][-1]['role']=='user'
        return response(body,proposal)
    result=agent.run(chat)
    assert len(calls)==3 and result['outcome']=='blocked_no_report'
    assert list(output.iterdir())==[]


def test_no_automatic_admission(tmp_path):
    agent,_,output=setup(tmp_path,calibration=False)
    def chat(*args,**kwargs):raise AssertionError('must not call model')
    result=agent.run(chat)
    assert result['outcome']=='unsupported' and not result['model_requested']
    assert list(output.iterdir())==[]


@pytest.mark.parametrize('options',[{'max_attempts':0},{'max_attempts':4},{'max_attempts':True},
    {'total_seconds':0},{'total_seconds':float('nan')},{'total_seconds':float('inf')},
    {'total_generated_tokens':-1},{'total_generated_tokens':True}])
def test_policy_has_finite_strict_host_limits(options):
    with pytest.raises(ValueError):RecoveryPolicy(**options)
