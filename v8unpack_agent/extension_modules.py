"""Модули расширения конфигурации в normalized-layout v8unpack (issue #435).

Корень распакованного ``.cfe`` отличается файлом ``ConfigurationExtension.json``
вместо ``Configuration.json``; имя расширения — его поле ``name``. Объекты
расширения лежат в том же layout, что и у конфигурации, поэтому их модули
находят существующие сканеры, а этот модуль:

* помечает записи именем расширения (``ModuleEntry.extension``), чтобы
  ``module_id`` и ``artifact_id`` не совпадали с основной конфигурацией;
* не выдаёт ``missing``: у заимствованного объекта отсутствие файла значит
  «модуль не переопределён», а не «файла нет»;
* добавляет модули уровня расширения ``ConfigurationExtension.app.bsl`` и
  ``ConfigurationExtension.ssn.bsl`` (сверка с Конфигуратором — #433).

Заимствованные и собственные объекты не различаются: подтверждённого
признака в JSON v8unpack нет (#435). Модуль только читает файлы.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from v8unpack_agent._exact_names import exact_child
from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    ModuleKind,
    ModuleReadStatus,
    classify_bsl_bytes,
)

__all__ = [
    "EXTENSION_METADATA_FILE",
    "EXTENSION_METADATA_TYPE",
    "EXTENSION_MODULE_FILES",
    "apply_extension",
    "extension_name",
    "scan_extension_modules",
]

EXTENSION_METADATA_FILE = "ConfigurationExtension.json"
EXTENSION_METADATA_TYPE = "ConfigurationExtension"
_MAX_METADATA_BYTES = 16 * 1024 * 1024

EXTENSION_MODULE_FILES: Mapping[ModuleKind, str] = MappingProxyType(
    {
        "managed_application": "ConfigurationExtension.app.bsl",
        "session": "ConfigurationExtension.ssn.bsl",
    }
)
"""Модули уровня расширения, подтверждённые в #433."""


def extension_name(root: Path) -> str | None:
    """Имя расширения, если ``root`` — распакованный ``.cfe``, иначе ``None``.

    Файл ``ConfigurationExtension.json`` есть, но не читается, не является
    JSON-объектом или его ``name`` не идентификатор 1С — ``ValueError``:
    выгрузку расширения нельзя молча обработать как конфигурацию.
    """
    path = exact_child(Path(root), EXTENSION_METADATA_FILE)
    if path is None:
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_METADATA_BYTES:
        raise ValueError("extension metadata is not a regular file")
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("extension metadata is not valid JSON") from exc
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError("extension name is not a 1C identifier")
    return name


def _entry(
    module_kind: ModuleKind,
    name: str,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind=module_kind,
        owner_kind="extension",
        metadata_type=EXTENSION_METADATA_TYPE,
        owner_name=name,
        relative_path=EXTENSION_MODULE_FILES[module_kind],
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
        extension=name,
    )


def scan_extension_modules(root: Path, name: str) -> ModuleIndex:
    """Модули уровня расширения ``name`` в корне ``root``.

    Запись создаётся только для существующего файла; отсутствующий модуль
    записи не даёт. Symlink и иной не-обычный файл, а также ``OSError`` дают
    ``read_error`` без чтения содержимого. ``owner_kind`` — ``extension``,
    ``metadata_type`` — ``ConfigurationExtension``, ``owner_name`` — имя
    расширения.
    """
    export_root = Path(root)
    entries: list[ModuleEntry] = []
    for module_kind in sorted(EXTENSION_MODULE_FILES):
        file_name = EXTENSION_MODULE_FILES[module_kind]
        try:
            path = exact_child(export_root, file_name)
            if path is None:
                continue
            if not stat.S_ISREG(path.lstat().st_mode):
                entries.append(_entry(module_kind, name, "read_error"))
                continue
            data = path.read_bytes()
        except OSError:
            entries.append(_entry(module_kind, name, "read_error"))
            continue
        entries.append(
            _entry(
                module_kind,
                name,
                classify_bsl_bytes(data),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return ModuleIndex.from_entries(entries)


def apply_extension(
    root: Path,
    entries: Iterable[ModuleEntry],
    *,
    include_extension_modules: bool = True,
) -> list[ModuleEntry]:
    """Привести записи сканеров к выгрузке ``root``.

    Для конфигурации записи возвращаются без изменений. Для расширения
    записи ``missing`` отбрасываются, остальные получают имя расширения, и
    при ``include_extension_modules`` добавляются модули уровня расширения
    (группа модулей ``configuration``).
    """
    items = list(entries)
    name = extension_name(root)
    if name is None:
        return items
    marked = [
        dataclasses.replace(entry, extension=name)
        for entry in items
        if entry.read_status != "missing"
    ]
    if include_extension_modules:
        marked.extend(scan_extension_modules(root, name))
    return marked
