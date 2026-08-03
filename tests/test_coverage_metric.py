"""Tests for coverage_metric module (issue #90).

TDD-first: эти тесты писались до реализации.

Ключевой тест: форма Catalog/Банки/CatalogForm/ФормаЭлементаУправляемая — где
служебные элементы преобладают: 8 служебных из 19,
и фактическое покрытие полей данных = 100%.
"""
from __future__ import annotations

import pytest

from v8unpack_agent.coverage_metric import (
    DATA_ELEMENT_TYPES,
    SERVICE_ELEMENT_TYPES,
    PLATFORM_STANDARD_ATTRIBUTES,
    CoverageReport,
    calc_data_path_coverage,
    calc_coverage_from_elem_index,
)


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

class TestConstants:
    def test_data_and_service_types_are_disjoint(self):
        """Типы DATA и SERVICE не должны пересекаться."""
        assert DATA_ELEMENT_TYPES.isdisjoint(SERVICE_ELEMENT_TYPES)

    def test_data_element_types_contains_required_types(self):
        for t in ("Field", "InputField", "Table", "CheckBox", "Calendar", "Chart", "Picture"):
            assert t in DATA_ELEMENT_TYPES, f"{t!r} должен быть в DATA_ELEMENT_TYPES"

    def test_service_element_types_contains_required_types(self):
        for t in ("Label", "CommandPanel", "Panel", "Page", "Group", "Button", "Separator", "Unknown"):
            assert t in SERVICE_ELEMENT_TYPES, f"{t!r} должен быть в SERVICE_ELEMENT_TYPES"

    def test_platform_standard_attributes_contains_required(self):
        for name in ("Код", "Наименование", "Родитель", "Дата", "Номер", "ПометкаУдаления"):
            assert name in PLATFORM_STANDARD_ATTRIBUTES, f"{name!r} должен быть в PLATFORM_STANDARD_ATTRIBUTES"

    def test_platform_attributes_count(self):
        assert len(PLATFORM_STANDARD_ATTRIBUTES) >= 6

    def test_data_types_are_frozenset(self):
        assert isinstance(DATA_ELEMENT_TYPES, frozenset)
        assert isinstance(SERVICE_ELEMENT_TYPES, frozenset)
        assert isinstance(PLATFORM_STANDARD_ATTRIBUTES, frozenset)


# ---------------------------------------------------------------------------
# Эталонная форма Catalog/Банки/CatalogForm/ФормаЭлементаУправляемая
# 19 элементов: 11 Field (все привязаны) + 3 Group + 1 Button + 3 Panel/Page + 1 Label
# Фактическое покрытие = 100%
# ---------------------------------------------------------------------------

BANKS_FORM_ELEMENTS = [
    # Данные — привязанные
    {"type": "Field", "name": "КоррСчет", "data_path": "Объект.КоррСчет"},
    {"type": "Field", "name": "БИК", "data_path": "Объект.БИК"},
    {"type": "Field", "name": "НомерКор", "data_path": "Объект.НомерКор"},
    {"type": "Field", "name": "ТелефонФакс", "data_path": "Объект.ТелефонФакс"},
    {"type": "Field", "name": "Индекс", "data_path": "Объект.Индекс"},
    {"type": "Field", "name": "Город", "data_path": "Объект.Город"},
    {"type": "Field", "name": "Адрес", "data_path": "Объект.Адрес"},
    {"type": "Field", "name": "РКЦ", "data_path": "Объект.РКЦ"},
    {"type": "Field", "name": "Участник", "data_path": "Объект.Участник"},
    {"type": "Field", "name": "ОКПО", "data_path": "Объект.ОКПО"},
    {"type": "Field", "name": "ОКОНХ", "data_path": "Объект.ОКОНХ"},
    # Служебные — без привязки
    {"type": "Group", "name": "Группа1", "data_path": None},
    {"type": "Group", "name": "Группа2", "data_path": None},
    {"type": "Group", "name": "Группа3", "data_path": None},
    {"type": "Button", "name": "Команда1", "data_path": None},
    {"type": "Label", "name": "Надпись1", "data_path": None},
    {"type": "CommandPanel", "name": "ПанельКоманд", "data_path": None},
    {"type": "Panel", "name": "Панель1", "data_path": None},
    {"type": "Page", "name": "СтраницаОсновная", "data_path": None},
]


