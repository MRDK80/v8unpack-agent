"""Tests for form_classifier module (issue #98).

TDD-first: тесты написаны до реализации.

Задача: классифицировать формы 1С на объектные и сервисные.
Сервисные формы (мастера, помощники, диалоги) привязывают поля
не к Объект.Реквизит, а к временным реквизитам формы — и дают
заниженное покрытие data_path. Это не баг парсера, а архитектурный
паттерн платформы 1С.

Критерий классификации — двойной:
1. По имени формы: паттерны Помощник*, Мастер*, Черновик*, Диалог*, Добавление*
2. По структуре: data_elements > 0 И нет ни одной привязки к Объект.*

Форма считается сервисной если выполняется хотя бы одно условие.

Эталонные формы (из issue #98):
- ДобавлениеПредставителя — map=44, elements=43, resolved=0 → service
- ПомощникПодключенияЭДО  — 45 data, 16 bound → service (по имени)
- ЧерновикМЧД             — 77 data, 1 bound → service (по имени)
- ФормаЭлементаУправляемая — 11/11 bound → object
"""
from __future__ import annotations

import pytest

from v8unpack_agent.form_classifier import (
    FormClass,
    classify_form_by_name,
    classify_form_by_bindings,
    classify_form,
    SERVICE_FORM_NAME_PATTERNS,
)


# ---------------------------------------------------------------------------
# Константа SERVICE_FORM_NAME_PATTERNS
# ---------------------------------------------------------------------------

class TestServiceFormNamePatterns:
    def test_patterns_is_tuple_or_frozenset(self):
        assert isinstance(SERVICE_FORM_NAME_PATTERNS, (tuple, frozenset, list))

    def test_patterns_not_empty(self):
        assert len(SERVICE_FORM_NAME_PATTERNS) >= 4

    def test_known_patterns_present(self):
        lower = [p.lower() for p in SERVICE_FORM_NAME_PATTERNS]
        for expected in ("помощник", "мастер", "черновик", "добавление"):
            assert any(expected in p for p in lower), f"Паттерн {expected!r} не найден"


# ---------------------------------------------------------------------------
# FormClass
# ---------------------------------------------------------------------------

class TestFormClass:
    def test_object_value(self):
        assert FormClass.OBJECT == "object"

    def test_service_value(self):
        assert FormClass.SERVICE == "service"

    def test_unknown_value(self):
        assert FormClass.UNKNOWN == "unknown"


# ---------------------------------------------------------------------------
# classify_form_by_name — классификация по имени формы
# ---------------------------------------------------------------------------

class TestClassifyFormByName:
    # --- Сервисные по имени ---

    def test_pomoshnik_is_service(self):
        assert classify_form_by_name("ПомощникПодключенияЭДО") == FormClass.SERVICE

    def test_master_is_service(self):
        assert classify_form_by_name("МастерНастройки") == FormClass.SERVICE

    def test_chernovik_is_service(self):
        assert classify_form_by_name("ЧерновикМЧД") == FormClass.SERVICE

    def test_dobavlenie_is_service(self):
        assert classify_form_by_name("ДобавлениеПредставителя") == FormClass.SERVICE

    def test_dialog_is_service(self):
        assert classify_form_by_name("ДиалогВыбораПериода") == FormClass.SERVICE

    def test_chernovik_peredoveriya_is_service(self):
        """Реальное имя из production-выгрузки."""
        assert classify_form_by_name("ЧерновикПередоверияМЧД") == FormClass.SERVICE

    def test_nastroyka_reglamen_is_unknown_by_name(self):
        """НастройкаРегламентаЭДО — не подходит под паттерн имени."""
        result = classify_form_by_name("НастройкаРегламентаЭДО")
        assert result == FormClass.UNKNOWN

    # --- Объектные / неизвестные по имени ---

    def test_forma_elementa_is_unknown_by_name(self):
        assert classify_form_by_name("ФормаЭлементаУправляемая") == FormClass.UNKNOWN

    def test_forma_spiska_is_unknown_by_name(self):
        assert classify_form_by_name("ФормаСписка") == FormClass.UNKNOWN

    def test_empty_name_is_unknown(self):
        assert classify_form_by_name("") == FormClass.UNKNOWN

    # --- Регистронезависимость ---

    def test_lowercase_pomoshnik_is_service(self):
        assert classify_form_by_name("помощниктест") == FormClass.SERVICE

    def test_uppercase_master_is_service(self):
        assert classify_form_by_name("МАСТЕРНАСТРОЙКИ") == FormClass.SERVICE


# ---------------------------------------------------------------------------
# classify_form_by_bindings — классификация по структуре привязок
# ---------------------------------------------------------------------------

