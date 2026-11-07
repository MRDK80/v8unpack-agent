"""Host-side validation of two bounded report operations before effect (#483).

No filesystem writes, model calls, arbitrary patch approval or user authorization.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from v8unpack_agent.bsl_facts import (
    SourceSnapshot,
    analyze_indexed_module,
    analyze_module,
)
from v8unpack_agent.evidence_context import EvidenceModule, Operation
from v8unpack_agent.modules import ModuleEntry, ModuleIndex

__all__ = ['ReportContract', 'ValidationResult', 'create_report_contract',
           'make_report_proposal', 'validate_report_action']


@dataclass(frozen=True)
class ReportContract:
    """Created/authorized by host outside model context. Hashes are not secrets."""
    entry: ModuleEntry
    snapshot: SourceSnapshot = field(repr=False)
    operation: Operation = 'exported_functions'
    declaration_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entry, ModuleEntry) or not isinstance(self.snapshot, SourceSnapshot):
            raise TypeError('contract requires indexed entry and host snapshot')
        if self.entry.module_id != self.snapshot.module_id or self.entry.relative_path != self.snapshot.relative_path:
            raise ValueError('contract source mismatch')
        if self.snapshot.raw is None or self.entry.sha256 is None or self.entry.sha256 != self.snapshot.bytes_sha256:
            raise ValueError('contract requires indexed bytes')
        # Validate enum/target options without claiming a report is supported.
        EvidenceModule(analyze_module(self.entry, self.snapshot.text, bsl_bytes=self.snapshot.raw),
                       self.operation, self.declaration_name)


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    reason: str
    canonical_report: str | None = None


def create_report_contract(root: Path, index: ModuleIndex, module_id: str, *,
                           operation: Operation = 'exported_functions',
                           declaration_name: str | None = None) -> ReportContract:
    """Bind host-selected indexed bytes. Unsupported tasks cannot create a grant."""
    facts = analyze_indexed_module(root, index, module_id)
    entry = index.get(module_id)
    if entry is None or facts.status != 'complete' or facts.source is None:
        raise ValueError('contract source not complete')
    contract = ReportContract(entry, facts.source, operation, declaration_name)
    if _expected(contract) is None:
        raise ValueError('contract operation unsupported')
    return contract


def _expected(contract: ReportContract) -> dict[str, Any] | None:
    # Re-extract from immutable host source; never accept supplied facts as truth.
    f = analyze_module(contract.entry, contract.snapshot.text, bsl_bytes=contract.snapshot.raw)
    if f.status != 'complete' or f.source is None:
        return None
    projection = EvidenceModule(f, contract.operation, contract.declaration_name,
                               max_facts=len(f.declarations)).to_dict()
    if projection['status'] != 'complete':
        return None
    facts = projection['facts']
    if contract.operation == 'exported_functions':
        report = {'function_names': [d['name'] for d in facts]}
    else:
        if len(facts) != 1:
            return None
        report = {'query': facts[0]['value']}
    return {'operation': contract.operation, 'module_id': contract.entry.module_id,
            'source_id': contract.snapshot.source_id,
            'evidence': [{'fact_id': d['fact_id'], 'start_line': d['start_line'],
                          'end_line': d['end_line']} for d in facts], 'report': report}


def make_report_proposal(contract: ReportContract) -> dict[str, Any]:
    """Explicit deterministic proposal, without an LLM; still requires validation.

    Useful to host clients, not a tool granting the model authority.
    """
    if not isinstance(contract, ReportContract):
        raise TypeError('contract must be ReportContract')
    proposal = _expected(contract)
    if proposal is None:
        raise ValueError('contract operation unsupported')
    return proposal


def validate_report_action(contract: ReportContract, arguments: Any, *,
                           current_bytes: bytes | None) -> ValidationResult:
    """Fail closed. current_bytes must be read by host, never from model args.

    This checks one instant/source version; the executor must ensure exclusive
    ownership/immutability through commit for a current-file guarantee.
    """
    if not isinstance(contract, ReportContract):
        raise TypeError('contract must be ReportContract')
    def deny(reason: str) -> ValidationResult:
        return ValidationResult(False, reason)
    if not isinstance(current_bytes, bytes):
        return deny('current_source_unavailable')
    if hashlib.sha256(current_bytes).hexdigest() != contract.snapshot.bytes_sha256:
        return deny('stale_source')
    expected = _expected(contract)
    if expected is None:
        return deny('unsupported_or_incomplete_source')
    if type(arguments) is not dict or set(arguments) != set(expected):
        return deny('invalid_argument_keys')
    for key in ('operation', 'module_id', 'source_id'):
        if type(arguments[key]) is not str or arguments[key] != expected[key]:
            return deny('task_scope_mismatch')
    evidence = arguments['evidence']
    if type(evidence) is not list:
        return deny('invalid_evidence')
    if len(evidence) != len(expected['evidence']):
        return deny('evidence_mismatch')
    refs: list[tuple[str, int, int]] = []
    for ref in evidence:
        if type(ref) is not dict or set(ref) != {'fact_id', 'start_line', 'end_line'}:
            return deny('invalid_evidence')
        if type(ref['fact_id']) is not str or type(ref['start_line']) is not int or type(ref['end_line']) is not int:
            return deny('invalid_evidence')
        refs.append((ref['fact_id'], ref['start_line'], ref['end_line']))
    expected_refs = [(r['fact_id'], r['start_line'], r['end_line']) for r in expected['evidence']]
    if len(refs) != len(set(refs)) or set(refs) != set(expected_refs):
        return deny('evidence_mismatch')
    report = arguments['report']
    if type(report) is not dict or set(report) != set(expected['report']):
        return deny('invalid_report_keys')
    if contract.operation == 'exported_functions':
        names = report['function_names']
        if type(names) is not list or any(type(n) is not str for n in names):
            return deny('invalid_report_types')
        if len(names) != len(set(names)) or set(names) != set(expected['report']['function_names']):
            return deny('report_mismatch')
    elif type(report['query']) is not str or report['query'] != expected['report']['query']:
        return deny('report_mismatch')
    # Produce a fresh canonical serialization, never a mutable model object.
    return ValidationResult(True, 'validated', json.dumps(expected['report'], ensure_ascii=False,
                                                        sort_keys=True, allow_nan=False) + '\n')
