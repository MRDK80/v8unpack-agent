"""Opt-in combination/1: one selector namespace, independent host truth (#502).

Категория: reference host для bounded static reports, не CLI/MCP/OS sandbox.
Входные данные: host immutable BSL/form contract, private producer/consumer output.
Ожидаемый результат: только точный snapshot report после полной selection.
Поведение без данных: импорт не пишет и не вызывает модели.
New protocol, not an alias or edit of frozen R11/R15. Model-facing short IDs
are request-scoped data, not capabilities. Proof hashes remain host-side.
"""
from __future__ import annotations

import copy
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from examples.provenance_chain import ChainConsumer, ChainProducer
from v8unpack_agent.action_validation import ReportContract, make_report_proposal
from v8unpack_agent.form_evidence import make_form_report
from v8unpack_agent.report_provenance import (
    Contract,
    ProvenanceRegistry,
    checked_envelope,
)


class SelectionProjection:
    """Immutable host report plus minimal ONE-namespace model observation."""
    def __init__(self,contract:Contract):
        report=(make_report_proposal(contract)['report'] if isinstance(contract,ReportContract)
                else make_form_report(contract))
        envelope=checked_envelope(contract,report,'projection')
        self._contract=contract
        self._report=copy.deepcopy(envelope['report'])
        operation=envelope['operation']
        values=(self._report['function_names'] if operation=='exported_functions' else
                self._report['attribute_names'] if operation=='declared_form_attributes' else
                [self._report['query']])
        if len(values)>1000:raise ValueError('selection budget exceeded')
        key='value' if operation=='returned_string' else 'name'
        self._view={'schema':'snapshot_selection_context/1','operation':operation,
            'completeness':'complete_selected_operation','value_semantics':'untrusted_data',
            'facts':[{'fact_id':'F'+str(i+1),key:value}for i,value in enumerate(values)]}
        self._ids=[fact['fact_id']for fact in self._view['facts']]

    @property
    def contract(self)->Contract:return self._contract

    @property
    def report(self)->dict[str,Any]:return copy.deepcopy(self._report)

    def observation(self)->dict[str,Any]:return copy.deepcopy(self._view)

    def proposal(self)->dict[str,Any]:return {'target_id':'approved','fact_ids':list(self._ids)}

    def validate(self,proposal:Any)->bool:
        if (type(proposal) is not dict or set(proposal)!={'target_id','fact_ids'}
                or type(proposal['target_id']) is not str or proposal['target_id']!='approved'):
            return False
        ids=proposal['fact_ids']
        return (type(ids) is list and all(type(v) is str for v in ids)
            and len(ids)==len(set(ids)) and set(ids)==set(self._ids))


class SelectionHost(AbstractContextManager['SelectionHost']):
    """Trusted scope outside model; sole dispatcher execute, no raw writer."""
    def __init__(self,projection:SelectionProjection,*,producer_dir:Path,output_dir:Path,request_id:str):
        self.projection=projection
        registry=ProvenanceRegistry()
        with ChainProducer(projection.contract,registry=registry,output_dir=producer_dir,
                           request_id='p-'+request_id) as producer:
            result=producer.execute(projection.report)
            if result['status']!='committed':raise RuntimeError('producer publication failed')
            receipt=producer.receipt()
        self._consumer=ChainConsumer(projection.contract,registry=registry,receipt=receipt,
            producer_request='p-'+request_id,output_dir=output_dir,request_id='c-'+request_id)
        self._terminal=False

    def execute(self,proposal:Any)->dict[str,str]:
        if self._terminal:return {'status':'blocked','reason':'request_terminal'}
        if not self.projection.validate(proposal):
            return {'status':'blocked','reason':'invalid_selection'}
        # This is explicit host materialization AFTER complete exact selection,
        # not repairing a bad model report or trusting supplied source metadata.
        result=self._consumer.execute_calls([{'name':'save_chain_report','arguments':{
            'target_id':'approved','report':self.projection.report}}])
        if result['status']=='committed':self._terminal=True
        return result

    def __exit__(self,*args:object)->None:
        self._terminal=True
        self._consumer.close()
