"""coverage_metric — расчёт покрытия data_path по элементам формы.

Решает issue #90: старая метрика «14134 из 49326 = 28.7%» вводила в заблуждение,
потому что знаменатель включал Label, CommandPanel, Panel, Page и другие
элементы, которым data_path не нужен по определению.

Исправлено в issue #98:
- добавлено поле ``form_class`` в :class:`CoverageReport`;
- добавлен параметр ``form_name`` в :func:`calc_data_path_coverage`;
- формы с пустым ``tree`` диагностируются в :func:`calc_coverage_from_elem_index`
  через :func:`~v8unpack_agent.form_classifier.classify_empty_tree_form`.

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
) -> CoverageReport:
    """Удобная обёртка: принимает ``ElemIndexResult`` из elem_parser.

    Параметры
    ---------
    result:
        Экземпляр :class:`~v8unpack_agent.elem_parser.ElemIndexResult`.
    form_name:
        Короткое имя формы для классификации.

    Поведение
    ---------
    - ``elem_index_ok=True`` и elements есть → полный расчёт
      через :func:`calc_data_path_coverage`.
    - ``elem_index_ok=False`` или elements=[] и form_name передан →
      :func:`~v8unpack_agent.form_classifier.classify_empty_tree_form`.
      SERVICE возвращается только при совпадении с проверенным списком
      сервисных паттернов; во всех остальных случаях — ``"unknown"``.
    - form_name не передан → ``"unknown"`` (обратная совместимость).

    Notes
    -----
    Ранее пустой ``tree`` трактовался как SERVICE «безопасным дефолтом».
    Это неверно: среди таких форм есть ``ФормаЗаписи`` регистров сведений
    и основные формы отчётов ``Форма``. Классификатор не придумывает класс,
    когда парсер не предоставил структуру формы.

    Причину решения (``platform_object_name_unparsed``,
    ``empty_tree_name_hint`` и др.) можно получить прямым вызовом
    :func:`classify_empty_tree_form` — она возвращает пару
    ``(класс, причина)``.
    """
    from v8unpack_agent.form_classifier import (  # noqa: PLC0415
        FormClass,
        classify_empty_tree_form,
    )

    elem_index_ok = getattr(result, "elem_index_ok", False)
    elements = getattr(result, "elements", []) or []

    if not elem_index_ok or not elements:
        if form_name:
            form_class, _reason = classify_empty_tree_form(form_name)
        else:
            form_class = FormClass.UNKNOWN

        return CoverageReport(
            total_elements=0,
            data_elements=0,
            bound_data_elements=0,
            coverage_pct=0.0,
            form_class=str(form_class),
        )

    return calc_data_path_coverage(elements, form_name=form_name)
