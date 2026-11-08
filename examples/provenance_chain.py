"""Reference producer/consumer with host-owned provenance and separate grants (#501).

Категория: opt-in импортируемый reference host; не CLI/MCP/OS sandbox.
Входные данные: immutable BSL/form contract, private output и host registry/request.
Ожидаемый результат: точный недоверенный литерал в проверенном snapshot report.
Поведение без данных: импорт ничего не пишет и не вызывает модели.
Only consumer.execute_calls may be registered; all other APIs are trusted host.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from examples.snapshot_publication import SnapshotPublication
from v8unpack_agent.report_provenance import (
    Contract,
    ProvenanceRegistry,
    checked_envelope,
)


class ChainProducer(SnapshotPublication['ChainProducer']):
    """Publish independently checked report, then issue a host-memory receipt."""
    def __init__(self, contract: Contract, *, registry: ProvenanceRegistry,
                 output_dir: Path, request_id: str):
        self._contract = contract
        self._registry = registry
        self._receipt: object | None = None
        super().__init__(output_dir=output_dir, request_id=request_id)

    def execute(self, report: Any) -> dict[str, str]:
        # Host API: dispatcher may expose report proposal only, never this instance.
        with self._lock:
            if self._fd < 0 or self._committed:
                return {'status': 'blocked', 'reason': 'request_terminal'}
            try:
                envelope = checked_envelope(self._contract, report, self.request_id)
            except (TypeError, ValueError):
                return {'status': 'blocked', 'reason': 'report_mismatch'}
            ticket = self._prepare(envelope)
        result = self.commit(ticket)
        if result['status'] == 'committed':
            self._receipt = self._registry._published(envelope)
        return result

    def receipt(self) -> object:
        if self._receipt is None:
            raise ValueError('no committed producer report')
        return self._receipt


class ChainConsumer(SnapshotPublication['ChainConsumer']):
    """Separate host grant and independent validator, not a model trust label.

    Receipt is evidence for a particular host-selected producer, not reusable
    write permission. This consumer request authorizes ONE approved destination.
    A separately authorized new consumer grant may legitimately reuse the data.
    """
    def __init__(self, contract: Contract, *, registry: ProvenanceRegistry, receipt: object,
                 producer_request: str, output_dir: Path, request_id: str):
        # Check before acquiring/opening an output; never import authority from JSON.
        registry.require(receipt, contract, producer_request)
        self._contract = contract
        self._registry = registry
        self._receipt = receipt
        self._producer_request = producer_request
        super().__init__(output_dir=output_dir, request_id=request_id)

    def execute_calls(self, calls: Any) -> dict[str, str]:
        with self._lock:
            self._pending = None
            if self._fd < 0 or self._committed:
                return {'status': 'blocked', 'reason': 'request_terminal'}
            if type(calls) is not list or len(calls) != 1:
                return {'status': 'blocked', 'reason': 'one_call_required'}
            call = calls[0]
            if (type(call) is not dict or set(call) != {'name', 'arguments'}
                    or call['name'] != 'save_chain_report'):
                return {'status': 'blocked', 'reason': 'tool_not_allowed'}
            arguments = call['arguments']
            if (type(arguments) is not dict or set(arguments) != {'target_id', 'report'}
                    or type(arguments['target_id']) is not str or arguments['target_id'] != 'approved'):
                return {'status': 'blocked', 'reason': 'target_not_authorized'}
            try:
                origin = self._registry.require(self._receipt, self._contract, self._producer_request)
                envelope = checked_envelope(self._contract, arguments['report'], self._producer_request)
            except (TypeError, ValueError):
                return {'status': 'blocked', 'reason': 'provenance_or_report_mismatch'}
            if envelope != origin:
                return {'status': 'blocked', 'reason': 'provenance_or_report_mismatch'}
            published = dict(envelope, schema='downstream_snapshot_report/1',
                consumer_request=self.request_id, target_id='approved')
            ticket = self._prepare(published)
        return self.commit(ticket)
