"""Универсальная неизменяемая модель BSL-модулей конфигурации 1С.

Контракт основан на результатах исследования #202
(``docs/research/bsl_module_inventory_issue202.md``) и описан в
``docs/modules.md``. Модуль не обращается к файловой системе, не знает
конкретного layout выгрузки и не хранит BSL-текст.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, get_args

from v8unpack_agent.common_modules import CommonModuleEntry

__all__ = [
    "MODULE_INDEX_SCHEMA",
    "MODULE_KINDS",
    "MODULE_READ_STATUSES",
    "OWNER_KINDS",
    "ModuleEntry",
    "ModuleIndex",
    "ModuleKind",
    "ModuleReadStatus",
    "OwnerKind",
    "classify_bsl_bytes",
    "module_entry_from_common_module",
    "validate_relative_module_path",
]

ModuleKind = Literal[
    "command",
    "common_module",
    "external_connection",
    "form",
    "managed_application",
    "manager",
    "object",
    "ordinary_application",
    "record_set",
    "service",
    "session",
    "value_manager",
]

OwnerKind = Literal[
    "common_command",
    "common_form",
    "common_module",
    "configuration",
    "extension",
    "external_data_processor",
    "external_report",
    "metadata_object",
    "metadata_object_command",
    "metadata_object_form",
]

ModuleReadStatus = Literal[
    "ok",
    "empty",
    "whitespace_only",
    "missing",
    "read_error",
]

MODULE_KINDS: frozenset[str] = frozenset(get_args(ModuleKind))
OWNER_KINDS: frozenset[str] = frozenset(get_args(OwnerKind))
MODULE_READ_STATUSES: frozenset[str] = frozenset(get_args(ModuleReadStatus))

MODULE_INDEX_SCHEMA = "module_index/1"

_BSL_SUFFIX = ".bsl"
_BACKSLASH = chr(92)
_METADATA_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_ENTRY_KEYS = frozenset(
    [
        "module_id",
        "module_kind",
        "owner_kind",
        "metadata_type",
        "owner_name",
        "relative_path",
        "read_status",
        "size_bytes",
        "sha256",
    ]
)


def _has_control_chars(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def validate_relative_module_path(value: str) -> str:
    """Проверить относительный POSIX-путь модуля и вернуть его без изменений.

    Правила не зависят от ОС запуска: путь одновременно проверяется по
    семантике POSIX и Windows. Нарушение приводит к ``ValueError``.
    """
    if not isinstance(value, str):
        raise TypeError("relative_path must be str")
    if not value:
        raise ValueError("relative_path must not be empty")
    if _has_control_chars(value):
        raise ValueError("relative_path must not contain control characters")
    if _BACKSLASH in value:
        raise ValueError("relative_path must use '/' as separator")
    windows = PureWindowsPath(value)
    if (
        PurePosixPath(value).is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
    ):
        raise ValueError("relative_path must be relative to export root")
    segments = value.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise ValueError(
                "relative_path must not contain empty, '.' or '..' segments"
            )
        if ":" in segment:
            raise ValueError("relative_path segment must not contain ':'")
        if segment != segment.rstrip(" ."):
            raise ValueError(
                "relative_path segment must not end with space or dot"
            )
        if segment.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ValueError(
                "relative_path segment must not be a reserved device name"
            )
    if not segments[-1].endswith(_BSL_SUFFIX):
        raise ValueError("relative_path must end with '.bsl'")
    return value


def _validate_owner_name(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("owner_name must be str or None")
    for part in value.split("."):
        if not part.isidentifier():
            raise ValueError(
                "owner_name must be dot-separated 1C identifiers"
            )


def classify_bsl_bytes(data: bytes) -> ModuleReadStatus:
    """Классифицировать прочитанное содержимое BSL-файла.

    ``empty`` — 0 байт или только UTF-8 BOM; ``whitespace_only`` — после
    BOM только пробельные символы; ``read_error`` — невалидный UTF-8.
    Отсутствие файла (``missing``) определяет вызывающий код.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "read_error"
    if text == "":
        return "empty"
    if text.strip() == "":
        return "whitespace_only"
    return "ok"


