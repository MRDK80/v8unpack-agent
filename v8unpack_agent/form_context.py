"""Компактный LLM-готовый контекст формы (issue #77).

``FormEntry`` из :mod:`v8unpack_agent.scan_forms` — карточка указателей: она
знает, где лежат части формы, но не содержит их содержимого.
:class:`FormContext` материализует содержимое: прочитанный BSL-текст,
построенный :class:`~v8unpack_agent.form_summary.FormSummary`, компактные
метаданные, пригодные для вставки в промпт, а также (issue #NEW) реквизиты
и табличные части объекта метаданных за формой, разрешённые через
``object_decoder`` и ``catalog_resolver``.

Почему нужен ``unpacked_root``
---------------------------------

Семантика путей ``FormEntry`` смешанная (issue #57): ``form_path``,
``bsl_path`` и ``json_path`` абсолютные, а ``elem_json_path`` —
relative-to-root и может быть ``None`` (старые индексы). ``unpacked_root``
резолвит относительные пути, служит базой для обезличенных путей в
``metadata`` и вырезается из текстов предупреждений парсера.

Границы контракта
------------------

* второго пути разбора не вводится: структуру даёт единственный
  ``parse_elem_json``, выжимку — ``build_form_summary_from_elem_index``
  (та же композиция, что внутри ``build_form_summary``);
* отсутствие BSL — штатный ``None``, пустой файл — пустая строка;
* отсутствие ``*.elem.json`` обрабатывает сам ``FormSummary`` — пустые
  бакеты и ``warnings`` парсера;
* привязки здесь не создаются и не догадываются: отсутствующий файл
  никогда не превращается в выдуманные данные;
* то же правило действует и для объекта (issue #NEW): если
  ``object_json_path`` не находит файл объекта, ``object_attributes``
  остаётся ``None`` — это фиксируется предупреждением, а не подменяется
  пустой структурой, похожей на успех;
* ``to_llm_prompt_fragment`` физически не может вернуть больше
  ``max_chars`` символов: обрезка выполняется последним шагом и режет
  только хвост секции ``## BSL`` — секции ``## SUMMARY`` и
  ``## OBJECT_ATTRIBUTES`` идут раньше и в обрезку попадают только если
  сами по себе длиннее лимита.

Обезличенность предупреждений
--------------------------------

``parse_elem_json`` формирует часть своих предупреждений с абсолютным путём
каталога формы. Для диагностики локального запуска это полезно, но
``FormContext`` предназначен для промпта и отчётов, поэтому база
``unpacked_root`` из текстов вырезается: остаётся относительный путь.
Содержательная часть предупреждения не меняется и не теряется. То же
правило применяется к предупреждениям ``object_decoder``.

Явное отрицательное знание о ``data_path`` (issue #141)
------------------------------------------------------

Недоказанная привязка не угадывается и не остаётся молчаливым пропуском.
Каноническая структура ``FormContext.unresolved_data_paths`` хранит
``data_path: None`` вместе со стабильными ``status`` и ``reason``; LLM-проекция
выводит строковый маркер в той же строке, что и статус, и обрезка
``max_chars`` не может разделить маркер и статус. Доказанные ``data_path``
по-прежнему живут только в ``summary.relations`` и не меняются. Статус
``not_found`` зарезервирован за доказанным отсутствием целевой сущности;
отсутствие результата разбора таким доказательством не является.

Граница санитизации (issue #142)
--------------------------------

Диагностические части LLM-проекции — заголовок, JSON выжимки с
``warnings`` и строки отрицательного знания — проходят через
:func:`~v8unpack_agent._safe_paths.sanitize_diagnostic` до сборки и
обрезки фрагмента, поэтому лимит ``max_chars`` и атомарность строк #141
сохраняются. ``metadata['warnings']`` проходит ту же границу. Текст BSL и
реквизиты объекта — данные выгрузки, а не диагностика: они не меняются.
``FormContext.unresolved_data_paths`` остаётся канонической структурой без
изменений.

RAG-индексация (#78) и диспетчеризация (#79) в этот модуль не входят.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from v8unpack_agent._safe_paths import sanitize_diagnostic
from v8unpack_agent.catalog_resolver import object_json_path, resolve_data_path
from v8unpack_agent.coverage_metric import DATA_ELEMENT_TYPES
from v8unpack_agent.elem_parser import _find_elem_json, parse_elem_json
from v8unpack_agent.form_summary import (
    FormSummary,
    build_form_summary_from_elem_index,
    to_normalized_json,
)
from v8unpack_agent.object_decoder import decode_object_attributes

__all__ = [
    "FormContext",
    "build_form_context",
    "to_llm_prompt_fragment",
]

#: Маркеры секций фрагмента. Формат стабилен и тестируем.
SUMMARY_MARKER = "## SUMMARY"
OBJECT_ATTRIBUTES_MARKER = "## OBJECT_ATTRIBUTES"
BSL_MARKER = "## BSL"
#: Замена тела модуля, когда BSL отсутствует (elem-only форма).
NO_BSL_PLACEHOLDER = "(модуль формы отсутствует)"
#: Замена секции объекта, когда файл объекта не найден или не декодирован.
NO_OBJECT_PLACEHOLDER = "(реквизиты объекта не найдены)"

#: Резолвер ссылочных типов ``uuid -> имя типа`` (#88); ``None`` означает, что
#: тип неизвестен и значение остаётся ``Ref#<uuid>``. Алиас приватный: в
#: ``object_decoder`` тип задан inline, второй публичный контракт не вводится.
_TypeResolver = Callable[[str], str | None]

#: Статусы недоказанного ``data_path`` (issue #141). Набор конечный.
#: ``unresolved`` — привязка не доказана, причина в ``reason``;
#: ``unknown_layout`` — файл структуры есть, но раскладка не распознана;
#: ``not_found`` — отсутствие целевой сущности доказано. Ни одна текущая
#: диагностическая ветка такого доказательства не даёт, поэтому модуль
#: ``not_found`` не выдаёт: пустой результат разбора — не доказательство.
DATA_PATH_STATUS_UNRESOLVED = "unresolved"
DATA_PATH_STATUS_UNKNOWN_LAYOUT = "unknown_layout"
DATA_PATH_STATUS_NOT_FOUND = "not_found"
DATA_PATH_STATUSES: frozenset[str] = frozenset({
    DATA_PATH_STATUS_UNRESOLVED,
    DATA_PATH_STATUS_UNKNOWN_LAYOUT,
    DATA_PATH_STATUS_NOT_FOUND,
})

#: Стабильные коды причин деградации (issue #141) и их статусы.
DATA_PATH_REASON_FORM_DIR_MISSING = "form_dir_missing"
DATA_PATH_REASON_ELEM_JSON_MISSING = "elem_json_missing"
DATA_PATH_REASON_ELEM_JSON_INVALID = "elem_json_invalid"
DATA_PATH_REASON_LAYOUT_NOT_RECOGNIZED = "layout_not_recognized"
DATA_PATH_REASON_BINDING_NOT_PROVEN = "binding_not_proven"
DATA_PATH_REASONS: dict[str, str] = {
    DATA_PATH_REASON_FORM_DIR_MISSING: DATA_PATH_STATUS_UNRESOLVED,
    DATA_PATH_REASON_ELEM_JSON_MISSING: DATA_PATH_STATUS_UNRESOLVED,
    DATA_PATH_REASON_ELEM_JSON_INVALID: DATA_PATH_STATUS_UNRESOLVED,
    DATA_PATH_REASON_LAYOUT_NOT_RECOGNIZED: DATA_PATH_STATUS_UNKNOWN_LAYOUT,
    DATA_PATH_REASON_BINDING_NOT_PROVEN: DATA_PATH_STATUS_UNRESOLVED,
}

#: Видимые маркеры LLM-проекции. Стабильны и машинно различимы.
DATA_PATH_MARKERS: dict[str, str] = {
    DATA_PATH_STATUS_UNRESOLVED: "<UNRESOLVED: путь не доказан>",
    DATA_PATH_STATUS_UNKNOWN_LAYOUT: "<UNKNOWN_LAYOUT: путь не доказан>",
    DATA_PATH_STATUS_NOT_FOUND: "<NOT_FOUND: отсутствие доказано>",
}
#: Начало строки отрицательного знания в секции ``## SUMMARY``.
DATA_PATH_LINE_PREFIX = "data_path: "


class _FormEntryProtocol(Protocol):
    """Структурный контракт записи реестра форм (см. #191).

    Описывает только те поля, которые действительно читает этот модуль.
    Наследование не требуется: ``scan_forms.FormEntry`` и структурно
    совместимые test doubles подходят автоматически. Протокол проверяется
    только статически — runtime-валидации и импорта ``scan_forms`` здесь
    нет, поэтому контракт ленивых импортов (#140) не нарушается.
    """

    @property
    def bsl_path(self) -> Path: ...

    @property
    def bsl_sha256(self) -> str | None: ...

    @property
    def container_name(self) -> str: ...

    @property
    def elem_sha256(self) -> str | None: ...

    @property
    def form_name(self) -> str: ...

    @property
    def form_path(self) -> Path: ...

    @property
    def object_name(self) -> str: ...

    @property
    def object_type(self) -> str: ...

    @property
    def warnings(self) -> list[str]: ...


@dataclass(frozen=True)
class FormContext:
    """Материализованное содержимое одной формы.

    ``bsl_text``
        Содержимое модуля формы, прочитанное как UTF-8. ``None`` — файла
        нет (штатная ситуация для elem-only форм). Пустая строка отличается
        от ``None``: файл есть, но пуст.
    ``summary``
        Семантическая выжимка структуры формы с обезличенными warnings.
    ``metadata``
        Отобранные поля ``FormEntry`` без дублирования всей карточки;
        пути — только относительные posix-строки.
    ``object_attributes``
        Реквизиты и табличные части объекта метаданных за формой
        (issue #NEW), нормализованные ``object_decoder.decode_object_attributes``.
        ``None`` — файл объекта не найден или не декодирован; это отличается
        от пустой структуры и не подменяется на неё. Наличие результата
        проверяется напрямую через ``is not None`` — отдельный ключ в
        ``metadata`` для этого не вводится, чтобы не ломать существующий
        строгий контракт точного набора ключей ``metadata``.
    ``resolved_relations``
        Обогащение ``summary.relations`` (только ``kind == "data"``)
        через ``catalog_resolver.resolve_data_path``: тип и синоним
        реквизита, если он найден в файле объекта. Элементы, для которых
        резолюция не удалась, помечаются ``resolved=False`` и не отбрасываются.
    ``unresolved_data_paths``
        Явное отрицательное знание (issue #141): по записи на каждую
        недоказанную привязку. Ключи ``scope`` (``"form"`` или
        ``"element"``), ``element`` (имя элемента либо ``None``),
        ``data_path`` (всегда ``None``), ``status`` и ``reason``.
        Доказанные привязки сюда не попадают и остаются в
        ``summary.relations`` без изменений.

    Датакласс frozen, как и ``FormSummary``: подмена полей запрещена.
    Глубокой неизменяемости у ``metadata``/``object_attributes`` нет — это
    та же комбинация, что уже принята в ``FormSummary`` со списками.
    """

    form_name: str
    container_name: str
    object_type: str
    object_name: str
    bsl_text: str | None
    summary: FormSummary
    metadata: dict[str, Any]
    object_attributes: dict[str, Any] | None = None
    resolved_relations: list[dict[str, Any]] = field(default_factory=list)
    unresolved_data_paths: list[dict[str, Any]] = field(default_factory=list)


def build_form_context(
    form_entry: _FormEntryProtocol,
    unpacked_root: Path,
    *,
    type_resolver: _TypeResolver | None = None,
) -> FormContext:
    """Собрать :class:`FormContext` по карточке ``FormEntry``.

    Parameters
    ----------
    form_entry:
        Запись реестра форм (``scan_forms.FormEntry``).
    unpacked_root:
        Корень распакованной выгрузки. Им резолвятся относительные
        пути, вычисляются обезличенные пути для ``metadata`` и
        вырезается база из текстов предупреждения.
    type_resolver:
        Опциональный резолвер ссылочных типов ``uuid -> имя типа``
        (issue #147). Совместим с ``FormScanIndex.resolve_reference_type``
        (#88) и передаётся в ``object_decoder.decode_object_attributes``.
        Без него поведение прежнее: ссылка остаётся ``Ref#<uuid>``.
        Параметр keyword-only, поэтому существующие позиционные вызовы
        ``build_form_context(entry, root)`` не ломаются.

    Ни одна ветка не порождает данные, которых нет на диске.
    """
    root = Path(unpacked_root)

    bsl_path = _resolve(form_entry.bsl_path, root)
    bsl_text = _read_bsl(bsl_path)

    # Старые индексы форм могут не содержать это поле: толерантный доступ — часть контракта, а не долг.
    elem_json_path = getattr(form_entry, "elem_json_path", None)
    form_dir = _form_dir(form_entry, elem_json_path, root)
    summary, unresolved_data_paths = _build_summary(form_dir, root)

    object_attributes, object_warnings, object_json = _build_object_attributes(
        form_entry, root, type_resolver=type_resolver
    )
    resolved_relations = _resolve_relations(summary, object_json)

    metadata: dict[str, Any] = {
        "form_path": _relative_str(form_entry.form_path, root),
        "elem_json_path": _relative_str(elem_json_path, root),
        "bsl_sha256": form_entry.bsl_sha256,
        "elem_sha256": form_entry.elem_sha256,
        "has_bsl": bsl_text is not None,
        "warnings": [
            sanitize_diagnostic(_strip_root(str(item), root))
            for item in (form_entry.warnings or [])
        ]
        + [sanitize_diagnostic(_strip_root(item, root)) for item in object_warnings],
    }

    return FormContext(
        form_name=str(form_entry.form_name or ""),
        container_name=str(form_entry.container_name or ""),
        object_type=str(form_entry.object_type or ""),
        object_name=str(form_entry.object_name or ""),
        bsl_text=bsl_text,
        summary=summary,
        metadata=metadata,
        object_attributes=object_attributes,
        resolved_relations=resolved_relations,
        unresolved_data_paths=unresolved_data_paths,
    )


def _to_llm_prompt_fragment_chars(context: FormContext, max_chars: int = -1) -> str:
    """Компактное текстовое представление для вставки в промпт.

    Порядок фиксирован: заголовок формы, ``## SUMMARY``,
    ``## OBJECT_ATTRIBUTES``, затем ``## BSL``. Смысловая выжимка важнее
    кода, поэтому при жёстком лимите обрезается именно хвост BSL.

    ``max_chars=-1`` отключает обрезку и возвращает полный контекст.
    Нулевой и остальные отрицательные лимиты дают пустую строку.
    При положительном лимите результат детерминирован и всегда не длиннее
    ``max_chars``.

    Недоказанные ``data_path`` (issue #141) выводятся в секции
    ``## SUMMARY`` сразу после JSON выжимки — по одной строке на запись,
    маркер идёт первым, статус и причина — в той же строке. Строка
    атомарна относительно обрезки: если лимит попадает внутрь неё, она
    отбрасывается целиком. Без недоказанных привязок фрагмент совпадает
    с прежним форматом.
    """
    if max_chars == 0 or max_chars < -1:
        return ""

    header = "# FORM " + "/".join(
        part
        for part in (
            context.object_type,
            context.object_name,
            context.container_name,
            context.form_name,
        )
        if part
    )

    body = context.bsl_text if context.bsl_text is not None else NO_BSL_PLACEHOLDER

    if context.object_attributes is not None:
        object_block = _object_attributes_to_json(
            context.object_attributes, context.resolved_relations
        )
    else:
        object_block = NO_OBJECT_PLACEHOLDER

    # issue #142: диагностические части санитизируются до сборки и обрезки.
    header = sanitize_diagnostic(header)
    summary_block = sanitize_diagnostic(to_normalized_json(context.summary))
    status_lines = [
        sanitize_diagnostic(_data_path_status_line(entry))
        for entry in context.unresolved_data_paths
    ]

    fragment = "\n".join((
        header,
        SUMMARY_MARKER,
        summary_block,
        *status_lines,
        OBJECT_ATTRIBUTES_MARKER,
        object_block,
        BSL_MARKER,
        body,
    ))

    if max_chars == -1:
        return fragment

    # issue #141: обрезка не оставляет маркер без статуса — строка
    # отрицательного знания либо входит целиком, либо не входит вовсе.
    offset = len(header) + len(SUMMARY_MARKER) + len(summary_block) + 3
    for line in status_lines:
        end = offset + len(line)
        if offset < max_chars < end:
            return fragment[:offset]
        offset = end + 1
    return fragment[:max_chars]


def _data_path_status_line(entry: dict[str, Any]) -> str:
    """Строка LLM-проекции для одной записи отрицательного знания.

    Маркер стоит первым и в одной строке со ``status`` и ``reason``.
    Неизвестный статус не маскируется под доказанный: fail-closed
    выводится маркер ``unresolved``.
    """
    status = str(entry.get("status") or DATA_PATH_STATUS_UNRESOLVED)
    marker = DATA_PATH_MARKERS.get(
        status, DATA_PATH_MARKERS[DATA_PATH_STATUS_UNRESOLVED]
    )
    parts = [
        f'{DATA_PATH_LINE_PREFIX}"{marker}"',
        f"status: {status}",
        f"reason: {entry.get('reason')}",
        f"scope: {entry.get('scope')}",
    ]
    element = entry.get("element")
    if element is not None:
        parts.append("element: " + json.dumps(str(element), ensure_ascii=False))
    return "; ".join(parts)


def _object_attributes_to_json(
    object_attributes: dict[str, Any], resolved_relations: list[dict[str, Any]]
) -> str:
    """Детерминированное JSON-представление реквизитов объекта."""
    payload = {
        "Properties": object_attributes.get("Properties", []),
        "TabularSections": object_attributes.get("TabularSections", []),
        "ResolvedRelations": resolved_relations,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _build_object_attributes(
    form_entry: _FormEntryProtocol,
    root: Path,
    *,
    type_resolver: _TypeResolver | None = None,
) -> tuple[dict[str, Any] | None, list[str], Path | None]:
    """Найти и декодировать реквизиты объекта метаданных за формой.

    Best-effort, как и остальной модуль: отсутствие файла объекта или
    ошибка декодирования дают ``(None, [...], None)``, а не выдуманную
    структуру. ``type_resolver`` (issue #147) пробрасывается в декодер
    как есть: известный UUID превращается в читаемое имя типа,
    неизвестный остаётся безопасным ``Ref#<uuid>``. Догадок о типе
    метаданных модуль по-прежнему не делает и своего индекса не строит —
    источником имён служит вызывающий, обычно
    ``FormScanIndex.resolve_reference_type`` (#88).
    """
    object_json = object_json_path(form_entry)
    if object_json is None:
        # issue #172: отличаем «владельца нет по layout» от «файл не найден».
        # Читаем form_entry: в FormContext object_name нормализуется через
        # `or ""`, и None там уже не отличим от пустой строки.
        if form_entry.object_name == "":
            warning = "object_context: объект-владелец отсутствует по layout"
        else:
            warning = "object_context: файл объекта метаданных не найден"
        return None, [warning], None

    decode_result = decode_object_attributes(
        object_json,
        type_resolver=type_resolver,
    )
    warnings = list(decode_result.warnings)
    if not decode_result.ok:
        return None, warnings, object_json

    return decode_result.data, warnings, object_json


def _resolve_relations(
    summary: FormSummary, object_json: Path | None
) -> list[dict[str, Any]]:
    """Обогатить ``data``-связи ``summary.relations`` типом/синонимом.

    Только связи ``kind == "data"`` резолвятся через ``catalog_resolver``:
    это единственные связи с ``data_path``, для которых резолюция по
    файлу объекта имеет смысл. Связи ``kind == "event"`` не трогаются.
    Если ``object_json`` не найден, все data-связи возвращаются как
    нерезолвленные — без обращения к диску.
    """
    resolved: list[dict[str, Any]] = []
    for relation in summary.relations:
        if relation.get("kind") != "data":
            continue
        data_path = str(relation.get("target") or "")
        if not data_path:
            continue
        if object_json is None:
            resolved.append({
                "data_path": data_path,
                "object_type": "",
                "attribute_name": data_path.rsplit(".", 1)[-1],
                "value_type": None,
                "synonym": None,
                "resolved": False,
            })
            continue
        binding = resolve_data_path(data_path, object_json)
        resolved.append({
            "data_path": binding.data_path,
            "object_type": binding.object_type,
            "attribute_name": binding.attribute_name,
            "value_type": binding.value_type,
            "synonym": binding.synonym,
            "resolved": binding.resolved,
        })
    return resolved


def _resolve(value: str | Path | None, root: Path) -> Path | None:
    """Привести путь к абсолютному виду относительно ``root``.

    ``FormEntry`` содержит и абсолютные (``form_path``, ``bsl_path``), и
    относительные (``elem_json_path``) пути — обрабатываются оба случая.
    """
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_bsl(bsl_path: Path | None) -> str | None:
    """Прочитать модуль формы явно в UTF-8.

    Отсутствие файла — штатный ``None`` (у elem-only форм ``bsl_path``
    вообще является заглушкой). Ошибки чтения не глотаются.
    """
    if bsl_path is None or not bsl_path.is_file():
        return None
    return bsl_path.read_text(encoding="utf-8")


def _form_dir(
    form_entry: _FormEntryProtocol, elem_json_path: str | Path | None, root: Path
) -> Path | None:
    """Каталог формы для ``build_form_summary``.

    Приоритет у ``elem_json_path``: это подтверждённый реестром источник
    структуры (issue #57). Если поле ``None`` (старые индексы) — берётся
    ``form_path``; ``build_form_summary`` сам найдёт ``*.elem.json`` в каталоге.
    """
    elem_abs = _resolve(elem_json_path, root)
    if elem_abs is not None:
        return elem_abs.parent
    return _resolve(form_entry.form_path, root)


def _build_summary(
    form_dir: Path | None, root: Path
) -> tuple[FormSummary, list[dict[str, Any]]]:
    """Выжимка структуры формы единственным парсером проекта.

    Каталога формы может не быть вовсе — например, индекс старше
    выгрузки. Тогда возвращается пустая выжимка с предупреждением:
    вызывать парсер по несуществующему пути нет смысла, а глотать
    произвольные исключения нельзя.

    Вторым элементом возвращается явное отрицательное знание о
    ``data_path`` (issue #141). Оно выводится из структурных фактов
    (каталог, файл структуры, флаг ``elem_index_ok``, привязки), а не из
    текста ``warnings``.
    """
    if form_dir is None or not form_dir.is_dir():
        location = _relative_str(form_dir, root) or "неизвестно"
        summary = FormSummary(
            warnings=[f"каталог формы не найден: {location}"]
        )
        return summary, [_form_status(DATA_PATH_REASON_FORM_DIR_MISSING)]

    elem_result = parse_elem_json(form_dir)
    summary = _anonymize_summary(
        build_form_summary_from_elem_index(elem_result), root
    )
    if not elem_result.elem_index_ok:
        return summary, [_form_status(_unindexed_reason(form_dir))]
    return summary, _element_statuses(summary)


def _status_entry(reason: str, scope: str, element: str | None) -> dict[str, Any]:
    """Каноническая запись отрицательного знания (issue #141)."""
    return {
        "scope": scope,
        "element": element,
        "data_path": None,
        "status": DATA_PATH_REASONS[reason],
        "reason": reason,
    }


def _form_status(reason: str) -> dict[str, Any]:
    """Отрицательное знание для всей формы: элементы не прочитаны."""
    return _status_entry(reason, "form", None)


def _unindexed_reason(form_dir: Path) -> str:
    """Причина, по которой структура формы не прочитана.

    Файл ищется тем же локатором, что и в ``parse_elem_json``. Проверка
    ``json.loads`` лишь различает «файл не является JSON» и «JSON есть, но
    раскладка не распознана»; структуру она не разбирает.
    """
    elem_path = _find_elem_json(form_dir)
    if elem_path is None:
        return DATA_PATH_REASON_ELEM_JSON_MISSING
    try:
        json.loads(Path(elem_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return DATA_PATH_REASON_ELEM_JSON_INVALID
    return DATA_PATH_REASON_LAYOUT_NOT_RECOGNIZED


def _element_statuses(summary: FormSummary) -> list[dict[str, Any]]:
    """Элементы данных без доказанной привязки.

    Учитываются только типы из ``coverage_metric.DATA_ELEMENT_TYPES``:
    у служебных элементов привязки нет по определению. Доказанной считается
    привязка, которую уже выдал парсер (``relations`` с ``kind == "data"``).
    Порядок повторяет ``summary.elements``, дубликаты имён схлопываются.
    """
    proven = {
        str(relation.get("element") or "")
        for relation in summary.relations
        if relation.get("kind") == "data" and relation.get("target")
    }
    statuses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in summary.elements:
        if item.get("kind") not in DATA_ELEMENT_TYPES:
            continue
        name = str(item.get("name") or "")
        if name in proven or name in seen:
            continue
        seen.add(name)
        statuses.append(
            _status_entry(DATA_PATH_REASON_BINDING_NOT_PROVEN, "element", name)
        )
    return statuses


def _anonymize_summary(summary: FormSummary, root: Path) -> FormSummary:
    """Убрать базу ``root`` из текстов предупреждения выжимки.

    Предупреждения ``parse_elem_json`` могут содержать абсолютный путь
    каталога формы, а ``FormContext`` идёт в промпт и в отчёты. Парсер не
    меняется: текст только обезличивается здесь, на границе контекста.
    Если менять нечего, возвращается тот же объект.
    """
    original = list(summary.warnings)
    cleaned = [_strip_root(str(item), root) for item in original]
    if cleaned == original:
        return summary
    return replace(summary, warnings=cleaned)


def _strip_root(text: str, root: Path) -> str:
    """Вырезать префикс ``root`` из произвольного текста.

    Сначала убирается более длинная форма базы, чтобы символические
    ссылки не оставляли хвостов. Разделитель после базы тоже убирается,
    чтобы остался именно относительный путь.
    """
    for base in _root_bases(root):
        if not base:
            continue
        for separator in ("/", "\\"):
            text = text.replace(base + separator, "")
        text = text.replace(base, "")
    return text


def _root_bases(root: Path) -> tuple[str, ...]:
    """Формы записи корня, от длинной к короткой."""
    bases = {str(root)}
    try:
        bases.add(str(root.resolve()))
    except OSError:
        pass
    return tuple(sorted(bases, key=len, reverse=True))


def _relative_str(value: str | Path | None, root: Path) -> str | None:
    """Обезличенный относительный posix-строка или ``None``.

    Абсолютные локальные пути в публичные метаданные не попадают.
    Если путь лежит вне ``root``, остаётся только имя последнего сегмента.
    """
    if value is None:
        return None

    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()

    for base in (root, root.resolve()):
        for candidate in (path, path.resolve()):
            try:
                return candidate.relative_to(base).as_posix()
            except ValueError:
                continue

    return path.name


# --- issue #125: token budget ---

_TOKEN_BUDGET_PAIRING_ERROR = (
    "to_llm_prompt_fragment: max_tokens и count_tokens передаются только вместе"
)


def _split_complete_lines(text: str) -> list[str]:
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _line_prefix(lines: list[str], char_cap: int) -> list[str]:
    kept: list[str] = []
    total = 0
    for line in lines:
        if total + len(line) > char_cap:
            break
        kept.append(line)
        total += len(line)
    return kept


def _safe_token_count(count_tokens: Callable[[str], int], text: str) -> int | None:
    try:
        value = count_tokens(text)
    except Exception:  # noqa: BLE001 - сбой внешнего счётчика не роняет промпт
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def to_llm_prompt_fragment(
    context: FormContext,
    max_chars: int = -1,
    *,
    max_tokens: int | None = None,
    count_tokens: Callable[[str], int] | None = None,
) -> str:
    """Фрагмент для промпта с символьным и опциональным токенным бюджетом.

    Без ``max_tokens`` и ``count_tokens`` результат бит-в-бит совпадает с
    символьным режимом (#77). Контракт токенного режима (#125):

    - ``max_tokens`` и ``count_tokens`` передаются только вместе, иначе
      ``ValueError``;
    - ``max_tokens <= 0`` даёт пустую строку;
    - итог — префикс из целых строк санитизированного фрагмента, для
      которого одновременно ``len(result) <= max_chars`` (по семантике
      символьного режима) и ``count_tokens(result) <= max_tokens``;
    - исключение или некорректный результат ``count_tokens`` (не ``int``,
      ``bool`` или отрицательное число) — fail-safe fallback на символьный
      бюджет с той же границей целых строк, исходное исключение не
      пробрасывается.
    """
    if max_tokens is None and count_tokens is None:
        return _to_llm_prompt_fragment_chars(context, max_chars)
    if max_tokens is None or count_tokens is None:
        raise ValueError(_TOKEN_BUDGET_PAIRING_ERROR)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise TypeError("to_llm_prompt_fragment: max_tokens должен быть int")
    if not callable(count_tokens):
        raise TypeError("to_llm_prompt_fragment: count_tokens должен быть callable")
    if max_tokens <= 0:
        return ""

    char_limited = _to_llm_prompt_fragment_chars(context, max_chars)
    if not char_limited:
        return ""
    full = _to_llm_prompt_fragment_chars(context, -1)
    lines = _line_prefix(_split_complete_lines(full), len(char_limited))

    def fits(count: int) -> bool | None:
        tokens = _safe_token_count(count_tokens, "".join(lines[:count]))
        if tokens is None:
            return None
        return tokens <= max_tokens

    low, high = 0, len(lines)
    while low < high:
        mid = (low + high + 1) // 2
        verdict = fits(mid)
        if verdict is None:
            return "".join(lines)
        if verdict:
            low = mid
        else:
            high = mid - 1
    return "".join(lines[:low])
