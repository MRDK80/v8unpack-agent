"""Reference host: expose ONLY execute_calls to the model tool dispatcher (#483).

Категория: reference-компонент host, импортируется вызывающим клиентом.
Входные данные: разрешённый host ReportContract, корень снимка и отдельный output_dir.
Ожидаемый результат: только проверенный report.json либо отдельный отказ до записи.
Поведение без данных: импорт/запуск файла ничего не читает и не записывает;
данные и разрешение передаёт host при создании ReportExecutor.

Host authorizes a dedicated output directory and owns the source workspace
exclusively through commit. This is not an OS sandbox against other processes.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from v8unpack_agent.action_validation import ReportContract, validate_report_action
from v8unpack_agent.bsl_facts import analyze_indexed_module
from v8unpack_agent.modules import ModuleIndex


class ReportExecutor:
    """One host-selected report.json, no model path, shell or network tools."""

    def __init__(self, contract: ReportContract, *, root: Path, output_dir: Path):
        self._contract = contract
        self._root = Path(root).resolve(strict=True)
        self._output = Path(output_dir).resolve(strict=True)
        if (not self._output.is_dir() or self._output.is_relative_to(self._root) or
                self._root.is_relative_to(self._output)):
            raise ValueError('host must provide a separate dedicated output directory')
        self._lock = threading.Lock()
        self._committed = False
        self._recovered = False
        self._blocked = 0
        self._events: list[dict[str, Any]] = []

    @property
    def outcome(self) -> str:
        if self._committed:
            return 'recovered_correct' if self._recovered else 'correct_write'
        return 'blocked_no_report' if self._blocked else 'no_report'

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(e) for e in self._events)

    def _source(self) -> bytes | None:
        # Reuse indexed path/symlink/budget checks, not a path proposed by model.
        index = ModuleIndex.from_entries([self._contract.entry])
        facts = analyze_indexed_module(self._root, index, self._contract.entry.module_id)
        return facts.source.raw if facts.status == 'complete' and facts.source is not None else None

    def _deny(self, reason: str) -> dict[str, Any]:
        self._blocked += 1
        event = {'status': 'blocked', 'reason': reason}
        self._events.append(event)
        return dict(event)

    def execute_calls(self, calls: Any) -> dict[str, Any]:
        """All calls from ONE model response arrive together; reject multi-call batches.

        Input is the host-decoded list [{name, arguments}]. No direct writer is
        registered alongside this wrapper in the supported reference client.
        """
        with self._lock:
            if type(calls) is not list or len(calls) != 1:
                return self._deny('one_call_required')
            if self._committed:
                return self._deny('already_committed')
            call = calls[0]
            if type(call) is not dict or set(call) != {'name', 'arguments'} or call['name'] != 'save_report':
                return self._deny('tool_not_allowed')
            result = validate_report_action(self._contract, call['arguments'], current_bytes=self._source())
            if not result.allowed or result.canonical_report is None:
                return self._deny(result.reason)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(mode='wb', dir=self._output,
                                                  prefix='.report-', delete=False) as writer:
                    temporary = Path(writer.name)
                    writer.write(result.canonical_report.encode('utf-8'))
                    writer.flush()
                    os.fsync(writer.fileno())
                # Faults/changes after validation but before commit are checked again.
                check = validate_report_action(self._contract, call['arguments'], current_bytes=self._source())
                if not check.allowed or check.canonical_report != result.canonical_report:
                    return self._deny('source_changed_before_commit')
                # Atomic no-clobber publication in the dedicated output directory.
                # A pre-existing report (including a symlink) is never overwritten.
                os.link(temporary, self._output / 'report.json')
                self._recovered = self._blocked > 0
                self._committed = True
            except OSError:
                return self._deny('report_write_failed')
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        # A cleanup failure must not conceal an already committed effect.
                        self._events.append({'status': 'cleanup_failed',
                                             'reason': 'temporary_cleanup_failed'})
            event = {'status': 'committed', 'reason': 'validated',
                     'report': json.loads(result.canonical_report)}
            self._events.append(event)
            return copy.deepcopy(event)
