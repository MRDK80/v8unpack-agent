"""Сканер модулей объектов и менеджеров метаданных 1С (issue #205).

Обнаруживает объектные и менеджерские модули прикладных объектов
конфигурации в normalized-layout v8unpack и возвращает общий контракт
``ModuleIndex`` из #203. Пары «тип метаданных → вид модуля → файл» взяты
только из доказанных строк исследования #202
(``docs/research/bsl_module_inventory_issue202.md``) со статусом
``designer_content_match_A``. Сканер только читает: он не создаёт и не
изменяет файлы и не вызывает ``scan_forms()``.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from v8unpack_agent._exact_names import exact_child, exact_child_or_none
from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    ModuleKind,
    ModuleReadStatus,
    classify_bsl_bytes,
    validate_relative_module_path,
)

__all__ = [
    "METADATA_OBJECT_MODULE_LAYOUTS",
    "scan_metadata_object_modules",
]

_LAYOUTS: dict[str, dict[ModuleKind, str]] = {
    "AccumulationRegister": {
        "manager": "AccumulationRegister.mgr.bsl",
    },
    "BusinessProcess": {
        "object": "BusinessProcess.obj.bsl",
    },
    "Catalog": {
        "manager": "Catalog.mgr.bsl",
        "object": "Catalog.obj.bsl",
    },
    "ChartOfCharacteristicType": {
        "object": "ChartOfCharacteristicType.obj.bsl",
    },
    "DataProcessor": {
        "manager": "DataProcessor.mgr.bsl",
        "object": "DataProcessor.obj.bsl",
    },
    "Document": {
        "manager": "Document.mgr.bsl",
        "object": "Document.obj.bsl",
    },
    "DocumentJournal": {
        "manager": "DocumentJournal.mgr.bsl",
    },
    "Enum": {
        "manager": "Enum.obj.bsl",
    },
    "ExchangePlan": {
        "manager": "ExchangePlan.mgr.bsl",
        "object": "ExchangePlan.obj.bsl",
    },
    "InformationRegister": {
        "manager": "InformationRegister.mgr.bsl",
    },
    "Report": {
        "manager": "Report.mgr.bsl",
        "object": "Report.obj.bsl",
    },
    "Task": {
        "object": "Task.obj.bsl",
    },
}

METADATA_OBJECT_MODULE_LAYOUTS: Mapping[str, Mapping[ModuleKind, str]] = (
    MappingProxyType(
        {
            metadata_type: MappingProxyType(dict(layout))
            for metadata_type, layout in _LAYOUTS.items()
        }
    )
)
"""Доказанные в #202 пары «metadata_type → module_kind → имя файла».

Файл лежит непосредственно в каталоге объекта:
``<metadata_type>/<owner_name>/<имя файла>``.
"""


@dataclass(frozen=True)
class _Owner:
    metadata_type: str
    name: str
    directory: Path
    resolved: Path


def _validated_root(root: Path) -> Path:
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    return root


def _is_real_dir(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode)


def _resolves_into(path: Path, expected_parent: Path) -> bool:
    try:
        return path.resolve().parent == expected_parent
    except OSError:
        return False


def _relative_path(metadata_type: str, owner_name: str, file_name: str) -> str:
    return f"{metadata_type}/{owner_name}/{file_name}"


def _is_valid_owner_name(
    metadata_type: str,
    owner_name: str,
    layout: Mapping[ModuleKind, str],
) -> bool:
    if not owner_name.isidentifier():
        return False
    try:
        for file_name in layout.values():
            validate_relative_module_path(
                _relative_path(metadata_type, owner_name, file_name)
            )
    except ValueError:
        return False
    return True


def _owners(
    export_root: Path,
    resolved_root: Path,
    metadata_type: str,
    layout: Mapping[ModuleKind, str],
) -> list[_Owner]:
    type_dir = exact_child_or_none(export_root, metadata_type)
    if type_dir is None or not _is_real_dir(type_dir):
        return []
    if not _resolves_into(type_dir, resolved_root):
        return []
    type_resolved = type_dir.resolve()
    owners: list[_Owner] = []
    for child in type_dir.iterdir():
        if not _is_real_dir(child):
            continue
        if not _resolves_into(child, type_resolved):
            continue
        if not _is_valid_owner_name(metadata_type, child.name, layout):
            continue
        owners.append(
            _Owner(
                metadata_type=metadata_type,
                name=child.name,
                directory=child,
                resolved=child.resolve(),
            )
        )
    return sorted(owners, key=lambda o: (o.name.casefold(), o.name))


def _entry(
    owner: _Owner,
    module_kind: ModuleKind,
    file_name: str,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind=module_kind,
        owner_kind="metadata_object",
        metadata_type=owner.metadata_type,
        owner_name=owner.name,
        relative_path=_relative_path(
            owner.metadata_type, owner.name, file_name
        ),
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _scan_module(
    owner: _Owner, module_kind: ModuleKind, file_name: str
) -> ModuleEntry:
    try:
        found = exact_child(owner.directory, file_name)
    except OSError:
        return _entry(owner, module_kind, file_name, "read_error")
    if found is None:
        return _entry(owner, module_kind, file_name, "missing")
    path = found
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _entry(owner, module_kind, file_name, "missing")
    except OSError:
        return _entry(owner, module_kind, file_name, "read_error")
    if not stat.S_ISREG(info.st_mode):
        return _entry(owner, module_kind, file_name, "read_error")
    try:
        if path.resolve().parent != owner.resolved:
            return _entry(owner, module_kind, file_name, "read_error")
        data = path.read_bytes()
    except OSError:
        return _entry(owner, module_kind, file_name, "read_error")
    return _entry(
        owner,
        module_kind,
        file_name,
        classify_bsl_bytes(data),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def scan_metadata_object_modules(root: Path) -> ModuleIndex:
    """Построить индекс объектных и менеджерских модулей выгрузки v8unpack.

    Владелец — существующий обычный каталог ``<metadata_type>/<имя>``
    доказанного типа из ``METADATA_OBJECT_MODULE_LAYOUTS``. Для каждого
    владельца создаётся ровно одна запись на каждый применимый к его типу
    вид модуля; отсутствующий файл получает статус ``missing`` («файла
    нет», а не «модуля нет»). Неприменимые виды и недоказанные типы
    записей не создают.

    Symlink и иные не-обычные каталоги типов и владельцев не обходятся.
    Symlink, каталог или иной не-обычный файл на месте модуля, выход за
    каталог владельца и ``OSError`` дают ``read_error`` без чтения
    содержимого. Остальные статусы задаёт ``classify_bsl_bytes()``.
    """
    export_root = _validated_root(Path(root))
    resolved_root = export_root.resolve()
    entries: list[ModuleEntry] = []
    for metadata_type in sorted(_LAYOUTS):
        layout = _LAYOUTS[metadata_type]
        owners = _owners(export_root, resolved_root, metadata_type, layout)
        for owner in owners:
            for module_kind in sorted(layout):
                entries.append(
                    _scan_module(owner, module_kind, layout[module_kind])
                )
    return ModuleIndex.from_entries(entries)
