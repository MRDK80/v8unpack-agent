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
  ``build_form_summary`` поверх ``parse_elem_json``;
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

RAG-индексация (#78) и диспетчеризация (#79) в этот модуль не входят.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from v8unpack_agent.catalog_resolver import object_json_path, resolve_data_path
from v8unpack_agent.form_summary import (
    FormSummary,
    build_form_summary,
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
    summary = _build_summary(form_dir, root)

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
            _strip_root(str(item), root)
            for item in (form_entry.warnings or [])
        ]
        + [_strip_root(item, root) for item in object_warnings],
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
    )


def to_llm_prompt_fragment(context: FormContext, max_chars: int = -1) -> str:
    """Компактное текстовое представление для вставки в промпт.

    Порядок фиксирован: заголовок формы, ``## SUMMARY``,
    ``## OBJECT_ATTRIBUTES``, затем ``## BSL``. Смысловая выжимка важнее
    кода, поэтому при жёстком лимите обрезается именно хвост BSL.

    ``max_chars=-1`` отключает обрезку и возвращает полный контекст.
    Нулевой и остальные отрицательные лимиты дают пустую строку.
    При положительном лимите результат детерминирован и всегда не длиннее
    ``max_chars``.
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

    fragment = "\n".join((
        header,
        SUMMARY_MARKER,
        to_normalized_json(context.summary),
        OBJECT_ATTRIBUTES_MARKER,
        object_block,
        BSL_MARKER,
        body,
    ))

    return fragment if max_chars == -1 else fragment[:max_chars]


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


def _build_summary(form_dir: Path | None, root: Path) -> FormSummary:
    """Выжимка структуры формы единственным парсером проекта.

    Каталога формы может не быть вовсе — например, индекс старше
    выгрузки. Тогда возвращается пустая выжимка с предупреждением:
    вызывать парсер по несуществующему пути нет смысла, а глотать
    произвольные исключения нельзя.
    """
    if form_dir is None or not form_dir.is_dir():
        location = _relative_str(form_dir, root) or "неизвестно"
        return FormSummary(
            warnings=[f"каталог формы не найден: {location}"]
        )

    return _anonymize_summary(build_form_summary(form_dir), root)


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
