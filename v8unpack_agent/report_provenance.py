"""Opt-in host provenance for verified facts whose values remain untrusted (#501).

Pure data/validation API. No filesystem, instruction authority or cryptographic
identity. Handles belong to trusted Python host memory, never to model tools.
"""
from __future__ import annotations

import json
from typing import Any

from v8unpack_agent.action_validation import (
    ReportContract,
    ValidationResult,
    make_report_proposal,
    validate_report_action,
)
from v8unpack_agent.form_evidence import (
    FormContract,
    FormValidation,
    analyze_form,
    validate_form_report,
)
from v8unpack_agent.untrusted_transport import encode_block

Contract = ReportContract | FormContract
__all__ = ['ProvenanceRegistry', 'checked_envelope']


def checked_envelope(contract: Contract, report: Any, producer_request: str) -> dict[str, Any]:
    """Re-extract exact report and origins from host bytes, not supplied metadata."""
    if type(producer_request) is not str or not producer_request:
        raise ValueError('host producer request required')
    checked: ValidationResult | FormValidation
    operation: str
    if isinstance(contract, ReportContract):
        action = make_report_proposal(contract)
        action['report'] = report
        checked = validate_report_action(contract, action, current_bytes=contract.snapshot.raw)
        source = {'module_id': contract.entry.module_id, 'source_id': contract.snapshot.source_id,
                  'bytes_sha256': contract.snapshot.bytes_sha256}
        operation = contract.operation
        fields = []
        for index, ref in enumerate(action['evidence']):
            pointer = '/report/query' if operation == 'returned_string' else f'/report/function_names/{index}'
            fields.append({'pointer': pointer, 'artifact': 'bsl',
                'source_range': {'start_line': ref['start_line'], 'end_line': ref['end_line']},
                'fact_id': ref['fact_id']})
        coverage = {'selected_operation': 'complete', 'runtime': 'unknown'}
    elif isinstance(contract, FormContract):
        checked = validate_form_report(contract, report)
        evidence = analyze_form(contract.source)
        source = contract.source.to_dict()
        operation = 'declared_form_attributes'
        fields = [{'pointer': f'/report/attribute_names/{index}', 'artifact': 'elements',
                   'source_pointer': fact.pointer, 'raw_pointer': fact.raw_pointer, 'fact_id': fact.fact_id}
                  for index, fact in enumerate(evidence.facts)]
        coverage = evidence.to_dict()['coverage']
    else:
        raise TypeError('host BSL or form contract required')
    if not checked.allowed or checked.canonical_report is None:
        raise ValueError('report does not match host source')
    return {'schema': 'report_provenance/1', 'data_type': operation + '_report/1',
            'operation': operation, 'correctness': 'snapshot_only',
            'producer_request': producer_request, 'source': source,
            'completeness': 'complete_selected_operation', 'coverage': coverage,
            'value_semantics': 'untrusted_data', 'verification': 'host_static_fact',
            'fields': fields, 'report': json.loads(checked.canonical_report)}


class ProvenanceRegistry:
    """Host-only identity registry; JSON labels/hashes cannot mint a receipt.

    The host calls _published ONLY after its own validated effect succeeds.
    Arbitrary Python/plugin access to this instance is outside the threat model.
    Serializing view() retains descriptive provenance, loses in-memory authority.
    """
    def __init__(self) -> None:
        self._records: dict[object, bytes] = {}

    def _published(self, envelope: dict[str, Any]) -> object:
        handle = object()
        self._records[handle] = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                                          allow_nan=False).encode('utf-8')
        return handle

    def view(self, handle: object) -> dict[str, Any]:
        # Avoid hashing attacker dictionaries/lists, no polymorphic supplied class.
        if type(handle) is not object or handle not in self._records:
            raise ValueError('unknown host receipt')
        return json.loads(self._records[handle])

    def tool_message(self, handle: object) -> dict[str, str]:
        """Fixed data channel, no caller-configurable role/tool description/path."""
        payload = json.dumps(self.view(handle), ensure_ascii=False, sort_keys=True, allow_nan=False)
        return {'role': 'tool', 'content': encode_block(payload, n=1, kind='text')}

    def copy_field(self, handle: object, pointer: str) -> object:
        """Host copy retains exact value/origin; never changes instruction trust."""
        value = self.view(handle)
        if value['verification'] != 'host_static_fact' or 'projection' in value:
            raise ValueError('verified whole report required')
        # Whole report-field copies only, not arbitrary JSON path evaluation.
        mapping = {'/report/' + key: item for key, item in value['report'].items()}
        if type(pointer) is not str or pointer not in mapping:
            raise ValueError('unknown report field')
        value['projection'] = {'pointer': pointer, 'value': mapping[pointer]}
        return self._published(value)

    def summary(self, handle: object, text: str) -> object:
        """Origin retained, summary truth unverified, never a writer credential."""
        value = self.view(handle)
        if type(text) is not str: raise ValueError('summary text required')
        value['verification'] = 'unverified_summary'
        value['completeness'] = 'unknown'
        value['summary'] = text
        return self._published(value)

    def require(self, handle: object, contract: Contract, producer_request: str) -> dict[str, Any]:
        value = self.view(handle)
        if value.get('verification') != 'host_static_fact':
            raise ValueError('unverified derivative cannot authorize effect')
        expected = checked_envelope(contract, value['report'], producer_request)
        projection = value.pop('projection', None)
        if value != expected:
            raise ValueError('foreign producer/source/schema/provenance')
        if projection is not None:
            pointer = projection.get('pointer')
            if (pointer not in {'/report/' + key for key in expected['report']}
                    or projection != {'pointer': pointer, 'value': expected['report'][pointer[len('/report/'):]]}):
                raise ValueError('invalid copied field')
        return expected
