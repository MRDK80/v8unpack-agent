"""Production runner пайплайна обработки распакованной выгрузки (issue #198).

Модуль — чистая библиотека: он не разбирает аргументы командной строки, не
пишет отчёт на диск и не завершает процесс. Владение exit code и запись
отчёта относятся к :mod:`v8unpack_agent.cli`.

Состав прогона
--------------

1. ``scan_forms`` — обнаружение форм и предупреждения сканера.
2. ``build_form_context`` — единственная композитная обработка формы; внутри
   уже выполняются разбор ``.elem.json`` и декодирование атрибутов объекта,
   поэтому runner не вызывает их повторно.
3. ``parse_elem_json`` — только как классификатор результата, чтобы отличить
   ``complete`` от ``partial`` по признаку ``elem_index_ok``.
4. ``scan_common_modules`` и ``build_common_module_context`` — общие модули.
5. ``extract_all_skd_queries`` — артефакты СКД.

Границы деградации
------------------

Ошибка отдельного объекта не прерывает прогон и попадает в отчёт как
``partial`` либо ``failed``. Управляемая фатальная ошибка стадии обнаружения
даёт ``completed=false`` и ``fatal_error``. Отсутствие ``object_attributes``,
owner-уровня и BSL у elem-only формы деградацией не считается.

Пропуск группы объектов (``include_skd``/``include_common_modules``) не
создаёт результатов: обнаружение не выполняется, поэтому в отчёте таких
объектов нет.

Переносимость
-------------

Пути строятся только через :mod:`pathlib`, абсолютные пути и разделители
конкретной ОС в отчёт не попадают. Идентификатор формы берётся из
``FormContext.metadata['form_path']`` — уже относительного posix-пути.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from v8unpack_agent.common_modules import (
    build_common_module_context,
    scan_common_modules,
)
from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.run_report import (
    ObjectRunResult,
    PostRunReport,
    RunFatalError,
    RunObjectKind,
    RunObjectStatus,
    RunReportValidationError,
    RunSummary,
    common_module_status,
    scan_warning_reason_code,
    skd_status,
    unindexed_reason_code,
)
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms
from v8unpack_agent.skd_extractor import extract_all_skd_queries

__all__ = [
    "SCHEMA_VERSION",
    "RunOptions",
    "RunOutcome",
    "run_pipeline",
]

SCHEMA_VERSION = 1

STAGE_SCAN = "scan"
STAGE_BUILD_CONTEXT = "build_context"
STAGE_PARSE_ELEM = "parse_elem"
STAGE_COMMON_MODULES = "common_modules"
STAGE_SKD = "skd"

REASON_CONTEXT_BUILD_ERROR = "context_build_error"
REASON_PROMPT_BUILD_ERROR = "prompt_build_error"
REASON_ELEM_INDEX_UNAVAILABLE = "elem_index_unavailable"
REASON_SCAN_WARNING_UNCLASSIFIED = "scan_warning_unclassified"
REASON_COMMON_MODULE_CONTEXT_ERROR = "common_module_context_error"
REASON_COMMON_MODULE_UNCLASSIFIED = "common_module_unclassified"
REASON_SKD_UNCLASSIFIED = "skd_unclassified"

FATAL_SCAN_FAILED = "scan_failed"
FATAL_COMMON_MODULES_FAILED = "common_modules_failed"
FATAL_SKD_FAILED = "skd_failed"
FATAL_ERROR_TYPE_FALLBACK = "runtime_error"

_MACHINE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_CODE_CHARS_RE = re.compile(r"[^a-z0-9_]+")
_UNKNOWN_OBJECT = "unknown_object"
_MESSAGE_LIMIT = 180


@dataclass(frozen=True)
class RunOptions:
    """Параметры одного прогона пайплайна.

    Attributes
    ----------
    export_root:
        Корень уже распакованной выгрузки. Распаковка в задачу не входит.
    mode:
        Режим сканирования форм: конфигурация или внешние обработки.
    include_common_modules:
        ``False`` полностью отключает обнаружение общих модулей.
    include_skd:
        ``False`` полностью отключает извлечение артефактов СКД.
    max_prompt_chars:
        Лимит длины промпт-фрагмента; ``-1`` — без ограничения.
    """

    export_root: Path
    mode: Literal["config", "external"] = "config"
    include_common_modules: bool = True
    include_skd: bool = True
    max_prompt_chars: int = -1


@dataclass(frozen=True)
class RunOutcome:
    """Результат прогона без побочных эффектов.

    Attributes
    ----------
    report:
        Готовый :class:`~v8unpack_agent.run_report.PostRunReport`.
    scan_warnings:
        Предупреждения уровня прогона: они не привязаны к объекту и в отчёт
        не попадают, но доступны вызывающему коду для журналирования.
    prompt_chars:
        Суммарная длина собранных промпт-фрагментов.
    """

    report: PostRunReport
    scan_warnings: tuple[str, ...] = ()
    prompt_chars: int = 0

    @property
    def completed(self) -> bool:
        """Прогон завершён управляемо, фатальной ошибки нет."""
        return self.report.completed

    @property
    def degraded(self) -> bool:
        """Есть хотя бы один ``partial`` или ``failed`` объект."""
        summary = self.report.summary
        return bool(summary.partial or summary.failed)


def run_pipeline(options: RunOptions) -> RunOutcome:
    """Выполнить полный прогон и вернуть отчёт без записи на диск.

    Функция не бросает исключений пайплайна: управляемая фатальная ошибка
    превращается в ``fatal_error`` отчёта, а ошибка отдельного объекта — в
    ``failed``/``partial`` результат.
    """
    started_at = _utc_now()
    export_root = Path(options.export_root)

    objects: list[ObjectRunResult] = []
    scan_warnings: list[str] = []
    prompt_chars = 0
    fatal: RunFatalError | None = None

    try:
        index = scan_forms(
            export_root,
            mode=options.mode,
            include_elem_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        fatal = _fatal_error(FATAL_SCAN_FAILED, exc)
    else:
        scan_warnings.extend(index.scan_warnings)
        for entry in index.forms:
            result, fragment_chars = _process_form(entry, export_root, index, options)
            objects.append(result)
            prompt_chars += fragment_chars

        if fatal is None and options.include_common_modules:
            fatal = _process_common_modules(export_root, objects)

        if fatal is None and options.include_skd:
            fatal = _process_skd(export_root, objects)

    ordered = tuple(objects)
    report = PostRunReport(
        schema_version=SCHEMA_VERSION,
        completed=fatal is None,
        started_at=started_at,
        finished_at=_utc_now(),
        summary=RunSummary.from_objects(ordered),
        objects=ordered,
        fatal_error=fatal,
    )
    return RunOutcome(
        report=report,
        scan_warnings=tuple(scan_warnings),
        prompt_chars=prompt_chars,
    )


def _process_form(
    entry: FormEntry,
    export_root: Path,
    index: FormScanIndex,
    options: RunOptions,
) -> tuple[ObjectRunResult, int]:
    """Обработать одну форму и вернуть результат с длиной фрагмента."""
    object_id = _fallback_form_id(entry, export_root)

    try:
        context = build_form_context(
            entry,
            export_root,
            type_resolver=index.resolve_reference_type,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            _object_result(
                object_id,
                RunObjectKind.FORM,
                RunObjectStatus.FAILED,
                stage=STAGE_BUILD_CONTEXT,
                reason_code=REASON_CONTEXT_BUILD_ERROR,
                error=exc,
            ),
            0,
        )

    object_id = _context_form_id(context, object_id)

    try:
        fragment = to_llm_prompt_fragment(context, options.max_prompt_chars)
    except Exception as exc:  # noqa: BLE001
        return (
            _object_result(
                object_id,
                RunObjectKind.FORM,
                RunObjectStatus.FAILED,
                stage=STAGE_BUILD_CONTEXT,
                reason_code=REASON_PROMPT_BUILD_ERROR,
                error=exc,
            ),
            0,
        )

    fragment_chars = len(fragment)
    elem_index_ok, elem_reason_code = _probe_elem_index(context, export_root)

    if not elem_index_ok:
        return (
            _object_result(
                object_id,
                RunObjectKind.FORM,
                RunObjectStatus.PARTIAL,
                stage=STAGE_PARSE_ELEM,
                reason_code=elem_reason_code,
            ),
            fragment_chars,
        )

    warnings = [item for item in (entry.warnings or []) if item]
    if warnings:
        return (
            _object_result(
                object_id,
                RunObjectKind.FORM,
                RunObjectStatus.PARTIAL,
                stage=STAGE_SCAN,
                reason_code=_scan_reason_code(warnings[0]),
            ),
            fragment_chars,
        )

    return (
        _object_result(object_id, RunObjectKind.FORM, RunObjectStatus.COMPLETE),
        fragment_chars,
    )


def _process_common_modules(
    export_root: Path,
    objects: list[ObjectRunResult],
) -> RunFatalError | None:
    """Обработать общие модули; вернуть фатальную ошибку обнаружения."""
    try:
        index = scan_common_modules(export_root)
    except Exception as exc:  # noqa: BLE001
        return _fatal_error(FATAL_COMMON_MODULES_FAILED, exc)

    for entry in index.modules:
        object_id = _safe_object_id(Path(entry.bsl_path).as_posix())
        try:
            context = build_common_module_context(entry, export_root)
        except Exception as exc:  # noqa: BLE001
            objects.append(
                _object_result(
                    object_id,
                    RunObjectKind.COMMON_MODULE,
                    RunObjectStatus.FAILED,
                    stage=STAGE_COMMON_MODULES,
                    reason_code=REASON_COMMON_MODULE_CONTEXT_ERROR,
                    error=exc,
                )
            )
            continue

        status, reason_code = common_module_status(context.read_status)
        if status is RunObjectStatus.COMPLETE:
            objects.append(
                _object_result(object_id, RunObjectKind.COMMON_MODULE, status)
            )
            continue

        objects.append(
            _object_result(
                object_id,
                RunObjectKind.COMMON_MODULE,
                status,
                stage=STAGE_COMMON_MODULES,
                reason_code=_machine_code(
                    reason_code,
                    REASON_COMMON_MODULE_UNCLASSIFIED,
                ),
            )
        )

    return None


def _process_skd(
    export_root: Path,
    objects: list[ObjectRunResult],
) -> RunFatalError | None:
    """Обработать артефакты СКД; вернуть фатальную ошибку обнаружения.

    ``SkdResult`` не несёт ни имени, ни пути, поэтому идентификатор строится
    как порядковый номер позиции в ``SkdBatchResult.results``.
    """
    try:
        batch = extract_all_skd_queries(export_root)
    except Exception as exc:  # noqa: BLE001
        return _fatal_error(FATAL_SKD_FAILED, exc)

    for position, result in enumerate(batch.results, start=1):
        object_id = f"skd_artifact_{position:03d}"
        status, reason_code = skd_status(
            skd_extracted=result.skd_extracted,
            has_warnings=bool(result.warnings),
        )
        if status is RunObjectStatus.COMPLETE:
            objects.append(
                _object_result(object_id, RunObjectKind.SKD_ARTIFACT, status)
            )
            continue

        objects.append(
            _object_result(
                object_id,
                RunObjectKind.SKD_ARTIFACT,
                status,
                stage=STAGE_SKD,
                reason_code=_machine_code(reason_code, REASON_SKD_UNCLASSIFIED),
            )
        )

    return None


def _probe_elem_index(context: FormContext, export_root: Path) -> tuple[bool, str]:
    """Определить состояние elem-индекса формы.

    ``FormSummary`` не публикует признак индексации, поэтому классификация
    выполняется отдельным обращением к ``parse_elem_json``. Вызов защищён:
    любая ошибка означает недоступный индекс, а не отказ прогона.
    """
    relative = context.metadata.get("form_path")
    if not isinstance(relative, str) or not relative:
        return False, REASON_ELEM_INDEX_UNAVAILABLE

    form_dir = export_root / PurePosixPath(relative)
    if not form_dir.is_dir():
        return False, REASON_ELEM_INDEX_UNAVAILABLE

    try:
        result = parse_elem_json(form_dir)
    except Exception:  # noqa: BLE001
        return False, REASON_ELEM_INDEX_UNAVAILABLE

    if bool(getattr(result, "elem_index_ok", False)):
        return True, ""

    return False, _elem_reason_code(result)


def _elem_reason_code(result: object) -> str:
    """Получить машинный код причины неиндексации."""
    reason = getattr(result, "unindexed_reason", None)
    if reason is None:
        return REASON_ELEM_INDEX_UNAVAILABLE
    try:
        code = unindexed_reason_code(reason)
    except Exception:  # noqa: BLE001
        return REASON_ELEM_INDEX_UNAVAILABLE
    return _machine_code(code, REASON_ELEM_INDEX_UNAVAILABLE)


def _scan_reason_code(warning: str) -> str:
    """Привести код предупреждения сканера к машинному виду отчёта.

    ``scan_forms`` публикует коды в верхнем регистре как часть отображаемого
    формата предупреждения, а отчёт требует нижний регистр. Нормализация
    выполняется только для кода, распознанного по whitelist сканера; текст
    предупреждения к нижнему регистру не приводится.
    """
    code = scan_warning_reason_code(warning)
    if code is None:
        return REASON_SCAN_WARNING_UNCLASSIFIED
    return _machine_code(code.lower(), REASON_SCAN_WARNING_UNCLASSIFIED)


def _machine_code(value: str | None, fallback: str) -> str:
    """Вернуть значение, только если это корректный машинный код."""
    if value is None:
        return fallback
    return value if _MACHINE_CODE_RE.fullmatch(value) else fallback


def _object_result(
    object_id: str,
    object_kind: RunObjectKind,
    status: RunObjectStatus,
    *,
    stage: str | None = None,
    reason_code: str | None = None,
    error: BaseException | None = None,
) -> ObjectRunResult:
    """Собрать результат объекта, не допуская отказа валидации отчёта.

    Если сообщение не проходит проверку безопасности, результат создаётся без
    сообщения: диагностическая подробность менее важна, чем целостность
    отчёта.
    """
    safe_id = _safe_object_id(object_id)
    message = _safe_message(error) if error is not None else None

    if message is not None:
        try:
            return ObjectRunResult(
                object=safe_id,
                object_kind=object_kind,
                status=status,
                stage=stage,
                reason_code=reason_code,
                message=message,
            )
        except RunReportValidationError:
            pass

    try:
        return ObjectRunResult(
            object=safe_id,
            object_kind=object_kind,
            status=status,
            stage=stage,
            reason_code=reason_code,
        )
    except RunReportValidationError:
        return ObjectRunResult(
            object=_UNKNOWN_OBJECT,
            object_kind=object_kind,
            status=status,
            stage=stage,
            reason_code=reason_code,
        )


def _fatal_error(reason_code: str, error: BaseException) -> RunFatalError:
    """Собрать управляемую фатальную ошибку прогона."""
    error_type = _error_type(error)
    message = _safe_message(error)

    if message is not None:
        try:
            return RunFatalError(
                reason_code=reason_code,
                error_type=error_type,
                message=message,
            )
        except RunReportValidationError:
            pass

    return RunFatalError(reason_code=reason_code, error_type=error_type)


def _error_type(error: BaseException) -> str:
    """Преобразовать имя класса исключения в машинный код."""
    name = _CAMEL_BOUNDARY_RE.sub("_", type(error).__name__).lower()
    normalized = _NON_CODE_CHARS_RE.sub("_", name).strip("_")
    return _machine_code(normalized, FATAL_ERROR_TYPE_FALLBACK)


def _safe_message(error: BaseException | None) -> str | None:
    """Свести текст исключения к одной короткой строке."""
    if error is None:
        return None
    text = " ".join(str(error).split())
    if not text:
        text = type(error).__name__
    trimmed = text[:_MESSAGE_LIMIT].strip()
    return trimmed or None


def _safe_object_id(value: str) -> str:
    """Привести идентификатор объекта к относительному posix-виду.

    Корневой префикс и разделители конкретной ОС удаляются, чтобы значение
    прошло проверку безопасности отчёта на любой целевой платформе.
    """
    text = " ".join(str(value).split())
    if not text:
        return _UNKNOWN_OBJECT

    pure = PureWindowsPath(text)
    if pure.anchor:
        pure = pure.relative_to(pure.anchor)

    parts = [part for part in pure.parts if part not in {".", ".."}]
    if not parts:
        return _UNKNOWN_OBJECT

    return PurePosixPath(*parts).as_posix()


def _fallback_form_id(entry: FormEntry, export_root: Path) -> str:
    """Построить идентификатор формы, когда контекст ещё не собран."""
    form_path = Path(entry.form_path)
    for base in (export_root, export_root.resolve()):
        for candidate in (form_path, _resolved(form_path)):
            try:
                return _safe_object_id(candidate.relative_to(base).as_posix())
            except ValueError:
                continue

    name = entry.form_name or form_path.name
    return _safe_object_id(name)


def _context_form_id(context: FormContext, fallback: str) -> str:
    """Взять идентификатор формы из метаданных контекста."""
    relative = context.metadata.get("form_path")
    if isinstance(relative, str) and relative:
        return _safe_object_id(relative)
    return fallback


def _resolved(path: Path) -> Path:
    """Вернуть разрешённый путь, не падая на недоступном пути."""
    try:
        return path.resolve()
    except OSError:
        return path


def _utc_now() -> str:
    """Текущее время UTC в формате отчёта."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return f"{now.isoformat(timespec='microseconds')}Z"
