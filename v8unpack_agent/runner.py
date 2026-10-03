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
6. ``scan_*_modules`` (issue #208) — опциональный индекс BSL-модулей
   конфигурации, объектов, наборов записей, команд, констант и сервисов;
   по умолчанию выключен (``include_module_index``).

Границы деградации
------------------

Ошибка отдельного объекта не прерывает прогон и попадает в отчёт как
``partial`` либо ``failed``. Управляемая фатальная ошибка стадии обнаружения
даёт ``completed=false`` и ``fatal_error``. Отсутствие ``object_attributes``,
owner-уровня и BSL у elem-only формы деградацией не считается.

Для индекса модулей (issue #208) деградацию даёт только
``read_error``. Состояния ``empty``, ``whitespace_only`` и ``missing`` — допустимые
состояния выгрузки: модуль учтён в отчёте со статусом ``excluded``
(текста для LLM нет) и не меняют exit code. Сбой сканера или дубль
идентификатора модуля — фатальная ошибка ``modules_failed``.

Пропуск группы объектов (``include_skd``/``include_common_modules``) не
создаёт результатов: обнаружение не выполняется, поэтому в отчёте таких
объектов нет.

Переносимость
-------------

Пути строятся только через :mod:`pathlib`, абсолютные пути и разделители
конкретной ОС в отчёт не попадают. Идентификатор формы берётся из
``FormContext.metadata['form_path']`` — уже относительного posix-пути.

Граница санитизации (issue #142)
--------------------------------

Сообщения исключений и ``RunOutcome.scan_warnings`` проходят через
:func:`~v8unpack_agent._safe_paths.sanitize_diagnostic`. Непредвиденное
исключение в любой точке прогона, в том числе после формирования основных
полей, не выходит из :func:`run_pipeline`: оно становится
``fatal_error`` с кодом ``internal_error`` и санитизированным сообщением.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from v8unpack_agent._safe_paths import sanitize_diagnostic
from v8unpack_agent.command_modules import scan_command_modules
from v8unpack_agent.common_modules import (
    build_common_module_context,
    scan_common_modules,
)
from v8unpack_agent.configuration_modules import scan_configuration_modules
from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.metadata_modules import scan_metadata_object_modules
from v8unpack_agent.modules import ModuleEntry, ModuleIndex
from v8unpack_agent.record_set_modules import scan_record_set_modules
from v8unpack_agent.run_report import (
    ModuleStatusTable,
    ObjectRunResult,
    PostRunReport,
    RunFatalError,
    RunObjectKind,
    RunObjectStatus,
    RunReportValidationError,
    RunSummary,
    common_module_status,
    module_object_kind,
    module_read_status,
    scan_warning_reason_code,
    skd_status,
    unindexed_reason_code,
)
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms
from v8unpack_agent.service_modules import scan_service_modules
from v8unpack_agent.skd_extractor import extract_all_skd_queries
from v8unpack_agent.value_manager_modules import scan_value_manager_modules

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
STAGE_MODULES = "modules"

REASON_CONTEXT_BUILD_ERROR = "context_build_error"
REASON_PROMPT_BUILD_ERROR = "prompt_build_error"
REASON_ELEM_INDEX_UNAVAILABLE = "elem_index_unavailable"
REASON_SCAN_WARNING_UNCLASSIFIED = "scan_warning_unclassified"
REASON_COMMON_MODULE_CONTEXT_ERROR = "common_module_context_error"
REASON_COMMON_MODULE_UNCLASSIFIED = "common_module_unclassified"
REASON_SKD_UNCLASSIFIED = "skd_unclassified"
REASON_MODULE_UNCLASSIFIED = "module_unclassified"

FATAL_SCAN_FAILED = "scan_failed"
FATAL_COMMON_MODULES_FAILED = "common_modules_failed"
FATAL_SKD_FAILED = "skd_failed"
FATAL_MODULES_FAILED = "modules_failed"
FATAL_INTERNAL_ERROR = "internal_error"
FATAL_ERROR_TYPE_FALLBACK = "runtime_error"

_MACHINE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_CODE_CHARS_RE = re.compile(r"[^a-z0-9_]+")
_UNKNOWN_OBJECT = "unknown_object"
_MESSAGE_LIMIT = 180
_COMMON_MODULE_CONTAINER = "CommonModule"
_COMMON_MODULE_BSL_NAME = "CommonModule.obj.bsl"


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
    include_module_index:
        ``True`` включает индекс BSL-модулей (issue #208). По умолчанию
        выключен: прежние отчёты не меняются.
    """

    export_root: Path
    mode: Literal["config", "external"] = "config"
    include_common_modules: bool = True
    include_skd: bool = True
    max_prompt_chars: int = -1
    include_module_index: bool = False


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
    ``failed``/``partial`` результат. Непредвиденное исключение вне
    стадийных обработчиков даёт ``fatal_error`` с кодом ``internal_error``
    (issue #142): исходный текст и traceback наружу не выходят.
    """
    started_at = _utc_now()
    try:
        return _run_pipeline(options, started_at)
    except Exception as exc:  # noqa: BLE001 — внешняя fail-closed граница прогона
        return _internal_failure(started_at, exc)


def _internal_failure(started_at: str, error: BaseException) -> RunOutcome:
    """Собрать управляемый отчёт для непредвиденного исключения прогона."""
    report = PostRunReport(
        schema_version=SCHEMA_VERSION,
        completed=False,
        started_at=started_at,
        finished_at=_utc_now(),
        summary=RunSummary.from_objects(()),
        objects=(),
        fatal_error=_fatal_error(FATAL_INTERNAL_ERROR, error),
    )
    return RunOutcome(report=report)


def _run_pipeline(options: RunOptions, started_at: str) -> RunOutcome:
    """Тело прогона; исключения перехватывает :func:`run_pipeline`."""
    export_root = Path(options.export_root)

    objects: list[ObjectRunResult] = []
    scan_warnings: list[str] = []
    prompt_chars = 0
    fatal: RunFatalError | None = None
    module_table: ModuleStatusTable | None = None

    try:
        index = scan_forms(
            export_root,
            mode=options.mode,
            include_elem_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        fatal = _fatal_error(FATAL_SCAN_FAILED, exc)
    else:
        scan_warnings.extend(sanitize_diagnostic(w) for w in index.scan_warnings)
        for entry in index.forms:
            result, fragment_chars = _process_form(entry, export_root, index, options)
            objects.append(result)
            prompt_chars += fragment_chars

        if fatal is None and options.include_common_modules:
            fatal = _process_common_modules(export_root, objects)

        if fatal is None and options.include_module_index:
            fatal, module_table = _process_module_index(export_root, objects)

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
        modules=module_table,
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
        object_id = _common_module_object_id(entry.bsl_path)
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


def _module_scanners() -> tuple[Callable[[Path], Iterable[ModuleEntry]], ...]:
    """Сканеры индекса модулей; собираются при вызове, чтобы их можно было подменить."""
    return (
        scan_configuration_modules,
        scan_metadata_object_modules,
        scan_record_set_modules,
        scan_command_modules,
        scan_value_manager_modules,
        scan_service_modules,
    )


def _process_module_index(
    export_root: Path,
    objects: list[ObjectRunResult],
) -> tuple[RunFatalError | None, ModuleStatusTable | None]:
    """Обработать индекс BSL-модулей (issue #208).

    Записи всех сканеров собираются через :meth:`ModuleIndex.from_entries`:
    дубли ``module_id`` и ``relative_path`` между сканерами дают
    ``modules_failed``. Результаты добавляются в отчёт атомарно: при
    отказе любого сканера частичные записи не попадают, а возвращается
    фатальная ошибка. Таблица статусов возвращается только при успехе.
    """
    results: list[ObjectRunResult] = []
    try:
        entries: list[ModuleEntry] = []
        for scanner in _module_scanners():
            entries.extend(scanner(export_root))
        module_index = ModuleIndex.from_entries(entries)
        for entry in module_index:
            results.append(_module_result(entry))
    except Exception as exc:  # noqa: BLE001
        return _fatal_error(FATAL_MODULES_FAILED, exc), None

    objects.extend(results)
    return None, ModuleStatusTable.from_entries(module_index)


def _module_result(entry: ModuleEntry) -> ObjectRunResult:
    """Собрать результат одного BSL-модуля; идентификатор — относительный путь."""
    kind = module_object_kind(entry.module_kind)
    status, reason_code = module_read_status(entry.read_status)
    if status is RunObjectStatus.COMPLETE:
        return _object_result(entry.relative_path, kind, status)
    return _object_result(
        entry.relative_path,
        kind,
        status,
        stage=STAGE_MODULES,
        reason_code=_machine_code(reason_code, REASON_MODULE_UNCLASSIFIED),
    )


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
    """Свести санитизированный текст исключения к одной короткой строке."""
    if error is None:
        return None
    text = " ".join(sanitize_diagnostic(error).split())
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


def _common_module_object_id(bsl_path: object) -> str:
    """Идентификатор общего модуля без компонентов вне export root (issue #301).

    ``scan_common_modules`` хранит ``bsl_path`` относительно export root —
    такой путь используется как есть. Абсолютный путь в идентификатор не
    переносится: из него берётся только хвост доказанной раскладки
    ``CommonModule/<имя>/CommonModule.obj.bsl``. Если хвост раскладке не
    соответствует, возвращается ``unknown_object``: fail-closed важнее
    подробности. Разбор не зависит от разделителя текущей ОС.
    """
    text = str(bsl_path)
    if not (PurePosixPath(text).is_absolute() or PureWindowsPath(text).anchor):
        return _safe_object_id(Path(text).as_posix())
    segments = [segment for segment in re.split(r"[\\/]+", text) if segment]
    if (
        len(segments) >= 3
        and segments[-3] == _COMMON_MODULE_CONTAINER
        and segments[-1] == _COMMON_MODULE_BSL_NAME
    ):
        return _safe_object_id("/".join(segments[-3:]))
    return _UNKNOWN_OBJECT


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
