"""Компактный LLM-готовый контекст формы (issue #77).

``FormEntry`` из :mod:`v8unpack_agent.scan_forms` — карточка указателей: она
знает, где лежат части формы, но не содержит их содержимого.
:class:`FormContext` материализует содержимое: прочитанный BSL-текст,
построенный :class:`~v8unpack_agent.form_summary.FormSummary` и компактные
метаданные, пригодные для вставки в промпт.

Почему нужен ``unpacked_root``
---------------------------------

Семантика путей ``FormEntry`` смешанная (issue #57): ``form_path``,
``bsl_path`` и ``json_path`` абсолютные, а ``elem_json_path`` —
relative-to-root и может быть ``None`` (старые индексы). ``unpacked_root``
резолвит относительные пути и одновременно служит базой для
обезличенных относительных путей в ``metadata``.

Границы контракта
------------------

* второго пути разбора не вводится: структуру даёт единственный
  ``build_form_summary`` поверх ``parse_elem_json``;
* отсутствие BSL — штатный ``None``, пустой файл — пустая строка;
* отсутствие ``*.elem.json`` обрабатывает сам ``FormSummary`` — пустые
  бакеты и ``warnings`` парсера;
* привязки здесь не создаются и не догадываются: отсутствующий файл
  никогда не превращается в выдуманные данные;
* ``to_llm_prompt_fragment`` физически не может вернуть больше
  ``max_chars`` символов: обрезка выполняется последним шагом.

RAG-индексация (#78) и диспетчеризация (#79) в этот модуль не входят.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from v8unpack_agent.form_summary import (
    FormSummary,
    build_form_summary,
    to_normalized_json,
)

__all__ = [
    "FormContext",
    "build_form_context",
    "to_llm_prompt_fragment",
]

#: Маркеры секций фрагмента. Формат стабилен и тестируем.
SUMMARY_MARKER = "## SUMMARY"
BSL_MARKER = "## BSL"
#: Замена тела модуля, когда BSL отсутствует (elem-only форма).
NO_BSL_PLACEHOLDER = "(модуль формы отсутствует)"


@dataclass(frozen=True)
class FormContext:
    """Материализованное содержимое одной формы.

    ``bsl_text``
        Содержимое модуля формы, прочитанное как UTF-8. ``None`` — файла
        нет (штатная ситуация для elem-only форм). Пустая строка отличается
        от ``None``: файл есть, но пуст.
    ``summary``
        Семантическая выжимка структуры формы.
    ``metadata``
        Отобранные поля ``FormEntry`` без дублирования всей карточки;
        пути — только относительные posix-строки.

    Датакласс frozen, как и ``FormSummary``: подмена полей запрещена.
    Глубокой неизменяемости у ``metadata`` нет — это та же комбинация,
    что уже принята в ``FormSummary`` со списками.
    """

    form_name: str
    container_name: str
    object_type: str
    object_name: str
    bsl_text: str | None
    summary: FormSummary
    metadata: dict[str, Any]


def build_form_context(form_entry: Any, unpacked_root: Path) -> FormContext:
    """Собрать :class:`FormContext` по карточке ``FormEntry``.

    Parameters
    ----------
    form_entry:
        Запись реестра форм (``scan_forms.FormEntry``).
    unpacked_root:
        Корень распакованной выгрузки. Им резолвятся относительные
        пути и вычисляются обезличенные пути для ``metadata``.

    Ни одна ветка не порождает данные, которых нет на диске.
    """
    root = Path(unpacked_root)

    bsl_path = _resolve(getattr(form_entry, "bsl_path", None), root)
    bsl_text = _read_bsl(bsl_path)

    elem_json_path = getattr(form_entry, "elem_json_path", None)
    form_dir = _form_dir(form_entry, elem_json_path, root)
    summary = _build_summary(form_dir, root)

    metadata: dict[str, Any] = {
        "form_path": _relative_str(getattr(form_entry, "form_path", None), root),
        "elem_json_path": _relative_str(elem_json_path, root),
        "bsl_sha256": getattr(form_entry, "bsl_sha256", None),
        "elem_sha256": getattr(form_entry, "elem_sha256", None),
        "has_bsl": bsl_text is not None,
        "warnings": list(getattr(form_entry, "warnings", []) or []),
    }

    return FormContext(
        form_name=str(getattr(form_entry, "form_name", "") or ""),
        container_name=str(getattr(form_entry, "container_name", "") or ""),
        object_type=str(getattr(form_entry, "object_type", "") or ""),
        object_name=str(getattr(form_entry, "object_name", "") or ""),
        bsl_text=bsl_text,
        summary=summary,
        metadata=metadata,
    )


def to_llm_prompt_fragment(context: FormContext, max_chars: int = 8000) -> str:
    """Компактное текстовое представление для вставки в промпт.

    Порядок фиксирован: заголовок формы, затем ``## SUMMARY``, затем
    ``## BSL``. Смысловая выжимка важнее кода, поэтому при жёстком
    лимите обрезается именно хвост BSL.

    ``max_chars`` меньше или равный нулю даёт пустую строку: запрошен
    нулевой бюджет — выдан нулевой бюджет, исключения нет.
    Результат детерминирован и всегда не длиннее ``max_chars``.
    """
    if max_chars <= 0:
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

    fragment = "\n".join(
        (
            header,
            SUMMARY_MARKER,
            to_normalized_json(context.summary),
            BSL_MARKER,
            body,
        )
    )

    return fragment[:max_chars]


def _resolve(value: object, root: Path) -> Path | None:
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


def _form_dir(form_entry: Any, elem_json_path: object, root: Path) -> Path | None:
    """Каталог формы для ``build_form_summary``.

    Приоритет у ``elem_json_path``: это подтверждённый реестром источник
    структуры (issue #57). Если поле ``None`` (старые индексы) — берётся
    ``form_path``; ``build_form_summary`` сам найдёт ``*.elem.json`` в каталоге.
    """
    elem_abs = _resolve(elem_json_path, root)
    if elem_abs is not None:
        return elem_abs.parent
    return _resolve(getattr(form_entry, "form_path", None), root)


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
    return build_form_summary(form_dir)


def _relative_str(value: object, root: Path) -> str | None:
    """Обезличенный относительный posix-путь или ``None``.

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
