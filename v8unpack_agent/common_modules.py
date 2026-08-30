"""Обнаружение и чтение общих модулей конфигурации 1С."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CommonModuleReadStatus = Literal["ok", "empty", "missing", "read_error"]

_COMMON_MODULE_DIR = "CommonModule"
_COMMON_MODULE_BSL = "CommonModule.obj.bsl"


@dataclass(frozen=True)
class CommonModuleEntry:
    """Указатель на общий модуль в распакованной выгрузке."""

    name: str
    bsl_path: Path


@dataclass
class CommonModuleIndex:
    """Детерминированный индекс общих модулей."""

    modules: list[CommonModuleEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Количество обнаруженных объектов CommonModule."""
        return len(self.modules)


@dataclass(frozen=True)
class CommonModuleContext:
    """Материализованный BSL-контекст общего модуля."""

    name: str
    bsl_text: str | None
    read_status: CommonModuleReadStatus
    metadata: dict[str, Any]


def _validated_root(root: Path) -> Path:
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    return root


def _relative_path(path: Path) -> Path:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("CommonModule path must be relative to export root")
    return path


def scan_common_modules(root: Path) -> CommonModuleIndex:
    """Построить индекс общих модулей распакованной выгрузки."""
    export_root = _validated_root(Path(root))
    container = export_root / _COMMON_MODULE_DIR

    if not container.exists():
        return CommonModuleIndex()

    if not container.is_dir():
        raise NotADirectoryError(container)

    modules = [
        CommonModuleEntry(
            name=object_dir.name,
            bsl_path=Path(
                _COMMON_MODULE_DIR,
                object_dir.name,
                _COMMON_MODULE_BSL,
            ),
        )
        for object_dir in container.iterdir()
        if object_dir.is_dir()
    ]

    modules.sort(
        key=lambda entry: (
            entry.bsl_path.as_posix().casefold(),
            entry.bsl_path.as_posix(),
        )
    )
    return CommonModuleIndex(modules=modules)


def build_common_module_context(
    entry: CommonModuleEntry,
    root: Path,
) -> CommonModuleContext:
    """Прочитать BSL общего модуля строго как UTF-8."""
    export_root = _validated_root(Path(root))
    relative_bsl_path = _relative_path(Path(entry.bsl_path))
    absolute_bsl_path = export_root / relative_bsl_path

    try:
        bsl_text = absolute_bsl_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        bsl_text = None
        read_status: CommonModuleReadStatus = "missing"
    except (UnicodeDecodeError, OSError):
        bsl_text = None
        read_status = "read_error"
    else:
        read_status = "empty" if bsl_text == "" else "ok"

    return CommonModuleContext(
        name=entry.name,
        bsl_text=bsl_text,
        read_status=read_status,
        metadata={"bsl_path": relative_bsl_path.as_posix()},
    )
