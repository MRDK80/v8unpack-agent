"""Модель и атомарная запись post-run report (issue #196)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v8unpack_agent.common_modules import CommonModuleReadStatus
    from v8unpack_agent.elem_parser import UnindexedReason
    from v8unpack_agent.object_decoder import DecodeError

_MACHINE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WINDOWS_DRIVE_RE = re.compile(r"(?:^|\s)[A-Za-z]:[\\/]")
_TRACEBACK_MARKERS = ("Traceback (most recent call last)", "File \"")


class RunReportValidationError(ValueError):
    """Нарушен контракт post-run report."""


class RunReportWriteError(OSError):
    """Обязательный post-run report не удалось записать."""


class RunObjectStatus(str, Enum):
    """Итоговый статус единицы обработки."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    EXCLUDED = "excluded"


class RunObjectKind(str, Enum):
    """Поддерживаемые виды единиц обработки."""

    FORM = "form"
    COMMON_MODULE = "common_module"
    SKD_ARTIFACT = "skd_artifact"


def _validate_machine_code(value: str, field_name: str) -> None:
    if not _MACHINE_CODE_RE.fullmatch(value):
        raise RunReportValidationError(
            f"{field_name} must be a lowercase machine code"
        )


def _validate_safe_text(value: str, field_name: str) -> None:
    if "\n" in value or "\r" in value:
        raise RunReportValidationError(f"{field_name} must be single-line")
    if any(marker in value for marker in _TRACEBACK_MARKERS):
        raise RunReportValidationError(f"{field_name} contains traceback data")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise RunReportValidationError(f"{field_name} contains an absolute path")
    if _WINDOWS_DRIVE_RE.search(value) or "\\\\" in value:
        raise RunReportValidationError(f"{field_name} contains an absolute path")


def _validate_timestamp(value: str, field_name: str) -> datetime:
    if not value.endswith("Z"):
        raise RunReportValidationError(f"{field_name} must be UTC and end with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RunReportValidationError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc
    return parsed


@dataclass(frozen=True)
class ObjectRunResult:
    """Результат обработки одного логического объекта."""

    object: str
    object_kind: RunObjectKind
    status: RunObjectStatus
    stage: str | None = None
    reason_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.object:
            raise RunReportValidationError("object must not be empty")
        _validate_safe_text(self.object, "object")

        if self.status in {RunObjectStatus.PARTIAL, RunObjectStatus.FAILED}:
            if self.stage is None or self.reason_code is None:
                raise RunReportValidationError(
                    "partial and failed results require stage and reason_code"
                )
        elif self.status is RunObjectStatus.COMPLETE:
            if any((self.stage, self.reason_code, self.message)):
                raise RunReportValidationError(
                    "complete result must not contain degradation details"
                )
        elif self.reason_code is None:
            raise RunReportValidationError(
                "excluded result requires reason_code"
            )

        if self.stage is not None:
            _validate_machine_code(self.stage, "stage")
        if self.reason_code is not None:
            _validate_machine_code(self.reason_code, "reason_code")
        if self.message is not None:
            _validate_safe_text(self.message, "message")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "object": self.object,
            "object_kind": self.object_kind.value,
            "status": self.status.value,
        }
        if self.stage is not None:
            result["stage"] = self.stage
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        if self.message is not None:
            result["message"] = self.message
        return result


@dataclass(frozen=True)
class RunSummary:
    """Счётчики результатов с проверяемым инвариантом."""

    found: int
    complete: int
    partial: int
    failed: int
    excluded: int = 0

    def __post_init__(self) -> None:
        values = (
            self.found,
            self.complete,
            self.partial,
            self.failed,
            self.excluded,
        )
        if any(value < 0 for value in values):
            raise RunReportValidationError("summary counters must be non-negative")
        if self.found != self.complete + self.partial + self.failed:
            raise RunReportValidationError(
                "found must equal complete + partial + failed"
            )

    @property
    def discovered(self) -> int:
        """Число найденных и сознательно исключённых объектов."""

        return self.found + self.excluded

    @classmethod
    def from_objects(cls, objects: tuple[ObjectRunResult, ...]) -> RunSummary:
        counts = {status: 0 for status in RunObjectStatus}
        for item in objects:
            counts[item.status] += 1
        complete = counts[RunObjectStatus.COMPLETE]
        partial = counts[RunObjectStatus.PARTIAL]
        failed = counts[RunObjectStatus.FAILED]
        return cls(
            found=complete + partial + failed,
            complete=complete,
            partial=partial,
            failed=failed,
            excluded=counts[RunObjectStatus.EXCLUDED],
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "found": self.found,
            "complete": self.complete,
            "partial": self.partial,
            "failed": self.failed,
            "excluded": self.excluded,
            "discovered": self.discovered,
        }


