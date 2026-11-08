"""Opt-in static managed-form report host (#500), snapshot-only, Linux effect.

Категория: reference-компонент доверенного host, не CLI/MCP/sandbox.
Входные данные: host FormContract, приватный output и host request identity.
Ожидаемый результат: проверенный form_snapshot_report/1 либо отказ до публикации.
Поведение без данных: импорт не читает/пишет и не вызывает модель.
Регистрировать только execute_calls; модель не получает ticket/commit/raw writer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from examples.snapshot_publication import (
    SnapshotPublication,
    UnsupportedPlatform,
    platform_supported,
)
from examples.snapshot_report_executor import Admission
from v8unpack_agent.form_evidence import (
    OPERATION,
    FormContract,
    analyze_form,
    make_form_report,
    validate_form_report,
)


class FormExecutor(SnapshotPublication['FormExecutor']):
    """One host-bound form request; reply has no path/operation/source authority."""

    def __init__(self, contract: FormContract, *, output_dir: Path,
                 request_id: str | None = None):
        if not platform_supported():
            raise UnsupportedPlatform('form publication requires the Linux dirfd adapter')
        if not isinstance(contract, FormContract):
            raise TypeError('host FormContract required')
        make_form_report(contract)
        self._contract = contract
        # Optional ID is chosen by host, e.g. a pinned experiment row. Not a secret
        # or capability; tickets remain memory objects owned by this instance.
        super().__init__(output_dir=output_dir, request_id=request_id)

    @property
    def contract(self) -> FormContract:
        return self._contract

    def proposal(self) -> dict[str, Any]:
        """Deterministic host demo. Actual model proposes only the report field."""
        return {'request_id': self.request_id, 'report': make_form_report(self.contract)}

    def validate(self, calls: Any) -> Admission:
        with self._lock:
            self._pending = None
            if self._fd < 0 or self._committed:
                return Admission(False, 'request_terminal')
            if type(calls) is not list or len(calls) != 1:
                return Admission(False, 'one_call_required')
            call = calls[0]
            if (type(call) is not dict or set(call) != {'name', 'arguments'}
                    or call['name'] != 'save_form_report'):
                return Admission(False, 'tool_not_allowed')
            arguments = call['arguments']
            if (type(arguments) is not dict or set(arguments) != {'request_id', 'report'}
                    or type(arguments['request_id']) is not str
                    or arguments['request_id'] != self.request_id):
                return Admission(False, 'request_mismatch')
            checked = validate_form_report(self.contract, arguments['report'])
            if not checked.allowed or checked.canonical_report is None:
                return Admission(False, checked.reason)
            evidence = analyze_form(self.contract.source)
            envelope = {'schema': 'form_snapshot_report/1', 'correctness': 'snapshot_only',
                        'request_id': self.request_id, 'operation': OPERATION,
                        'source': self.contract.source.to_dict(),
                        'coverage': evidence.to_dict()['coverage'],
                        'evidence': [f.to_dict() for f in evidence.facts],
                        'report': json.loads(checked.canonical_report)}
            ticket = self._prepare(envelope)
            return Admission(True, 'validated_static_snapshot', ticket)

    def execute_calls(self, calls: Any) -> dict[str, str]:
        """ONLY model dispatcher; host binds its request to an associated RPC reply."""
        decision = self.validate(calls)
        if not decision.allowed:
            return {'status': 'blocked', 'reason': decision.reason}
        return self.commit(decision.ticket)
