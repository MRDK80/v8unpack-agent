"""coverage_metric — расчёт покрытия data_path по элементам формы.

Решает issue #90: старая метрика «14134 из 49326 = 28.7%» вводила в заблуждение,
потому что знаменатель включал Label, CommandPanel, Panel, Page и другие
элементы, которым data_path не нужен по определению.

Исправлено в issue #98:
- добавлено поле ``form_class`` в :class:`CoverageReport`;
- добавлен параметр ``form_name`` в :func:`calc_data_path_coverage`;
- формы с пустым ``tree`` диагностируются в :func:`calc_coverage_from_elem_index`
  через :func:`~v8unpack_agent.form_classifier.classify_empty_tree_form`.

Issue #112 (Вариант B):
:func:`calc_coverage_from_elem_index` теперь использует структурное подтверждение:
- при ``elem_index_ok=False`` вызывает ``classify_unindexed_form()``;
- если reason == ``NO_TABULAR_NO_WIDGETS`` — вычисляет
  ``has_data_widgets = not _has_tabular_field(legacy_data)`` и передаёт
  флаг в :func:`~v8unpack_agent.form_classifier.classify_no_widgets_form`;
- конфликт сигналов (``has_data_widgets=True``) → UNKNOWN;
- для всех остальных reason — поведение PR #111.

Два слоя метрики
----------------
``data_elements``
    Количество элементов типов из :data:`DATA_ELEMENT_TYPES` — входят
    в знаменатель. Это «осмысленные» поля формы: Field, InputField, Table,
    CheckBox, Calendar, Chart, Picture.

``total_elements``
    Все элементы формы без исключения — для справки.

Стандартные реквизиты платформы
--------------------------------
Реквизиты ``Код``, ``Наименование``, ``Родитель``, ``Дата``, ``Номер``,
``ПометкаУдаления`` отсутствуют в ``Properties`` объекта
(Catalog.json / Document.json), но являются полностью валидными полями данных.
Они учтены через :data:`PLATFORM_STANDARD_ATTRIBUTES` — эта константа
используется вызывающим кодом при резолюции привязок.

Классификация форм
--------------------
Поле ``form_class`` определяется модулем
:mod:`~v8unpack_agent.form_classifier` (задача #98):
- ``"object"`` — объектная форма;
- ``"service"`` — сервисная (мастер, помощник, диалог);
- ``"unknown"`` — нельзя определить.

Формы с пустым tree
--------------------
Пустой ``tree`` НЕ означает сервисную форму. Верификация на production
(2231 форма) показала, что среди таких форм есть объектные: ``ФормаЗаписи``
регистров сведений и основные формы ``Форма`` отчётов и обработок. Их
разметку elem_parser не распарсил. Отнесение таких форм к SERVICE
исключило бы объектные формы из агрегированного покрытия и замаскировало
пробел в парсере, поэтому по умолчанию возвращается ``"unknown"``.

OS-нейтральность, кодировка UTF-8.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Константы типов элементов
# ---------------------------------------------------------------------------

# Типы элементов формы, для которых data_path имеет смысл.
# Только эти типы участвуют в знаменателе метрики покрытия.
# Источник: документация платформы 1С + верификация на живых данных (issue #90).
DATA_ELEMENT_TYPES: frozenset[str] = frozenset({
    "Field",
    "InputField",
    "Table",
    "CheckBox",
    "Calendar",
    "Chart",
    "Picture",
})

# Служебные типы элементов — не имеют привязки к данным по определению.
# Исключаются из знаменателя метрики покрытия.
# Добавление нового типа сюда автоматически исключает его из счётчика.
SERVICE_ELEMENT_TYPES: frozenset[str] = frozenset({
    "Label",
    "CommandPanel",
    "Panel",
    "Page",
    "Group",
    "Button",
    "Separator",
    "Unknown",
})

# Стандартные реквизиты платформы 1С.
# Эти реквизиты создаются платформой автоматически и отсутствуют
# в секции Properties объекта (Catalog.json / Document.json),
# однако являются полностью валидными полями данных.
# Используется вызывающим кодом при резолюции привязок (issue #90).
PLATFORM_STANDARD_ATTRIBUTES: frozenset[str] = frozenset({
    "Код",
    "Наименование",
    "Родитель",
    "Дата",
    "Номер",
    "ПометкаУдаления",
})


# ---------------------------------------------------------------------------
# Dataclass результата
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageReport:
    """Результат расчёта покрытия data_path для одной формы.

    Attributes
    ----------
    total_elements:
        Общее количество элементов формы (все типы).
    data_elements:
        Количество элементов типов из :data:`DATA_ELEMENT_TYPES`
        (входят в знаменатель).
    bound_data_elements:
        Из data_elements — те, у которых data_path не пустой.
    coverage_pct:
        bound_data_elements / data_elements * 100.
        0.0, если data_elements == 0.
    form_class:
        Класс формы: ``"object"`` | ``"service"`` | ``"unknown"``.
        Определяется :mod:`~v8unpack_agent.form_classifier` (issue #98).
        По умолчанию ``"unknown"`` — обратная совместимость
        (старый код, не передающий form_name, получит UNKNOWN).
    """

    total_elements: int
    data_elements: int
    bound_data_elements: int
    coverage_pct: float
    form_class: str = field(default="unknown")

    def __str__(self) -> str:
        return (
            f"Привязано {self.bound_data_elements} из {self.data_elements} "
            f"элементов данных = {self.coverage_pct:.1f}% "
            f"(всего элементов формы: {self.total_elements})"
            f" [form_class={self.form_class}]"
        )

    def to_dict(self) -> dict:
        """Сериализовать в dict для JSON-отчётов."""
        return {
            "total_elements": self.total_elements,
            "data_elements": self.data_elements,
            "bound_data_elements": self.bound_data_elements,
            "coverage_pct": round(self.coverage_pct, 2),
            "form_class": str(self.form_class),
        }


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def calc_data_path_coverage(
    elements: Iterable[dict],
    form_name: str | None = None,
) -> CoverageReport:
    """Рассчитать покрытие data_path только по элементам данных.

    Параметры
    ---------
    elements:
        Итерируемая коллекция dict-элементов формы.
        Каждый dict должен содержать:

        - ``"type"`` (str) — тип элемента (например ``"Field"``, ``"Label"``);
        - ``"data_path"`` (str | None) — привязка к данным или ``None``.

        Остальные ключи игнорируются.
    form_name:
        Короткое имя формы (лист-часть пути), например ``"ПомощникПодключенияЭДО"``.
        Если указано, используется в :func:`classify_form` для
        заполнения ``form_class`` в :class:`CoverageReport`.
        Если ``None`` — классификация только по привязкам.

    Возвращает
    ----------
    :class:`CoverageReport` с тремя метриками:

    - покрытие по полям данных (``bound_data_elements / data_elements``);
    - общее число элементов формы (``total_elements``);
    - класс формы (``form_class``).

    Notes
    -----
    Элемент считается «привязанным», если ``data_path`` — непустая строка
    (``bool(data_path)`` истинно). Значения ``None``, ``""`` и ``0``
    считаются отсутствием привязки.
    """
    # Ленивый импорт внутри функции — избегаем циклический импорт на уровне модуля.
    from v8unpack_agent.form_classifier import classify_form  # noqa: PLC0415

    elements = list(elements)
    total = 0
    data_count = 0
    bound_count = 0

    for elem in elements:
        total += 1
        elem_type = elem.get("type") or "Unknown"

        if elem_type not in DATA_ELEMENT_TYPES:
            continue

        data_count += 1

        if elem.get("data_path"):
            bound_count += 1

    coverage = (
        (bound_count / data_count * 100.0)
        if data_count > 0
        else 0.0
    )

    form_cls = classify_form(
        form_name=form_name or "",
        elements=elements,
    )

    return CoverageReport(
        total_elements=total,
        data_elements=data_count,
        bound_data_elements=bound_count,
        coverage_pct=coverage,
        form_class=str(form_cls),
    )


def calc_coverage_from_elem_index(
    result: object,
    form_name: str | None = None,
    form_root: object = None,
) -> CoverageReport:
    """Удобная обёртка: принимает ``ElemIndexResult`` из elem_parser.

    Параметры
    ---------
    result:
        Экземпляр :class:`~v8unpack_agent.elem_parser.ElemIndexResult`.
    form_name:
        Короткое имя формы для классификации.
    form_root:
        Путь к директории формы (Path). Если указан и
        ``elem_index_ok=False``, используется для
        структурного подтверждения SERVICE через
        ``classify_unindexed_form`` + ``classify_no_widgets_form``
        (issue #112, Вариант B).

    Поведение
    ---------
    - ``elem_index_ok=True`` и elements есть → полный расчёт
      через :func:`calc_data_path_coverage`.
    - ``elem_index_ok=False`` + ``form_root`` указан →
      ``classify_unindexed_form()``;
      если reason == ``NO_TABULAR_NO_WIDGETS`` —
      вычисляет ``has_data_widgets`` и вызывает
      :func:`~v8unpack_agent.form_classifier.classify_no_widgets_form`
      с флагом (issue #112 Вариант B).
    - ``elem_index_ok=False`` без ``form_root`` / другой reason →
      :func:`~v8unpack_agent.form_classifier.classify_empty_tree_form`
      (поведение PR #111).
    - form_name не передан → ``"unknown"`` (обратная совместимость).
    """
    from v8unpack_agent.form_classifier import (  # noqa: PLC0415
        FormClass,
        classify_empty_tree_form,
        classify_no_widgets_form,
    )
    from v8unpack_agent.elem_parser import (  # noqa: PLC0415
        UnindexedReason,
        classify_unindexed_form,
        _has_tabular_field,
    )
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    elem_index_ok = getattr(result, "elem_index_ok", False)
    elements = getattr(result, "elements", []) or []

    if not elem_index_ok or not elements:
        form_class = FormClass.UNKNOWN

        # issue #112 Вариант B: структурное подтверждение
        if form_root is not None and form_name:
            try:
                _form_root = Path(form_root)
                _dummy_result = type("_R", (), {"elem_index_ok": False, "elements": []})()  # noqa: UP012
                unindexed = classify_unindexed_form(_form_root, _dummy_result)

                if unindexed.reason is UnindexedReason.NO_TABULAR_NO_WIDGETS:
                    # Вычисляем has_data_widgets: есть ли виджеты в legacy JSON
                    legacy_path = _form_root / _find_legacy_json_name(_form_root)
                    if legacy_path.exists():
                        try:
                            legacy_data = json.loads(
                                legacy_path.read_text(encoding="utf-8-sig")
                            )
                            has_data_widgets = _has_tabular_field(legacy_data)
                        except Exception:  # noqa: BLE001
                            has_data_widgets = None
                    else:
                        has_data_widgets = None

                    form_class = classify_no_widgets_form(
                        form_name,
                        unindexed.reason,
                        has_data_widgets=has_data_widgets,
                    )
                else:
                    # Другой reason — поведение PR #111
                    if form_name:
                        form_class, _reason = classify_empty_tree_form(form_name)

            except Exception:  # noqa: BLE001
                # best-effort: не ломаем пиплайн при ошибке чтения
                if form_name:
                    form_class, _reason = classify_empty_tree_form(form_name)

        elif form_name:
            # form_root не передан — поведение PR #111
            form_class, _reason = classify_empty_tree_form(form_name)

        return CoverageReport(
            total_elements=0,
            data_elements=0,
            bound_data_elements=0,
            coverage_pct=0.0,
            form_class=str(form_class),
        )

    return calc_data_path_coverage(elements, form_name=form_name)


def _find_legacy_json_name(form_root: object) -> str:
    """Найти имя legacy JSON-файла в директории формы (best-effort).

    Дублирует логику ``_find_legacy_form_json`` из elem_parser,
    но возвращает только имя файла (не Path) для безопасного join.
    Если файл не найден — возвращает пустую строку (exists() вернёт False).
    """
    from pathlib import Path  # noqa: PLC0415
    from v8unpack_agent.elem_parser import _find_legacy_form_json  # noqa: PLC0415

    result = _find_legacy_form_json(Path(form_root))
    return result.name if result is not None else ""
