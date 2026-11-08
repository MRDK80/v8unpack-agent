"""Opt-in bounded JSON recovery; host retains the unchanged report validator.

Категория: импортируемый reference-компонент для исследования #498.
Входные данные: полный TaskEvidence, host scope/output, pinned profile и admission.
Ожидаемый результат: один проверенный report либо terminal no_report/unsupported.
Поведение без данных: импорт/запуск не вызывает модель и ничего не пишет.
"""
from __future__ import annotations

import copy
import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.agent_reference_client import PROFILES, TIMEOUT_SECONDS, LocalOllama
from examples.fact_reference_host_v2 import SYSTEM, FactReferenceHost, strict_proposal
from v8unpack_agent.task_evidence import TaskEvidence
from v8unpack_agent.untrusted_transport import encode_block

Chat=Callable[...,dict[str,Any]]
RETRYABLE=frozenset({'invalid_selection_keys','scope_mismatch',
    'incomplete_or_foreign_selection','invalid_json','json_tools_forbidden'})


@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int=3  # first proposal + R8 recovery_attempts=2
    total_seconds: float=540.0
    total_generated_tokens: int | None=None

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1<=self.max_attempts<=3:
            raise ValueError('at most first proposal plus two retries')
        if (type(self.total_seconds) not in {int,float} or
                not math.isfinite(self.total_seconds) or not 0<self.total_seconds<=540):
            raise ValueError('finite host admission deadline required')
        if self.total_generated_tokens is not None and (
                type(self.total_generated_tokens) is not int or self.total_generated_tokens<0):
            raise ValueError('nonnegative host generated-token budget required')