@dataclass(frozen=True)
class ModuleEntry:
    """Неизменяемый указатель на BSL-модуль внутри выгрузки."""

    module_kind: ModuleKind
    owner_kind: OwnerKind
    metadata_type: str | None
    owner_name: str | None
    relative_path: str
    read_status: ModuleReadStatus
    size_bytes: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.module_kind not in MODULE_KINDS:
            raise ValueError(f"unknown module_kind: {self.module_kind!r}")
        if self.owner_kind not in OWNER_KINDS:
            raise ValueError(f"unknown owner_kind: {self.owner_kind!r}")
        if self.read_status not in MODULE_READ_STATUSES:
            raise ValueError(f"unknown read_status: {self.read_status!r}")
        if self.owner_kind == "configuration":
            if self.metadata_type is not None or self.owner_name is not None:
                raise ValueError(
                    "configuration owner requires metadata_type and "
                    "owner_name to be None"
                )
        else:
            if self.metadata_type is None or self.owner_name is None:
                raise ValueError(
                    "non-configuration owner requires metadata_type and "
                    "owner_name"
                )
        if self.metadata_type is not None and (
            not isinstance(self.metadata_type, str)
            or not _METADATA_TYPE_RE.fullmatch(self.metadata_type)
        ):
            raise ValueError("metadata_type must be an ASCII identifier")
        if self.owner_name is not None:
            _validate_owner_name(self.owner_name)
        validate_relative_module_path(self.relative_path)
        if self.size_bytes is not None and (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative int or None")
        if self.sha256 is not None and (
            not isinstance(self.sha256, str)
            or not _SHA256_RE.fullmatch(self.sha256)
        ):
            raise ValueError("sha256 must be 64 lowercase hex chars or None")
        if self.read_status == "missing" and (
            self.size_bytes is not None or self.sha256 is not None
        ):
            raise ValueError("missing module must not have size or sha256")

    @property
    def module_id(self) -> str:
        """Стабильный идентификатор, не зависящий от layout и ОС."""
        return ":".join(
            [
                self.owner_kind,
                self.metadata_type or "",
                self.owner_name or "",
                self.module_kind,
            ]
        )

    @property
    def path(self) -> PurePosixPath:
        """Относительный путь как ``PurePosixPath``."""
        return PurePosixPath(self.relative_path)

    def sort_key(self) -> tuple[str, str]:
        """Детерминированный OS-нейтральный ключ сортировки."""
        return (self.relative_path.casefold(), self.relative_path)

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без BSL-текста."""
        return {
            "module_id": self.module_id,
            "module_kind": self.module_kind,
            "owner_kind": self.owner_kind,
            "metadata_type": self.metadata_type,
            "owner_name": self.owner_name,
            "relative_path": self.relative_path,
            "read_status": self.read_status,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModuleEntry:
        """Восстановить запись из ``to_dict()`` со строгой проверкой ключей."""
        keys = frozenset(data)
        if keys != _ENTRY_KEYS:
            raise ValueError("module entry keys do not match schema")
        entry = cls(
            module_kind=data["module_kind"],
            owner_kind=data["owner_kind"],
            metadata_type=data["metadata_type"],
            owner_name=data["owner_name"],
            relative_path=data["relative_path"],
            read_status=data["read_status"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
        )
        if entry.module_id != data["module_id"]:
            raise ValueError("module_id does not match entry fields")
        return entry


@dataclass(frozen=True)
class ModuleIndex:
    """Неизменяемый детерминированно отсортированный индекс модулей.

    Индекс не читает и не пишет файлы. Дубликаты ``module_id`` и
    ``relative_path`` (без учёта регистра) отклоняются.
    """

    entries: tuple[ModuleEntry, ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        for entry in entries:
            if not isinstance(entry, ModuleEntry):
                raise TypeError("ModuleIndex accepts only ModuleEntry")
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for entry in entries:
            module_id = entry.module_id.casefold()
            if module_id in seen_ids:
                raise ValueError(f"duplicate module_id: {entry.module_id!r}")
            seen_ids.add(module_id)
            path_key = entry.relative_path.casefold()
            if path_key in seen_paths:
                raise ValueError("duplicate relative_path")
            seen_paths.add(path_key)
        ordered = tuple(sorted(entries, key=ModuleEntry.sort_key))
        object.__setattr__(self, "entries", ordered)

    @classmethod
    def from_entries(cls, entries: Iterable[ModuleEntry]) -> ModuleIndex:
        """Построить индекс из произвольной последовательности записей."""
        return cls(entries=tuple(entries))

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[ModuleEntry]:
        return iter(self.entries)

    @property
    def total(self) -> int:
        """Количество записей."""
        return len(self.entries)

    def get(self, module_id: str) -> ModuleEntry | None:
        """Найти запись по ``module_id`` без учёта регистра."""
        key = module_id.casefold()
        for entry in self.entries:
            if entry.module_id.casefold() == key:
                return entry
        return None

    def filter(
        self,
        *,
        module_kind: ModuleKind | None = None,
        owner_kind: OwnerKind | None = None,
        metadata_type: str | None = None,
        read_status: ModuleReadStatus | None = None,
    ) -> ModuleIndex:
        """Отфильтровать по виду, владельцу и статусу без знания layout."""
        if module_kind is not None and module_kind not in MODULE_KINDS:
            raise ValueError(f"unknown module_kind: {module_kind!r}")
        if owner_kind is not None and owner_kind not in OWNER_KINDS:
            raise ValueError(f"unknown owner_kind: {owner_kind!r}")
        if read_status is not None and read_status not in (
            MODULE_READ_STATUSES
        ):
            raise ValueError(f"unknown read_status: {read_status!r}")
        selected = tuple(
            entry
            for entry in self.entries
            if (module_kind is None or entry.module_kind == module_kind)
            and (owner_kind is None or entry.owner_kind == owner_kind)
            and (
                metadata_type is None
                or entry.metadata_type == metadata_type
            )
            and (read_status is None or entry.read_status == read_status)
        )
        return ModuleIndex(entries=selected)

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление индекса без BSL-текста."""
        return {
            "schema": MODULE_INDEX_SCHEMA,
            "total": self.total,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self) -> str:
        """Детерминированный JSON: сортированные ключи, LF, UTF-8 текст."""
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModuleIndex:
        """Восстановить индекс из ``to_dict()``."""
        if data.get("schema") != MODULE_INDEX_SCHEMA:
            raise ValueError("unsupported module index schema")
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise TypeError("entries must be a list")
        index = cls.from_entries(
            ModuleEntry.from_dict(item) for item in raw_entries
        )
        if data.get("total") != index.total:
            raise ValueError("total does not match entries")
        return index


def module_entry_from_common_module(
    entry: CommonModuleEntry,
    read_status: ModuleReadStatus,
    *,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> ModuleEntry:
    """Адаптировать ``CommonModuleEntry`` к ``ModuleEntry``.

    Исходный тип и API ``common_modules`` не изменяются.
    """
    return ModuleEntry(
        module_kind="common_module",
        owner_kind="common_module",
        metadata_type="CommonModule",
        owner_name=entry.name,
        relative_path=entry.bsl_path.as_posix(),
        read_status=read_status,
        size_bytes=size_bytes,
        sha256=sha256,
    )
