"""Сканер модулей команд 1С (issue #207).

Обнаруживает модули общих команд и команд прикладных объектов в
normalized-layout v8unpack и возвращает общий контракт ``ModuleIndex`` из
#203 с ``module_kind="command"``. Вид владельца хранится отдельно:
``owner_kind="common_command"`` для общих команд и
``owner_kind="metadata_object_command"`` для команд объектов. Классы команд
взяты только из доказанных строк исследования #202
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
    ModuleReadStatus,
    OwnerKind,
    classify_bsl_bytes,
    validate_relative_module_path,
)

__all__ = [
    "COMMON_COMMAND_MODULE_FILE",
    "COMMON_COMMAND_TYPE",
    "OBJECT_COMMAND_CONTAINERS",
    "scan_command_modules",
]

COMMON_COMMAND_TYPE = "CommonCommand"
"""``metadata_type`` общей команды и имя её каталога верхнего уровня."""

COMMON_COMMAND_MODULE_FILE = "CommonCommand.obj.bsl"
"""Файл модуля общей команды: ``CommonCommand/<Команда>/<файл>``."""

_CONTAINERS: dict[str, str] = {
    "Catalog": "CatalogCommand",
    "DataProcessor": "DataProcessorCommand",
    "Document": "DocumentCommand",
    "InformationRegister": "InformationRegisterCommand",
    "Report": "ReportCommand",
}

OBJECT_COMMAND_CONTAINERS: Mapping[str, str] = MappingProxyType(
    dict(_CONTAINERS)
)
"""Доказанные в #202 пары «metadata_type → каталог команд объекта».

Модуль команды объекта лежит в
``<metadata_type>/<Объект>/<контейнер>/<Команда>/<контейнер>.obj.bsl``.
"""

_MODULE_SUFFIX = ".obj.bsl"


@dataclass(frozen=True)
class _Command:
    owner_kind: OwnerKind
    metadata_type: str
    owner_name: str
    directory: Path
    resolved: Path
    relative_path: str


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


def _subdirs(parent: Path) -> list[Path]:
    """Обычные подкаталоги с именем-идентификатором, лежащие внутри parent."""
    parent_resolved = parent.resolve()
    found: list[Path] = []
    for child in parent.iterdir():
        if not child.name.isidentifier():
            continue
        if not _is_real_dir(child):
            continue
        if not _resolves_into(child, parent_resolved):
            continue
        found.append(child)
    return sorted(found, key=lambda p: (p.name.casefold(), p.name))


def _type_dir(
    export_root: Path, resolved_root: Path, name: str
) -> Path | None:
    type_dir = exact_child_or_none(export_root, name)
    if type_dir is None or not _is_real_dir(type_dir):
        return None
    if not _resolves_into(type_dir, resolved_root):
        return None
    return type_dir


def _command(
    owner_kind: OwnerKind,
    metadata_type: str,
    owner_name: str,
    directory: Path,
    relative_path: str,
) -> _Command | None:
    try:
        validate_relative_module_path(relative_path)
    except ValueError:
        return None
    return _Command(
        owner_kind=owner_kind,
        metadata_type=metadata_type,
        owner_name=owner_name,
        directory=directory,
        resolved=directory.resolve(),
        relative_path=relative_path,
    )


def _common_commands(
    export_root: Path, resolved_root: Path
) -> list[_Command]:
    type_dir = _type_dir(export_root, resolved_root, COMMON_COMMAND_TYPE)
    if type_dir is None:
        return []
    commands: list[_Command] = []
    for command_dir in _subdirs(type_dir):
        command = _command(
            "common_command",
            COMMON_COMMAND_TYPE,
            command_dir.name,
            command_dir,
            f"{COMMON_COMMAND_TYPE}/{command_dir.name}/"
            f"{COMMON_COMMAND_MODULE_FILE}",
        )
        if command is not None:
            commands.append(command)
    return commands


def _object_commands(
    export_root: Path, resolved_root: Path, metadata_type: str
) -> list[_Command]:
    type_dir = _type_dir(export_root, resolved_root, metadata_type)
    if type_dir is None:
        return []
    container = _CONTAINERS[metadata_type]
    file_name = f"{container}{_MODULE_SUFFIX}"
    commands: list[_Command] = []
    for owner_dir in _subdirs(type_dir):
        container_dir = exact_child_or_none(owner_dir, container)
        if container_dir is None or not _is_real_dir(container_dir):
            continue
        if not _resolves_into(container_dir, owner_dir.resolve()):
            continue
        for command_dir in _subdirs(container_dir):
            command = _command(
                "metadata_object_command",
                metadata_type,
                f"{owner_dir.name}.{command_dir.name}",
                command_dir,
                f"{metadata_type}/{owner_dir.name}/{container}/"
                f"{command_dir.name}/{file_name}",
            )
            if command is not None:
                commands.append(command)
    return commands


def _entry(
    command: _Command,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind="command",
        owner_kind=command.owner_kind,
        metadata_type=command.metadata_type,
        owner_name=command.owner_name,
        relative_path=command.relative_path,
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _scan_module(command: _Command) -> ModuleEntry:
    file_name = command.relative_path.rsplit("/", 1)[1]
    try:
        found = exact_child(command.directory, file_name)
    except OSError:
        return _entry(command, "read_error")
    if found is None:
        return _entry(command, "missing")
    path = found
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _entry(command, "missing")
    except OSError:
        return _entry(command, "read_error")
    if not stat.S_ISREG(info.st_mode):
        return _entry(command, "read_error")
    try:
        if path.resolve().parent != command.resolved:
            return _entry(command, "read_error")
        data = path.read_bytes()
    except OSError:
        return _entry(command, "read_error")
    return _entry(
        command,
        classify_bsl_bytes(data),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def scan_command_modules(root: Path) -> ModuleIndex:
    """Построить индекс модулей команд выгрузки v8unpack.

    Команда — существующий обычный каталог команды доказанного класса:
    ``CommonCommand/<Команда>`` для общей команды или
    ``<metadata_type>/<Объект>/<контейнер>/<Команда>`` для команды объекта
    из ``OBJECT_COMMAND_CONTAINERS``. Для каждой команды создаётся ровно
    одна запись ``command``; отсутствующий файл модуля получает статус
    ``missing`` («файла нет», а не «модуля нет»). Каталоги недоказанных
    типов, контейнеры с чужим именем и файлы вне каталога команды записей
    не создают.

    ``owner_name`` общей команды — её имя, команды объекта —
    ``<Объект>.<Команда>``; поэтому одноимённые команды разных владельцев
    получают разные ``module_id`` по контракту #203.

    Symlink и иные не-обычные каталоги не обходятся. Symlink, каталог или
    иной не-обычный файл на месте модуля, выход за каталог команды и
    ``OSError`` дают ``read_error`` без чтения содержимого. Остальные
    статусы задаёт ``classify_bsl_bytes()``.
    """
    export_root = _validated_root(Path(root))
    resolved_root = export_root.resolve()
    commands = _common_commands(export_root, resolved_root)
    for metadata_type in sorted(_CONTAINERS):
        commands.extend(
            _object_commands(export_root, resolved_root, metadata_type)
        )
    return ModuleIndex.from_entries(_scan_module(c) for c in commands)