class TestClassifyFormByBindings:
    """Форма сервисная если data_elements > 0 и ни одной привязки к Объект.*"""

    def test_zero_resolved_many_data_elements_is_service(self):
        """ДобавлениеПредставителя: 43 data-элемента, 0 привязок к Объект.*"""
        elements = [
            {"type": "Field", "data_path": None},
        ] * 43
        assert classify_form_by_bindings(elements) == FormClass.SERVICE

    def test_all_bound_to_objekt_is_object(self):
        """ФормаЭлементаУправляемая: все привязки к Объект.*"""
        elements = [
            {"type": "Field", "data_path": "Объект.БИК"},
            {"type": "Field", "data_path": "Объект.КоррСчет"},
        ]
        assert classify_form_by_bindings(elements) == FormClass.OBJECT

    def test_mixed_some_objekt_bindings_is_object(self):
        """Если хоть одна привязка к Объект.* — форма объектная."""
        elements = [
            {"type": "Field", "data_path": "Объект.Реквизит1"},
            {"type": "Field", "data_path": None},
            {"type": "Field", "data_path": None},
        ]
        assert classify_form_by_bindings(elements) == FormClass.OBJECT

    def test_partial_service_bindings_still_service(self):
        """ПомощникПодключенияЭДО: 16 bound, но все к временным реквизитам."""
        elements = [
            {"type": "Field", "data_path": "ЭтапПодключения"},
            {"type": "Field", "data_path": "АдресЭДО"},
            {"type": "Field", "data_path": None},
        ] * 15
        assert classify_form_by_bindings(elements) == FormClass.SERVICE

    def test_empty_elements_is_unknown(self):
        """Нет data-элементов — нельзя определить."""
        assert classify_form_by_bindings([]) == FormClass.UNKNOWN

    def test_all_service_elements_no_data_elements_is_unknown(self):
        """Только Label/Group/Panel — нет data-элементов, нельзя определить."""
        elements = [
            {"type": "Label", "data_path": None},
            {"type": "Group", "data_path": None},
            {"type": "Panel", "data_path": None},
        ]
        assert classify_form_by_bindings(elements) == FormClass.UNKNOWN

    def test_tabular_section_binding_counts_as_object(self):
        """Привязка к Объект.ТабличнаяЧасть.Реквизит тоже считается объектной."""
        elements = [
            {"type": "Field", "data_path": "Объект.Товары.Количество"},
        ]
        assert classify_form_by_bindings(elements) == FormClass.OBJECT

    def test_objekt_prefix_case_sensitive(self):
        """Проверка: 'объект.' в нижнем регистре тоже распознаётся."""
        elements = [
            {"type": "Field", "data_path": "объект.реквизит"},
        ]
        assert classify_form_by_bindings(elements) == FormClass.OBJECT


# ---------------------------------------------------------------------------
# classify_form — итоговая функция, объединяет оба критерия
# ---------------------------------------------------------------------------

class TestClassifyForm:
    """Форма SERVICE если хотя бы один критерий даёт SERVICE."""

    def test_service_by_name_overrides_unknown_bindings(self):
        """Имя = Помощник*, привязки неизвестны (нет data-элементов) → SERVICE."""
        result = classify_form(
            form_name="ПомощникПодключенияЭДО",
            elements=[{"type": "Label", "data_path": None}],
        )
        assert result == FormClass.SERVICE

    def test_service_by_bindings_overrides_unknown_name(self):
        """Имя = НастройкаРегламентаЭДО (не паттерн), но все привязки временные."""
        elements = [
            {"type": "Field", "data_path": "ЗначениеВводится"},
            {"type": "Field", "data_path": None},
        ]
        result = classify_form(
            form_name="НастройкаРегламентаЭДО",
            elements=elements,
        )
        assert result == FormClass.SERVICE

    def test_object_by_bindings_and_unknown_name(self):
        """Имя нейтральное, привязки к Объект.* → OBJECT."""
        elements = [
            {"type": "Field", "data_path": "Объект.БИК"},
            {"type": "Field", "data_path": "Объект.КоррСчет"},
        ]
        result = classify_form(
            form_name="ФормаЭлементаУправляемая",
            elements=elements,
        )
        assert result == FormClass.OBJECT

    def test_both_service_is_service(self):
        """И имя, и привязки указывают на сервисную → SERVICE."""
        elements = [
            {"type": "Field", "data_path": "ЭтапМастера"},
            {"type": "Field", "data_path": None},
        ]
        result = classify_form(
            form_name="МастерЗаполнения",
            elements=elements,
        )
        assert result == FormClass.SERVICE

    def test_unknown_name_and_unknown_bindings_is_unknown(self):
        """Ни имя, ни привязки не дали ответа → UNKNOWN."""
        result = classify_form(
            form_name="ФормаСписка",
            elements=[{"type": "Label", "data_path": None}],
        )
        assert result == FormClass.UNKNOWN

    def test_dobavlenie_predstavitelya_full_scenario(self):
        """Эталон из issue #98: ДобавлениеПредставителя, map=44, resolved=0."""
        elements = [
            {"type": "Field", "data_path": None},
        ] * 43 + [
            {"type": "Label", "data_path": None},
        ]
        result = classify_form(
            form_name="ДобавлениеПредставителя",
            elements=elements,
        )
        assert result == FormClass.SERVICE

    def test_chernovik_mchd_full_scenario(self):
        """Эталон из issue #98: ЧерновикМЧД, 77 data, 1 bound (к временному)."""
        elements = (
            [{"type": "Field", "data_path": "ВременноеЗначение"}]
            + [{"type": "Field", "data_path": None}] * 76
        )
        result = classify_form(
            form_name="ЧерновикМЧД",
            elements=elements,
        )
        assert result == FormClass.SERVICE

    def test_empty_name_and_empty_elements_is_unknown(self):
        result = classify_form(form_name="", elements=[])
        assert result == FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# CoverageReport.form_class — интеграция с coverage_metric
