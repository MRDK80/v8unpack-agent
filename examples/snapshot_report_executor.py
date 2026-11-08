"""Opt-in snapshot-only reference host (#499); NOT a Python/OS sandbox.

Категория: reference-компонент host, не самостоятельный CLI.
Входные данные: host ReportContract и приватный каталог результата на Linux.
Ожидаемый результат: атомарный snapshot_report/1 либо отказ, без перезаписи.
Поведение без данных: импорт ничего не читает/пишет. Модели выдаётся только
execute_calls, никогда validate/commit/ticket/descriptor или raw writer.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from v8unpack_agent.action_validation import (
    ReportContract,
    make_report_proposal,
    validate_report_action,
)


class UnsupportedPlatform(RuntimeError):
    """This effect adapter does not implement the required filesystem primitives."""


def platform_supported() -> bool:
    """Linux only; other platforms get no silent path-based fallback."""
    return (sys.platform == 'linux' and hasattr(os, 'O_NOFOLLOW')
            and hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_CLOEXEC')
            and all(f in os.supports_dir_fd for f in (os.open, os.link, os.unlink)))


def _open_private_directory(path: Path) -> tuple[int, dict[str, int]]:
    if sys.platform != 'linux':
        raise UnsupportedPlatform('private dirfd acquisition requires Linux')
    # Never resolve() through symlinks. Each component is opened relative to a
    # pinned parent; O_NOFOLLOW alone would protect only the last component.
    if not path.is_absolute() or '..' in path.parts or path.anchor != os.sep:
        raise ValueError('host output must be an absolute path without traversal')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            parent = descriptor
            descriptor = os.open(part, flags, dir_fd=parent)
            os.close(parent)
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('host output must be private and owned by the host uid')
        return descriptor, {'device': info.st_dev, 'inode': info.st_ino}
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    ticket: object | None = field(default=None, repr=False)


class SnapshotExecutor(AbstractContextManager['SnapshotExecutor']):
    """Host-owned single request, immutable bytes and pinned output directory.

    Trusted host code/uid owns the output through cleanup. An untrusted model
    submits JSON only. Arbitrary Python with this uid can bypass any Python API;
    this class does not grant isolation from that adversary. A snapshot result
    makes no assertion about the current source path or current output pathname.
    """

    def __init__(self, contract: ReportContract, *, output_dir: Path):
        if not platform_supported():
            raise UnsupportedPlatform('snapshot executor requires the Linux dirfd adapter')
        if not isinstance(contract, ReportContract) or type(contract.snapshot.raw) is not bytes:
            raise TypeError('host must supply a complete immutable byte contract')
        # Re-extract before acquiring any writer resource. The contract's frozen
        # SourceSnapshot/ModuleEntry and bytes belong to trusted host memory.
        make_report_proposal(contract)
        self._contract = contract
        self._request_id = secrets.token_hex(24)
        self._fd, self._identity = _open_private_directory(Path(output_dir))
        self._lock = threading.Lock()
        self._pending: tuple[object, bytes] | None = None
        self._committed = False

    @property
    def contract(self) -> ReportContract:
        return self._contract

    @property
    def request_id(self) -> str:
        return self._request_id

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
            data = (json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                               allow_nan=False) + '\n').encode('utf-8')
            ticket = object()
            self._pending = (ticket, data)
            return Admission(True, 'validated_snapshot', ticket)

    def commit(self, ticket: Any) -> dict[str, str]:
        """Host API only: consume exact in-memory ticket before any filesystem IO."""
        if sys.platform != 'linux':
            return {'status': 'blocked', 'reason': 'unsupported_platform'}
        with self._lock:
            if (self._fd < 0 or self._committed or self._pending is None
                    or ticket is not self._pending[0]):
                return {'status': 'blocked', 'reason': 'invalid_or_consumed_ticket'}
            data = self._pending[1]
            self._pending = None
            temporary = '.snapshot-' + secrets.token_hex(24)
            writer = -1
            created = False
            result = {'status': 'blocked', 'reason': 'publication_failed',
                      'cleanup': 'not_needed', 'durability': 'not_published'}
            try:
                writer = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self._fd)
                created = True
                offset = 0
                while offset < len(data):
                    written = os.write(writer, data[offset:])
                    if written <= 0:
                        raise OSError('write made no progress')
                    offset += written
                os.fsync(writer)
                # Source and destination are fixed basenames in the SAME pinned
                # private directory. Existing files/dirs/symlinks are not replaced.
                os.link(temporary, 'report.json', src_dir_fd=self._fd,
                        dst_dir_fd=self._fd, follow_symlinks=False)
                self._committed = True
                result.update(status='committed', reason='validated_snapshot', durability='unknown')
            except OSError:
                pass
            finally:
                if writer >= 0:
                    try:
                        os.close(writer)
                    except OSError:
                        result['close'] = 'failed'
                if created:
                    try:
                        os.unlink(temporary, dir_fd=self._fd)
                        result['cleanup'] = 'complete'
                    except OSError:
                        result['cleanup'] = 'failed'
                if self._committed:
                    try:
                        os.fsync(self._fd)
                        result['durability'] = 'synced'
                    except OSError:
                        # Publication already happened. Never pretend no effect.
                        result['durability'] = 'unknown'
            return result

    def execute_calls(self, calls: Any) -> dict[str, str]:
        """The ONLY operation registered with the model tool dispatcher."""
        decision = self.validate(calls)
        if not decision.allowed:
            return {'status': 'blocked', 'reason': decision.reason}
        return self.commit(decision.ticket)

    def close(self) -> None:
        with self._lock:
            self._pending = None
            if self._fd >= 0:
                descriptor, self._fd = self._fd, -1
                os.close(descriptor)

    def __exit__(self, *args: object) -> None:
        self.close()
