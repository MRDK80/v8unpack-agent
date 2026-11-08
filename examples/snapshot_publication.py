"""Host-only publication primitive shared by BSL/form reference executors (#500).

Категория: импортируемая внутренняя граница записи доверенного host.
Входные данные: приватный Linux output и canonical envelope доверенного validator.
Ожидаемый результат: один atomic no-clobber report либо явный отказ.
Поведение без данных: импорт не читает/пишет и не вызывает модель.
Никогда не регистрировать этот объект/commit/_prepare как инструмент модели.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, TypeVar


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

_Published = TypeVar('_Published')

class SnapshotPublication(AbstractContextManager[_Published]):
    """Host-internal IO only, not a validator/grant or sandbox for arbitrary Python."""

    def __init__(self, *, output_dir: Path, request_id: str | None = None):
        if not platform_supported():
            raise UnsupportedPlatform('snapshot publication requires Linux dirfd')
        if request_id is not None and (type(request_id) is not str or
                not 0 < len(request_id) <= 128 or not request_id.isascii() or
                any(not (c.isalnum() or c in '_.:-') for c in request_id)):
            raise ValueError('host request identifier must be bounded ASCII')
        self._request_id = request_id if request_id is not None else secrets.token_hex(24)
        self._fd, self._identity = _open_private_directory(Path(output_dir))
        self._lock = threading.Lock()
        self._pending: tuple[object, bytes] | None = None
        self._committed = False

    @property
    def request_id(self) -> str:
        return self._request_id

    def _prepare(self, envelope: dict[str, Any]) -> object:
        # Trusted subclass holds the lock and independently validated the report.
        data = (json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                           allow_nan=False) + '\n').encode('utf-8')
        ticket = object()
        self._pending = (ticket, data)
        return ticket

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

    def close(self) -> None:
        with self._lock:
            self._pending = None
            if self._fd >= 0:
                descriptor, self._fd = self._fd, -1
                os.close(descriptor)

    def __exit__(self, *args: object) -> None:
        self.close()
