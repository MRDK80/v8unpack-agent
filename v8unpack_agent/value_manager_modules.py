"""Сканер модулей менеджеров значений констант 1С (issue #337).

Обнаруживает модули менеджеров значений констант в normalized-layout
v8unpack и возвращает общий контракт ``ModuleIndex`` из #203 с
``module_kind="value_manager"``. Единственная доказанная строка взята из
исследования #202 (``docs/research/bsl_module_inventory_issue202.md``) со
статусом ``designer_content_match_A``: текст ``Constant.obj.bsl`` совпал с
``Constants/{Name}/Ext/ValueManagerModule.bsl`` выгрузки Конфигуратором.
Суффикс ``obj`` сам по себе не определяет вид модуля: ``value_manager``
задаёт пара «тип ``Constant`` + файл». Сканер только читает: он не создаёт
и не изменяет файлы и не вызывает ``scan_forms()``.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    ModuleReadStatus,
    classify_bsl_bytes,
    validate_relative_module_path,
)

__all__ = [
    "VALUE_MANAGER_MODULE_FILES",
    "scan_value_manager_modules",
]

_FILES: dict[str, str] = {
    "Constant": "Constant.obj.bsl",
}

VALUE_MANAGER_MODULE_FILES: Mapping[str, str] = MappingProxyType(dict(_FILES))
"""Доказанные в #202 пары «metadata_type → файл модуля менеджера значения».

Файл лежит непосредственно в каталоге константы:
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
    metadata_type: str, owner_name: str, file_name: str
) -> bool:
    if not owner_name.isidentifier():
        return False
    try:
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
    file_name: str,
) -> list[_Owner]:
    type_dir = export_root / metadata_type
    if not _is_real_dir(type_dir):
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
        if not _is_valid_owner_name(metadata_type, child.name, file_name):
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
    file_name: str,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind="value_manager",
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


def _scan_module(owner: _Owner, file_name: str) -> ModuleEntry:
    path = owner.directory / file_name
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _entry(owner, file_name, "missing")
    except OSError:
        return _entry(owner, file_name, "read_error")
    if not stat.S_ISREG(info.st_mode):
        return _entry(owner, file_name, "read_error")
    try:
        if path.resolve().parent != owner.resolved:
            return _entry(owner, file_name, "read_error")
        data = path.read_bytes()
    except OSError:
        return _entry(owner, file_name, "read_error")
    return _entry(
        owner,
        file_name,
        classify_bsl_bytes(data),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def scan_value_manager_modules(root: Path) -> ModuleIndex:
    """Построить индекс модулей менеджеров значений констант выгрузки v8unpack.

    Владелец — существующий обычный каталог ``Constant/<имя>``. Для каждого
    владельца создаётся ровно одна запись ``value_manager``; отсутствующий
    файл получает статус ``missing`` («файла нет», а не «модуля нет»): в
    #202 у большинства констант файла модуля не было. Каталоги других
    типов записей не создают; модуль константы не классифицируется как
    ``object``, ``manager`` или ``form``.

    Symlink и иные не-обычные каталоги типа и владельцев не обходятся.
    Symlink, каталог или иной не-обычный файл на месте модуля, выход за
    каталог владельца и ``OSError`` дают ``read_error`` без чтения
    содержимого. Остальные статусы задаёт ``classify_bsl_bytes()``.
    """
    export_root = _validated_root(Path(root))
    resolved_root = export_root.resolve()
    entries: list[ModuleEntry] = []
    for metadata_type in sorted(_FILES):
        file_name = _FILES[metadata_type]
        owners = _owners(export_root, resolved_root, metadata_type, file_name)
        for owner in owners:
            entries.append(_scan_module(owner, file_name))
    return ModuleIndex.from_entries(entries)
