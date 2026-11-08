"""Opt-in snapshot-only reference host (#499); NOT a Python/OS sandbox.

Категория: reference-компонент host, не самостоятельный CLI.
Входные данные: host ReportContract и приватный каталог результата на Linux.
Ожидаемый результат: атомарный snapshot_report/1 либо отказ, без перезаписи.
Поведение без данных: импорт ничего не читает/пишет. Модели выдаётся только
execute_calls, никогда validate/commit/ticket/descriptor или raw writer.
R14 shares publication behavior through a host-internal base.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from examples import snapshot_publication as publication
from v8unpack_agent.action_validation import (
    ReportContract,
    make_report_proposal,
    validate_report_action,
)

UnsupportedPlatform = publication.UnsupportedPlatform
platform_supported = publication.platform_supported
os = publication.os
secrets = publication.secrets
SnapshotPublication = publication.SnapshotPublication


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    ticket: object | None = field(default=None, repr=False)

class SnapshotExecutor(SnapshotPublication['SnapshotExecutor']):
    """Single host-selected BSL snapshot; model dispatcher gets only execute_calls."""

    def __init__(self, contract: ReportContract, *, output_dir: Path):
        if not platform_supported():
            raise UnsupportedPlatform('snapshot executor requires the Linux dirfd adapter')
        if not isinstance(contract, ReportContract) or type(contract.snapshot.raw) is not bytes:
            raise TypeError('host must supply a complete immutable byte contract')
        make_report_proposal(contract)
        self._contract = contract
        super().__init__(output_dir=output_dir)

    @property
    def contract(self) -> ReportContract:
        return self._contract

    @property
    def snapshot_bytes(self) -> bytes:
        raw = self.contract.snapshot.raw
        assert type(raw) is bytes
        return raw

    def proposal(self) -> dict[str, Any]:
        """Deterministic host proposal for demos/tests; NOT an LLM success."""
        return {'request_id': self.request_id, 'action': make_report_proposal(self.contract)}

    def validate(self, calls: Any) -> Admission:
        """Host API only: canonicalize independently, mint an opaque memory ticket."""
        with self._lock:
            self._pending = None
            if self._fd < 0 or self._committed:
                return Admission(False, 'request_terminal')
            if type(calls) is not list or len(calls) != 1:
                return Admission(False, 'one_call_required')
            call = calls[0]
            if (type(call) is not dict or set(call) != {'name', 'arguments'}
                    or call['name'] != 'save_snapshot_report'):
                return Admission(False, 'tool_not_allowed')
            arguments = call['arguments']
            if (type(arguments) is not dict or set(arguments) != {'request_id', 'action'}
                    or type(arguments['request_id']) is not str
                    or arguments['request_id'] != self.request_id):
                return Admission(False, 'request_mismatch')
            checked = validate_report_action(self.contract, arguments['action'],
                                             current_bytes=self.snapshot_bytes)
            if not checked.allowed or checked.canonical_report is None:
                return Admission(False, checked.reason)
            envelope = {'schema': 'snapshot_report/1', 'correctness': 'snapshot_only',
                        'request_id': self.request_id,
                        'scope': {'operation': self.contract.operation,
                                  'module_id': self.contract.entry.module_id,
                                  'source_id': self.contract.snapshot.source_id,
                                  'bytes_sha256': self.contract.snapshot.bytes_sha256,
                                  'output_identity': self._identity},
                        'report': json.loads(checked.canonical_report)}
            ticket = self._prepare(envelope)
            return Admission(True, 'validated_snapshot', ticket)

    def execute_calls(self, calls: Any) -> dict[str, str]:
        """The ONLY operation registered with the model tool dispatcher."""
        decision = self.validate(calls)
        if not decision.allowed:
            return {'status': 'blocked', 'reason': decision.reason}
        return self.commit(decision.ticket)