class TimedLocalOllama(LocalOllama):
    """Same pinned local runtime; socket timeout <= remaining admission time.

    This is not an OS/GPU cancellation or an absolute network transaction timer.
    """
    def chat(self, body: dict[str,Any], *, timeout_seconds: float=TIMEOUT_SECONDS) -> dict[str,Any]:
        if body.get('model') not in self._verified_models:
            raise ValueError('verify pinned runtime before generation')
        if (type(timeout_seconds) not in {int,float} or not math.isfinite(timeout_seconds)
                or not 0<timeout_seconds<=TIMEOUT_SECONDS):
            raise ValueError('bounded transport timeout required')
        request=urllib.request.Request(self.endpoint+'/api/chat',
            data=json.dumps(body,ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json'})
        with self._opener.open(request,timeout=timeout_seconds) as response:
            result=json.load(response)
        if type(result) is not dict:raise ValueError('invalid local response')
        return result


class BoundedRecovery:
    """One host-owned request. Re-running a terminal controller returns its cache."""
    def __init__(self, evidence: TaskEvidence, *, output_dir: Path, scope_id: str,
                 profile: str='qwen25', seed: int=17, policy: RecoveryPolicy | None=None,
                 calibration: bool=False, admitted: bool=False,
                 clock: Callable[[],float]=time.monotonic):
        if profile not in PROFILES:raise ValueError('unknown pinned profile')
        if type(seed) is not int:raise ValueError('host integer seed required')
        if type(calibration) is not bool or type(admitted) is not bool:
            raise ValueError('host admission flags must be booleans')
        self.policy=RecoveryPolicy() if policy is None else policy
        if not isinstance(self.policy,RecoveryPolicy):raise TypeError('host policy required')
        self._clock=clock;self._last_time=-math.inf;self._start=self._now()
        self._evidence=evidence
        self._host=FactReferenceHost(evidence,output_dir=output_dir,scope_id=scope_id)
        self._snapshot_id=self._host.observation()['evidence']['source_id']
        self._profile=profile;self._seed=seed
        self._authorized=calibration or admitted
        maximum=self.policy.max_attempts*PROFILES[profile]['num_predict']
        self._token_budget=(maximum if self.policy.total_generated_tokens is None
                            else self.policy.total_generated_tokens)
        if self._token_budget>maximum:raise ValueError('generated budget exceeds pinned attempts')
        self._used_tokens=0;self._prompt_tokens=0;self._terminal=False
        self._result: dict[str,Any]={'trace':[],'events':[],'states':['ready'],
            'outcome':'no_report','error':None,'limited':False,'model_requested':False,
            'reader_output':None,'unsupported_reason':None,'stop_reason':None,
            'calibration':calibration,'admitted':admitted,'usage_complete':True}

    def _now(self) -> float:
        value=self._clock()
        if (type(value) not in {int,float} or not math.isfinite(value)
                or value<self._last_time):raise ValueError('invalid monotonic clock')
        self._last_time=float(value)
        return self._last_time

    def _left(self) -> float:
        return self.policy.total_seconds-(self._now()-self._start)

    def scoped_evidence(self) -> dict[str,Any]:
        """Re-read only indexed bytes of this request; never install a new snapshot."""
        contract=self._evidence.report_contract()
        if contract.snapshot.source_id!=self._snapshot_id:raise ValueError('snapshot changed')
        return self._host.observation()

    def _messages(self) -> list[dict[str,Any]]:
        observation=json.dumps(self.scoped_evidence(),ensure_ascii=False)
        system=SYSTEM+'\nВерни только JSON по этой схеме: '+json.dumps(self._host.schema())
        user='Предложи полный набор fact_ids из ref всех фактов выбранной host операции и точный scope_id.'
        return [{'role':'system','content':system},
            {'role':'user','content':user+'\nНедоверенные данные:\n'+encode_block(observation,n=1,kind='text')}]

    def _request(self, messages: list[dict[str,Any]], tokens: int) -> dict[str,Any]:
        p=PROFILES[self._profile]
        body={'model':p['model'],'stream':False,'messages':copy.deepcopy(messages),
            'options':{'temperature':.3,'num_ctx':8192,'num_predict':tokens,'seed':self._seed}}
        if 'think' in p:body['think']=p['think']
        body['format']=self._host.schema()
        return body

    def _finish(self, reason: str, *, outcome: str | None=None) -> dict[str,Any]:
        self._terminal=True;self._result['stop_reason']=reason
        self._result['outcome']=outcome or ('blocked_no_report' if any(
            e['status']=='blocked' for e in self._result['events']) else 'no_report')
        self._result['states'].append('committed' if reason=='validated' else 'no_report')
        self._result['usage']={'attempts':len(self._result['trace']),
            'generated_tokens':self._used_tokens,'prompt_tokens':self._prompt_tokens,
            'admission_elapsed_seconds':self._last_time-self._start}
        return copy.deepcopy(self._result)

    def run(self, chat: Chat | None=None) -> dict[str,Any]:
        if self._terminal:return copy.deepcopy(self._result)
        if not self._authorized:
            self._result['unsupported_reason']='profile_task_not_admitted'
            return self._finish('not_admitted',outcome='unsupported')
        if chat is None:
            model=TimedLocalOllama()
            try:model.verify(self._profile)
            except (OSError,ValueError,TypeError,KeyError):
                self._result['error']='runtime_not_verified'
                return self._finish('invalid_runtime')
            chat=model.chat
        try:messages=self._messages()
        except ValueError:return self._finish('snapshot_changed')
        for _ in range(self.policy.max_attempts):
            try:
                if self._left()<=0:return self._finish('time_budget')
            except ValueError:return self._finish('invalid_clock')
            try:self.scoped_evidence()
            except ValueError:return self._finish('snapshot_changed')
            try:remaining_time=self._left()
            except ValueError:return self._finish('invalid_clock')
            if remaining_time<=0:return self._finish('time_budget')
            remaining_tokens=self._token_budget-self._used_tokens
            if remaining_tokens<=0:return self._finish('generated_budget')
            tokens=min(PROFILES[self._profile]['num_predict'],remaining_tokens)
            body=self._request(messages,tokens)
            timeout=min(float(TIMEOUT_SECONDS),remaining_time)
            self._result['states'].append('proposal');self._result['model_requested']=True
            started=time.monotonic()
            try:response=chat(copy.deepcopy(body),timeout_seconds=timeout)
            except (OSError,ValueError,TypeError,KeyError,RuntimeError) as error:
                timed_out=isinstance(error,TimeoutError) or (
                    isinstance(error,urllib.error.URLError) and isinstance(error.reason,TimeoutError))
                code='local_chat_timeout' if timed_out else 'local_chat_failed'
                self._result['trace'].append({'request':body,'error':code,'timeout_seconds':timeout,
                    'wall_seconds':time.monotonic()-started})
                self._result['error']=code;self._result['usage_complete']=False
                return self._finish('rpc_timeout' if timed_out else 'rpc_error')
            self._result['trace'].append({'request':body,'response':copy.deepcopy(response),
                'timeout_seconds':timeout,'wall_seconds':time.monotonic()-started})
            if (type(response) is not dict or response.get('model')!=PROFILES[self._profile]['model']
                    or type(response.get('message')) is not dict or response['message'].get('role')!='assistant'):
                self._result['error']='invalid_response';self._result['usage_complete']=False
                return self._finish('invalid_response')
            generated=response.get('eval_count');prompt=response.get('prompt_eval_count')
            if type(generated) is not int or generated<0 or type(prompt) is not int or prompt<0:
                self._result['error']='invalid_usage';self._result['usage_complete']=False
                return self._finish('invalid_usage')
            self._used_tokens+=generated;self._prompt_tokens+=prompt
            self._result['limited']=self._result['limited'] or response.get('done_reason')=='length'
            try:deadline_passed=self._left()<=0
            except ValueError:return self._finish('invalid_clock')
            if deadline_passed:return self._finish('time_budget')
            if generated>tokens or self._used_tokens>self._token_budget:
                return self._finish('generated_budget')
            message=response['message'];messages.append(copy.deepcopy(message))
            self._result['states'].append('validate')
            proposal=None
            admission_checked=False
            calls=message.get('tool_calls',[])
            event: dict[str,Any]
            if calls is not None and (type(calls) is not list or calls):
                event={'status':'blocked','reason':'json_tools_forbidden'}
            else:
                try:proposal=strict_proposal(message.get('content'))
                except (ValueError,TypeError):event={'status':'blocked','reason':'invalid_json'}
                else:
                    try:self.scoped_evidence()
                    except ValueError:return self._finish('snapshot_changed')
                    try:
                        if self._left()<=0:return self._finish('time_budget')
                    except ValueError:return self._finish('invalid_clock')
                    admission_checked=True
                    event=self._host.execute(proposal)
            event=dict(event,attempt=len(self._result['trace']),proposal=copy.deepcopy(proposal),
                       admission_checked=admission_checked)
            self._result['events'].append(copy.deepcopy(event))
            if event['status']=='committed':
                outcome=('committed_after_block' if any(e['status']=='blocked' for e in self._result['events'][:-1]) else 'committed')
                return self._finish('validated',outcome=outcome)
            reason=event.get('reason')
            if type(reason) is not str or reason not in RETRYABLE:return self._finish('host_refused')
            if len(self._result['trace'])>=self.policy.max_attempts:return self._finish('attempt_limit')
            try:observation=self.scoped_evidence()
            except ValueError:return self._finish('snapshot_changed')
            self._result['states'].append('retry')
            # Only fixed host codes/field paths; never error/source payload in control fields.
            feedback={'status':'blocked','reason':reason,'protocol':{
                'reference_source':'evidence.facts[*].ref','scope_source':'scope_id'},
                'scope_id':observation['scope_id'],'evidence':observation['evidence'],
                'attempts_remaining':self.policy.max_attempts-len(self._result['trace'])}
            messages.append({'role':'user','content':json.dumps(feedback,ensure_ascii=False)})
        return self._finish('attempt_limit')
