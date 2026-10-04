"""Сканер модулей объекта внешних обработок и отчётов (issue #351).

Обнаруживает модули объекта в распаковке ``v8unpack`` внешних обработок
(``.epf``) и внешних отчётов (``.erf``) и возвращает общий контракт
``ModuleIndex`` из #203 с ``module_kind="object"``. Layout и семантика
доказаны сверкой с Конфигуратором в #351: модуль объекта лежит в файле
``<артефакт>/ExternalDataProcessor.obj.bsl`` и у обработки, и у отчёта
(upstream распаковывает оба вида одним классом), текст совпадает с модулем
объекта побайтно. Файла нет только у артефактов с пустым модулем объекта.

Артефакт — подкаталог корня (или подкаталога ``External``, как в
``scan_forms(mode="external")``) с файлом метаданных
``ExternalDataProcessor.json``. Имя владельца берётся из поля ``name`` этого
файла: имя каталога задаётся при распаковке и не является именем объекта.
Вид владельца: контейнер ``ReportForm`` означает отчёт; без него вид
определяется по суффиксу каталога ``.erf`` / ``.epf``. Если вид или имя
установить нельзя, а также при неоднозначном владельце запись не создаётся.

Сканер только читает: он не создаёт и не изменяет файлы и не вызывает
``scan_forms()``. Модули форм остаются в ``scan_forms``.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

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
    "EXTERNAL_METADATA_FILE",
    "EXTERNAL_OBJECT_MODULE_FILE",
    "EXTERNAL_REPORT_CONTAINER",
    "EXTERNAL_ROOT",
    "scan_external_object_modules",
]

EXTERNAL_ROOT = "External"
"""Необязательный подкаталог с артефактами, как в ``scan_forms``."""

EXTERNAL_OBJECT_MODULE_FILE = "ExternalDataProcessor.obj.bsl"
"""Файл модуля объекта и у внешней обработки, и у внешнего отчёта."""

EXTERNAL_METADATA_FILE = "ExternalDataProcessor.json"
"""Файл метаданных артефакта; поле ``name`` — имя объекта."""

EXTERNAL_REPORT_CONTAINER = "ReportForm"
"""Контейнер форм, который есть только у внешнего отчёта."""

_MAX_METADATA_BYTES = 16 * 1024 * 1024

_KINDS: dict[str, tuple[OwnerKind, str]] = {
    "report": ("external_report", "ExternalReport"),
    "processor": ("external_data_processor", "ExternalDataProcessor"),
}


@dataclass(frozen=True)
class _Owner:
    owner_kind: OwnerKind
    metadata_type: str
    name: str
    directory: Path
    resolved: Path
    relative_dir: str


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


def _base(export_root: Path, resolved_root: Path) -> tuple[Path, str]:
    candidate = exact_child_or_none(export_root, EXTERNAL_ROOT)
    if (
        candidate is not None
        and _is_real_dir(candidate)
        and _resolves_into(candidate, resolved_root)
    ):
        return candidate, f"{EXTERNAL_ROOT}/"
    return export_root, ""


def _owner_name(directory: Path, resolved: Path) -> str | None:
    try:
        found = exact_child(directory, EXTERNAL_METADATA_FILE)
    except OSError:
        return None
    if found is None:
        return None
    try:
        info = found.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_METADATA_BYTES:
            return None
        if found.resolve().parent != resolved:
            return None
        data = json.loads(found.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.isidentifier():
        return None
    return name


def _kind(directory: Path) -> str | None:
    container = exact_child_or_none(directory, EXTERNAL_REPORT_CONTAINER)
    if container is not None and _is_real_dir(container):
        return "report"
    suffix = directory.name.lower()
    if suffix.endswith(".erf"):
        return "report"
    if suffix.endswith(".epf"):
        return "processor"
    return None


def _owners(export_root: Path) -> list[_Owner]:
    resolved_root = export_root.resolve()
    base, prefix = _base(export_root, resolved_root)
    base_resolved = base.resolve()
    owners: list[_Owner] = []
    for child in base.iterdir():
        if not _is_real_dir(child) or not _resolves_into(child, base_resolved):
            continue
        relative_dir = f"{prefix}{child.name}"
        try:
            validate_relative_module_path(
                f"{relative_dir}/{EXTERNAL_OBJECT_MODULE_FILE}"
            )
        except ValueError:
            continue
        resolved = child.resolve()
        name = _owner_name(child, resolved)
        kind = _kind(child)
        if name is None or kind is None:
            continue
        owner_kind, metadata_type = _KINDS[kind]
        owners.append(
            _Owner(
                owner_kind=owner_kind,
                metadata_type=metadata_type,
                name=name,
                directory=child,
                resolved=resolved,
                relative_dir=relative_dir,
            )
        )
    return owners


def _entry(
    owner: _Owner,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    return ModuleEntry(
        module_kind="object",
        owner_kind=owner.owner_kind,
        metadata_type=owner.metadata_type,
        owner_name=owner.name,
        relative_path=f"{owner.relative_dir}/{EXTERNAL_OBJECT_MODULE_FILE}",
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _scan_module(owner: _Owner) -> ModuleEntry:
    try:
        found = exact_child(owner.directory, EXTERNAL_OBJECT_MODULE_FILE)
    except OSError:
        return _entry(owner, "read_error")
    if found is None:
        return _entry(owner, "missing")
    try:
        info = found.lstat()
    except FileNotFoundError:
        return _entry(owner, "missing")
    except OSError:
        return _entry(owner, "read_error")
    if not stat.S_ISREG(info.st_mode):
        return _entry(owner, "read_error")
    try:
        if found.resolve().parent != owner.resolved:
            return _entry(owner, "read_error")
        data = found.read_bytes()
    except OSError:
        return _entry(owner, "read_error")
    return _entry(
        owner,
        classify_bsl_bytes(data),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def scan_external_object_modules(root: Path) -> ModuleIndex:
    """Построить индекс модулей объекта внешних обработок и отчётов.

    Для каждого артефакта с установленными видом и именем создаётся ровно
    одна запись ``object`` с ``owner_kind`` ``external_data_processor`` или
    ``external_report``. Отсутствующий файл модуля получает статус
    ``missing`` («файла нет»; в #351 доказано, что так распаковываются
    артефакты с пустым модулем объекта). Артефакты с одинаковым
    ``module_id`` (без учёта регистра) неоднозначны и записей не дают.

    Symlink и иные не-обычные каталоги и файлы метаданных не используются.
    Symlink, каталог или иной не-обычный файл на месте модуля, выход за
    каталог артефакта и ``OSError`` дают ``read_error`` без чтения
    содержимого. Остальные статусы задаёт ``classify_bsl_bytes()``.
    """
    export_root = _validated_root(Path(root))
    groups: dict[str, list[_Owner]] = defaultdict(list)
    for owner in _owners(export_root):
        key = f"{owner.owner_kind}:{owner.metadata_type}:{owner.name}:object"
        groups[key.casefold()].append(owner)
    entries = [
        _scan_module(owners[0])
        for owners in groups.values()
        if len(owners) == 1
    ]
    return ModuleIndex.from_entries(entries)