# ---------------------------------------------------------------------------

class TestCoverageReportFormClass:
    """CoverageReport должен содержать поле form_class."""

    def test_coverage_report_has_form_class_field(self):
        from v8unpack_agent.coverage_metric import CoverageReport
        report = CoverageReport(
            total_elements=10,
            data_elements=5,
            bound_data_elements=5,
            coverage_pct=100.0,
            form_class=FormClass.OBJECT,
        )
        assert report.form_class == FormClass.OBJECT

    def test_coverage_report_form_class_service(self):
        from v8unpack_agent.coverage_metric import CoverageReport
        report = CoverageReport(
            total_elements=43,
            data_elements=43,
            bound_data_elements=0,
            coverage_pct=0.0,
            form_class=FormClass.SERVICE,
        )
        assert report.form_class == FormClass.SERVICE

    def test_coverage_report_to_dict_includes_form_class(self):
        from v8unpack_agent.coverage_metric import CoverageReport
        report = CoverageReport(
            total_elements=10,
            data_elements=5,
            bound_data_elements=5,
            coverage_pct=100.0,
            form_class=FormClass.OBJECT,
        )
        d = report.to_dict()
        assert "form_class" in d
        assert d["form_class"] == "object"

    def test_coverage_report_default_form_class_is_unknown(self):
        """Обратная совместимость: старый код не передаёт form_class → UNKNOWN."""
        from v8unpack_agent.coverage_metric import CoverageReport
        report = CoverageReport(
            total_elements=5,
            data_elements=3,
            bound_data_elements=3,
            coverage_pct=100.0,
        )
        assert report.form_class == FormClass.UNKNOWN

    def test_coverage_report_str_includes_form_class(self):
        from v8unpack_agent.coverage_metric import CoverageReport
        report = CoverageReport(
            total_elements=10,
            data_elements=5,
            bound_data_elements=0,
            coverage_pct=0.0,
            form_class=FormClass.SERVICE,
        )
        assert "service" in str(report)


# ---------------------------------------------------------------------------
# calc_data_path_coverage с параметром form_name
# ---------------------------------------------------------------------------

class TestCalcDataPathCoverageWithFormName:
    """calc_data_path_coverage должна принимать form_name и проставлять form_class."""

    def test_form_name_pomoshnik_sets_service_class(self):
        from v8unpack_agent.coverage_metric import calc_data_path_coverage
        elements = [
            {"type": "Field", "data_path": "ЭтапПодключения"},
            {"type": "Field", "data_path": None},
        ]
        report = calc_data_path_coverage(elements, form_name="ПомощникПодключенияЭДО")
        assert report.form_class == FormClass.SERVICE

    def test_form_name_none_still_classifies_by_bindings(self):
        """Без имени — классификация только по привязкам."""
        from v8unpack_agent.coverage_metric import calc_data_path_coverage
        elements = [
            {"type": "Field", "data_path": "Объект.БИК"},
        ]
        report = calc_data_path_coverage(elements)
        assert report.form_class == FormClass.OBJECT

    def test_form_name_none_service_by_bindings(self):
        from v8unpack_agent.coverage_metric import calc_data_path_coverage
        elements = [
            {"type": "Field", "data_path": None},
        ] * 10
        report = calc_data_path_coverage(elements)
        assert report.form_class == FormClass.SERVICE
