"""Сканер BSL-модулей уровня конфигурации 1С (issue #204).

Обнаруживает четыре глобальных модуля конфигурации в normalized-layout
v8unpack и возвращает общий контракт ``ModuleIndex`` из #203. Соответствия
«вид модуля → файл» взяты только из доказанных строк исследования #202
(``docs/research/bsl_module_inventory_issue202.md``). Сканер только читает:
он не создаёт и не изменяет файлы и не вызывает ``scan_forms()``.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    ModuleKind,
    ModuleReadStatus,
    classify_bsl_bytes,
)

__all__ = [
    "CONFIGURATION_MODULE_FILES",
    "scan_configuration_modules",
]

_FILES: dict[ModuleKind, str] = {
    "external_connection": "Configuration.con.bsl",
    "managed_application": "Configuration.app.bsl",
    "ordinary_application": "Configuration.802.bsl",
    "session": "Configuration.seance.bsl",
}

CONFIGURATION_MODULE_FILES: Mapping[ModuleKind, str] = MappingProxyType(
    _FILES
)
"""Доказанные в #202 пары «module_kind → файл в корне выгрузки»."""


def _validated_root(root: Path) -> Path:
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    return root


def _scan_one(
    root: Path,
    resolved_root: Path,
    module_kind: ModuleKind,
    file_name: str,
) -> ModuleEntry | None:
    path = root / file_name
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return _entry(module_kind, file_name, "read_error")
    if not stat.S_ISREG(info.st_mode):
        return _entry(module_kind, file_name, "read_error")
    try:
        if path.resolve().parent != resolved_root:
            return _entry(module_kind, file_name, "read_error")
        data = path.read_bytes()
    except OSError:
        return _entry(module_kind, file_name, "read_error")
    return _entry(
        module_kind,
        file_name,
        classify_bsl_bytes(data),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _entry(
    module_kind: ModuleKind,
    file_name: str,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind=module_kind,
        owner_kind="configuration",
        metadata_type=None,
        owner_name=None,
        relative_path=file_name,
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def scan_configuration_modules(root: Path) -> ModuleIndex:
    """Построить индекс модулей уровня конфигурации выгрузки v8unpack.

    Ищутся только четыре доказанных файла в корне выгрузки. Если в корне
    нет ни одного из них, корень не считается выгрузкой конфигурации и
    возвращается пустой индекс. Иначе для каждого из четырёх видов
    создаётся ровно одна запись; отсутствующий файл получает статус
    ``missing`` («файла нет», а не «модуля нет»).

    Symlink, каталог или иной не-обычный файл на месте модуля, а также
    ``OSError`` дают ``read_error`` без чтения содержимого за пределами
    корня. Остальные статусы задаёт ``classify_bsl_bytes()``.
    """
    export_root = _validated_root(Path(root))
    resolved_root = export_root.resolve()
    found: dict[ModuleKind, ModuleEntry | None] = {
        kind: _scan_one(export_root, resolved_root, kind, name)
        for kind, name in sorted(_FILES.items())
    }
    if all(entry is None for entry in found.values()):
        return ModuleIndex()
    entries = [
        entry if entry is not None else _entry(kind, _FILES[kind], "missing")
        for kind, entry in found.items()
    ]
    return ModuleIndex.from_entries(entries)
