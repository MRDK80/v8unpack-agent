"""coverage_metric — расчёт покрытия data_path по элементам формы.

Решает issue #90: старая метрика «14134 из 49326 = 28.7%» вводила в заблуждение,
потому что знаменатель включал Label, CommandPanel, Panel, Page и другие
элементы, которым data_path не нужен по определению.

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
``ПометкаУдаления`` отсутствуют в ``Properties`` объекта (они добавляются
платформой автоматически), но являются валидными полями данных. Они
учтены через :data:`PLATFORM_STANDARD_ATTRIBUTES` — эта константа
используется вызывающим кодом при резолюции привязок.

OS-нейтральность, кодировка UTF-8.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
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
    """

    total_elements: int
    data_elements: int
    bound_data_elements: int
    coverage_pct: float

    def __str__(self) -> str:
        return (
            f"Привязано {self.bound_data_elements} из {self.data_elements} "
            f"элементов данных = {self.coverage_pct:.1f}% "
            f"(всего элементов формы: {self.total_elements})"
        )

    def to_dict(self) -> dict:
        """Сериализовать в dict для JSON-отчётов."""
        return {
            "total_elements": self.total_elements,
            "data_elements": self.data_elements,
            "bound_data_elements": self.bound_data_elements,
            "coverage_pct": round(self.coverage_pct, 2),
        }


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def calc_data_path_coverage(elements: Iterable[dict]) -> CoverageReport:
    """Рассчитать покрытие data_path только по элементам данных.

    Параметры
    ---------
    elements:
        Итерируемое коллекции dict-элементов формы.
        Каждый dict должен содержать:

        - ``"type"`` (str) — тип элемента (например ``"Field"``, ``"Label"``);
        - ``"data_path"`` (str | None) — привязка к данным или ``None``.

        Остальные ключи игнорируются.

    Возвращает
    ----------
    :class:`CoverageReport` с двумя метриками:

    - покрытие по полям данных (``bound_data_elements / data_elements``);
    - общее число элементов формы (``total_elements``).

    Notes
    -----
    Элемент считается «привязанным», если ``data_path`` — непустая строка
    (``bool(data_path)`` истинно). Значения ``None``, ``""`` и ``0``
    считаются отсутствием привязки.
    """
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

    coverage = (bound_count / data_count * 100.0) if data_count > 0 else 0.0
    return CoverageReport(
        total_elements=total,
        data_elements=data_count,
        bound_data_elements=bound_count,
        coverage_pct=coverage,
    )


def calc_coverage_from_elem_index(result: object) -> CoverageReport:
    """Удобная обёртка: принимает ``ElemIndexResult`` из elem_parser.

    Параметры
    ---------
    result:
        Экземпляр :class:`~v8unpack_agent.elem_parser.ElemIndexResult`.
        Если ``result.elem_index_ok`` ложен или ``result.elements`` пуст —
        возвращает нулевой :class:`CoverageReport`.
    """
    elem_index_ok = getattr(result, "elem_index_ok", False)
    elements = getattr(result, "elements", []) or []
    if not elem_index_ok or not elements:
        return CoverageReport(
            total_elements=0,
            data_elements=0,
            bound_data_elements=0,
            coverage_pct=0.0,
        )
    return calc_data_path_coverage(elements)