@dataclass(frozen=True)
class RunFatalError:
    """Санитизированная ошибка управляемого запуска."""

    reason_code: str
    error_type: str
    message: str | None = None

    def __post_init__(self) -> None:
        _validate_machine_code(self.reason_code, "reason_code")
        _validate_machine_code(self.error_type, "error_type")
        if self.message is not None:
            _validate_safe_text(self.message, "message")

    def to_dict(self) -> dict[str, str]:
        result = {
            "reason_code": self.reason_code,
            "error_type": self.error_type,
        }
        if self.message is not None:
            result["message"] = self.message
        return result


@dataclass(frozen=True)
class PostRunReport:
    """Единый источник машиночитаемого итогового отчёта."""

    schema_version: int
    completed: bool
    started_at: str
    finished_at: str
    summary: RunSummary
    objects: tuple[ObjectRunResult, ...]
    fatal_error: RunFatalError | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise RunReportValidationError("unsupported schema_version")
        started = _validate_timestamp(self.started_at, "started_at")
        finished = _validate_timestamp(self.finished_at, "finished_at")
        if finished < started:
            raise RunReportValidationError("finished_at precedes started_at")
        if self.completed and self.fatal_error is not None:
            raise RunReportValidationError(
                "completed report must not contain fatal_error"
            )
        if not self.completed and self.fatal_error is None:
            raise RunReportValidationError(
                "incomplete report requires fatal_error"
            )

        ordered = tuple(
            sorted(
                self.objects,
                key=lambda item: (
                    item.object_kind.value,
                    item.object,
                    item.status.value,
                    item.stage or "",
                    item.reason_code or "",
                ),
            )
        )
        object.__setattr__(self, "objects", ordered)
        if self.summary != RunSummary.from_objects(ordered):
            raise RunReportValidationError(
                "summary counters do not match object results"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run": {
                "completed": self.completed,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            },
            "summary": self.summary.to_dict(),
            "objects": [item.to_dict() for item in self.objects],
            "fatal_error": (
                None if self.fatal_error is None else self.fatal_error.to_dict()
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"


def unindexed_reason_code(reason: UnindexedReason) -> str:
    """Переиспользовать значение существующего UnindexedReason."""

    value = reason.value
    if not isinstance(value, str):
        raise TypeError("UnindexedReason value must be str")
    return value


def decode_error_reason_code(error: DecodeError) -> str:
    """Переиспользовать значение существующего DecodeError."""

    value = error.value
    if not isinstance(value, str):
        raise TypeError("DecodeError value must be str")
    return value


def scan_warning_reason_code(warning: str) -> str | None:
    """Делегировать разбор канонической функции scan_warning_code()."""

    from v8unpack_agent.scan_forms import scan_warning_code

    return scan_warning_code(warning)


def common_module_status(
    read_status: CommonModuleReadStatus,
) -> tuple[RunObjectStatus, str | None]:
    """Отобразить существующий статус чтения CommonModule."""

    mapping: dict[str, tuple[RunObjectStatus, str | None]] = {
        "ok": (RunObjectStatus.COMPLETE, None),
        "empty": (RunObjectStatus.PARTIAL, "empty_bsl"),
        "missing": (RunObjectStatus.FAILED, "missing_bsl"),
        "read_error": (RunObjectStatus.FAILED, "bsl_read_error"),
    }
    try:
        return mapping[read_status]
    except KeyError as exc:
        raise ValueError(f"unsupported CommonModule read status: {read_status}") from exc


def skd_status(
    skd_extracted: bool,
    *,
    has_warnings: bool,
) -> tuple[RunObjectStatus, str | None]:
    """Отобразить доказанные поля SkdBatchResult без нового status enum."""

    if not skd_extracted:
        return RunObjectStatus.FAILED, "skd_not_extracted"
    if has_warnings:
        return RunObjectStatus.PARTIAL, "skd_warning"
    return RunObjectStatus.COMPLETE, None


def write_post_run_report(report: PostRunReport, target: Path) -> None:
    """Атомарно записать JSON рядом с target, сохраняя старый файл при сбое."""

    target = Path(target)
    parent = target.parent
    if not parent.exists() or not parent.is_dir():
        raise RunReportWriteError(
            f"report parent directory does not exist: {parent.name}"
        )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(report.to_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    except Exception as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RunReportWriteError(
            f"failed to write post-run report: {target.name}"
        ) from exc
