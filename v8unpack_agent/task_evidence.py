"""Opt-in compact single-task facts with host-scoped source access (#496)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from v8unpack_agent.action_validation import ReportContract, create_report_contract
from v8unpack_agent.evidence_context import (
    EvidenceContext,
    Operation,
    build_module_evidence,
    evidence_fragment,
)
from v8unpack_agent.modules import ModuleEntry, ModuleIndex

__all__ = ['TASK_EVIDENCE_SCHEMA', 'TaskEvidence', 'build_task_evidence']
TASK_EVIDENCE_SCHEMA = 'task_evidence/1'


@dataclass(frozen=True)
class TaskEvidence:
    """Trusted host object; serialized facts and literal strings remain untrusted."""
    root: Path = field(repr=False)
    entry: ModuleEntry | None = field(repr=False)
    context: EvidenceContext = field(repr=False)
    max_fact_chars: int = 20000

    def __post_init__(self) -> None:
        if not isinstance(self.context, EvidenceContext) or len(self.context.modules) != 1:
            raise ValueError('one host-selected module required')
        if type(self.max_fact_chars) is not int or self.max_fact_chars < 0:
            raise ValueError('max_fact_chars must be a nonnegative integer')

    def to_dict(self) -> dict[str, Any]:
        full = self.context.to_dict()
        module = full['modules'][0]
        source = module['source'] or {}
        status = module['status']
        if status == 'complete' and full['status'] != 'complete':
            status = 'incomplete'
        result = {'schema': TASK_EVIDENCE_SCHEMA, 'operation': self.context.operation,
                'scope_kind': 'selected_bsl_module', 'status': status,
                'reason': module['reason'], 'value_semantics': 'untrusted_data',
                'source_id': source.get('source_id'),
                'bytes_sha256': source.get('bytes_sha256'),
                'text_sha256': source.get('text_sha256'),
                'normalization': source.get('normalization'),
                'extractor_version': module['extractor_version'],
                'facts': module['facts'], 'facts_total': module['facts_total'],
                'facts_omitted': module['facts_omitted']}
        if result['facts'] and len(json.dumps(result['facts'], ensure_ascii=False)) > self.max_fact_chars:
            result.update(status='truncated', reason='fact_char_budget', facts=[],
                          facts_omitted=result['facts_total'])
        return result

    @property
    def can_report(self) -> bool:
        data = self.to_dict()
        return data['status'] == 'complete' and data['bytes_sha256'] is not None

    def report_contract(self) -> ReportContract:
        """Re-read indexed bytes; incomplete view cannot authorize a full report."""
        if not self.can_report or self.entry is None:
            raise ValueError('task evidence incomplete')
        module = self.context.modules[0]
        contract = create_report_contract(self.root, ModuleIndex.from_entries([self.entry]),
            self.entry.module_id, operation=self.context.operation,
            declaration_name=module.declaration_name)
        if contract.snapshot.source_id != self.to_dict()['source_id']:
            raise ValueError('task snapshot changed')
        return contract

    def read_fact(self, fact_id: str, *, source_id: str,
                  start_line: int | None = None, end_line: int | None = None,
                  max_chars: int = 10000) -> dict[str, Any]:
        """No filesystem I/O: only ranges of included facts in the pinned snapshot."""
        data = self.to_dict()
        source = self.context.modules[0].facts.source
        if source is None or not isinstance(source_id, str) or source_id != data['source_id']:
            raise ValueError('snapshot mismatch')
        fact = next((item for item in data['facts'] if item['fact_id'] == fact_id), None)
        if fact is None:
            raise ValueError('unknown fact')
        if (start_line is None) != (end_line is None):
            raise ValueError('both range endpoints required')
        start = fact['start_line'] if start_line is None else start_line
        end = fact['end_line'] if end_line is None else end_line
        if (type(start) is not int or type(end) is not int or
                not fact['start_line'] <= start <= end <= fact['end_line']):
            raise ValueError('range outside selected fact')
        fragment = evidence_fragment(source, source_id=source_id, start_line=start,
                                     end_line=end, max_chars=max_chars)
        fragment['schema'] = 'task_evidence_fragment/1'
        fragment['fact_id'] = fact_id
        fragment['source'] = {key: data[key] for key in
                              ('source_id', 'text_sha256', 'bytes_sha256', 'normalization')}
        return fragment


def build_task_evidence(root: Path, index: ModuleIndex, module_id: str, *,
                        operation: Operation = 'exported_functions',
                        declaration_name: str | None = None, max_facts: int = 1000,
                        max_chars: int = 2_000_000, max_fact_chars: int = 20000) -> TaskEvidence:
    """Use the existing bounded extractor; no new grammar or default projection."""
    context = build_module_evidence(root, index, module_id, operation=operation,
        declaration_name=declaration_name, max_facts=max_facts, max_chars=max_chars)
    return TaskEvidence(root, index.get(module_id), context, max_fact_chars)