class TestBanksForm:
    """Форма с преобладанием служебных элементов (issue #90, эталон)."""

    def test_total_elements(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        assert report.total_elements == 19

    def test_data_elements_count(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        assert report.data_elements == 11

    def test_all_data_elements_bound(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        assert report.bound_data_elements == 11

    def test_coverage_is_100_percent(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        assert report.coverage_pct == pytest.approx(100.0)

    def test_str_contains_bound_and_total_data(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        s = str(report)
        assert "11 из 11" in s
        assert "100.0%" in s

    def test_str_contains_total_elements(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        assert "19" in str(report)

    def test_to_dict_keys(self):
        report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
        d = report.to_dict()
        assert set(d.keys()) == {"total_elements", "data_elements", "bound_data_elements", "coverage_pct"}


# ---------------------------------------------------------------------------
# Частичное покрытие
# ---------------------------------------------------------------------------

class TestPartialCoverage:
    def test_2_of_3_data_elements_bound(self):
        elems = [
            {"type": "Field", "data_path": "Объект.Название"},
            {"type": "Field", "data_path": None},
            {"type": "Label", "data_path": None},   # служебный
            {"type": "CheckBox", "data_path": "Объект.Признак"},
        ]
        report = calc_data_path_coverage(elems)
        assert report.total_elements == 4
        assert report.data_elements == 3
        assert report.bound_data_elements == 2
        assert report.coverage_pct == pytest.approx(200.0 / 3.0)

    def test_table_bound(self):
        elems = [
            {"type": "Table", "data_path": "Объект.Таблица"},
            {"type": "Separator", "data_path": None},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 1
        assert report.bound_data_elements == 1
        assert report.coverage_pct == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Пустая форма и только служебные
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_form(self):
        report = calc_data_path_coverage([])
        assert report.total_elements == 0
        assert report.data_elements == 0
        assert report.bound_data_elements == 0
        assert report.coverage_pct == 0.0

    def test_all_service_elements(self):
        elems = [
            {"type": "Label", "data_path": None},
            {"type": "CommandPanel", "data_path": None},
            {"type": "Panel", "data_path": None},
            {"type": "Page", "data_path": None},
            {"type": "Group", "data_path": None},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 0
        assert report.coverage_pct == 0.0
        assert report.total_elements == 5

    def test_unknown_type_excluded(self):
        """Unknown во вход — не данные, даже если есть data_path."""
        elems = [
            {"type": "Unknown", "data_path": "some_path"},
            {"type": "Field", "data_path": "Объект.X"},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 1
        assert report.bound_data_elements == 1

    def test_missing_type_key_treated_as_unknown(self):
        """Если у элемента нет ключа 'type' — считается Unknown, не данные."""
        elems = [
            {"data_path": "Объект.X"},  # нет 'type'
            {"type": "Field", "data_path": "Объект.Y"},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 1
        assert report.total_elements == 2

    def test_empty_string_data_path_counts_as_unbound(self):
        elems = [
            {"type": "Field", "data_path": ""},
            {"type": "Field", "data_path": "Объект.X"},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 2
        assert report.bound_data_elements == 1
        assert report.coverage_pct == pytest.approx(50.0)

    def test_coverage_report_is_frozen(self):
        report = calc_data_path_coverage([])
        with pytest.raises((AttributeError, TypeError)):
            report.total_elements = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Стандартные реквизиты платформы: Field со стандартным названием
# ---------------------------------------------------------------------------

class TestPlatformAttributes:
    """Стандартные реквизиты: в знаменатель, если тип элемента — данные."""

    def test_field_with_kod_is_counted_as_data(self):
        """Поле Field с data_path=Объект.Код — это данные + привязано."""
        elems = [
            {"type": "Field", "data_path": "Объект.Код"},
            {"type": "Field", "data_path": "Объект.Наименование"},
            {"type": "Group", "data_path": None},
        ]
        report = calc_data_path_coverage(elems)
        assert report.data_elements == 2
        assert report.bound_data_elements == 2
        assert report.coverage_pct == pytest.approx(100.0)

    def test_platform_attrs_names_in_constant(self):
        """PLATFORM_STANDARD_ATTRIBUTES должна содержать все 6 обязательных."""
        expected = {"Код", "Наименование", "Родитель", "Дата", "Номер", "ПометкаУдаления"}
        assert expected.issubset(PLATFORM_STANDARD_ATTRIBUTES)


# ---------------------------------------------------------------------------
# calc_coverage_from_elem_index
# ---------------------------------------------------------------------------

class TestCalcCoverageFromElemIndex:
    """calc_coverage_from_elem_index: удобная обёртка под ElemIndexResult."""

    def _make_result(self, ok: bool, elements: list) -> object:
        """Mock-объект с нужными атрибутами."""
        class MockResult:
            elem_index_ok = ok
            pass
        r = MockResult()
        r.elements = elements  # type: ignore[attr-defined]
        return r

    def test_ok_result_delegates_to_calc(self):
        elements = [
            {"type": "Field", "data_path": "Объект.X"},
            {"type": "Label", "data_path": None},
        ]
        result = self._make_result(True, elements)
        report = calc_coverage_from_elem_index(result)
        assert report.data_elements == 1
        assert report.bound_data_elements == 1
        assert report.total_elements == 2

    def test_failed_result_returns_zero_coverage(self):
        result = self._make_result(False, [])
        report = calc_coverage_from_elem_index(result)
        assert report.total_elements == 0
        assert report.coverage_pct == 0.0

    def test_ok_but_empty_elements_returns_zero(self):
        result = self._make_result(True, [])
        report = calc_coverage_from_elem_index(result)
        assert report.coverage_pct == 0.0


# ---------------------------------------------------------------------------
# Форма из issue #90: Catalog/Контрагенты/ФормаЭлемента
# Старая метрика: 14134/49326=28.7%
# Новая метрика берёт в знаменатель только данные-элементы
# ---------------------------------------------------------------------------

class TestContragentsFormExample:
    """Пример из issue #90: 40.2% по всем элементам, 100% по данным."""

    def test_service_heavy_form_coverage_above_data_only(self):
        """Если служебных больше — покрытие по данным выше, чем по всем."""
        # 53 Field все привязаны + 79 служебных
        bound_fields = [
            {"type": "Field", "data_path": f"Объект.R{i}"}
            for i in range(53)
        ]
        service_elems = [
            {"type": "Label", "data_path": None},
            {"type": "CommandPanel", "data_path": None},
            {"type": "Panel", "data_path": None},
            {"type": "Page", "data_path": None},
            {"type": "Group", "data_path": None},
        ] * 15  # 75 служебных, + 4 до 79
        service_elems += [
            {"type": "Separator", "data_path": None},
            {"type": "Button", "data_path": None},
            {"type": "Label", "data_path": None},
            {"type": "Page", "data_path": None},
        ]
        elements = bound_fields + service_elems
        report = calc_data_path_coverage(elements)

        # По данным: 53/53 = 100%
        assert report.data_elements == 53
        assert report.bound_data_elements == 53
        assert report.coverage_pct == pytest.approx(100.0)
        # Всего элементов: 53 + 79 = 132
        assert report.total_elements == 132
        # По всем: 53/132 = 40.2% — значение из issue #90
        old_metric = 53 / 132 * 100
        assert old_metric == pytest.approx(40.15, abs=0.1)
        # Новая метрика значительно выше
        assert report.coverage_pct > old_metric
