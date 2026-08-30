"""Tests for issue #109 — сервисные формы без виджетов данных (NO_TABULAR_NO_WIDGETS).

TDD-RED: все тесты должны ПАДАТЬ на main до реализации.

Проблема:
    17 форм категории C (NO_TABULAR_NO_WIDGETS) получают FormClass.UNKNOWN,
    хотя структурно подтверждены как сервисные: JSON < 1 KB, нет виджетов
    данных, владельцы — DataProcessor / Catalog / CommonForm / Document.
    Визуальная проверка подтверждена автором репозитория.

Контракт classify_no_widgets_form:
    Вызывается только когда UnindexedReason == NO_TABULAR_NO_WIDGETS
    (т.е. legacy JSON прочитан и реально не содержит TabularField/InputField).

    Правило (AND):
        1. имя даёт classify_empty_tree_form() → (_, "empty_tree_name_hint")
           ИЛИ (_, "by_service_pattern")
        2. reason == NO_TABULAR_NO_WIDGETS
    → SERVICE

    Отказать в SERVICE (→ UNKNOWN) если:
        - reason != NO_TABULAR_NO_WIDGETS
        - пустое/отсутствующее имя
        - имя → "platform_object_name_unparsed"
        - имя → "unparsed_empty_tree" (неизвестный паттерн)

Обезличенность:
    Все синтетические имена форм — нейтральные паттерны без реальных имён
    объектов конфигурации.
"""
from __future__ import annotations

import pytest

from v8unpack_agent.elem_parser import UnindexedReason
from v8unpack_agent.form_classifier import (
    FormClass,
    classify_form,
    classify_form_by_name,
    classify_empty_tree_form,
)


# ---------------------------------------------------------------------------
# Импорт целевой функции (ещё не существует → RED)
# ---------------------------------------------------------------------------

def _import_classify_no_widgets():
    """Отложенный импорт: функция появится только после реализации."""
    from v8unpack_agent.form_classifier import classify_no_widgets_form
    return classify_no_widgets_form


# ---------------------------------------------------------------------------
# 1. Функция существует и импортируется
# ---------------------------------------------------------------------------

class TestClassifyNoWidgetsFormExists:
    def test_function_importable(self):
        """classify_no_widgets_form должна экспортироваться из form_classifier."""
        classify_no_widgets_form = _import_classify_no_widgets()
        assert callable(classify_no_widgets_form)


# ---------------------------------------------------------------------------
# 2. Подтверждённые сервисные формы → SERVICE
# ---------------------------------------------------------------------------

class TestClassifyNoWidgetsFormService:
    """Синтетические имена с паттернами из EMPTY_TREE_NAME_HINTS + NO_TABULAR_NO_WIDGETS."""

    @pytest.mark.parametrize("form_name", [
        # «форма» + уточнение — типичный паттерн 17 форм
        "ФормаЗапросаПерезаписи",
        "ФормаНастройкиПодключения",
        "ФормаВыбораОтчета",
        "ФормаРедактированияТекста",
        "ФормаПечатиДокумента",
        "ФормаИнформацииОПочте",
        "ФормаПросмотраСообщений",
        "ХодВыполненияОбработки",
        "НастройкаПараметровОбмена",
        "ВыборТипаЦен",
        "ЗапросПарольяПочты",
        "РедактированиеТекстаПисьма",
        "ПросмотрИнформации",
        "ПечатьЭтикеток",
        "ВыгрузкаВФайл",
        "ЗагрузкаИзФайла",
        "ПроверкаКонтрагентаОнлайн",
    ])
    def test_service_pattern_with_no_widgets_reason_gives_service(
        self, form_name: str
    ):
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name=form_name,
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.SERVICE, (
            f"{form_name!r}: ожидался SERVICE, получен {result!r}"
        )

    def test_by_service_pattern_name_also_gives_service(self):
        """Имя начинается с SERVICE_FORM_NAME_PATTERNS → SERVICE даже без hint."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ПомощникПодключенияЭДО",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.SERVICE

    def test_dialog_name_with_no_widgets_reason_gives_service(self):
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ДиалогВыбораПериода",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.SERVICE


# ---------------------------------------------------------------------------
# 3. Защитные случаи → UNKNOWN (нельзя назначить SERVICE)
# ---------------------------------------------------------------------------

class TestClassifyNoWidgetsFormUnknown:

    def test_wrong_reason_stays_unknown(self):
        """reason != NO_TABULAR_NO_WIDGETS → UNKNOWN, даже если имя сервисное."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP,
        )
        assert result == FormClass.UNKNOWN

    def test_empty_name_stays_unknown(self):
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.UNKNOWN

    def test_platform_object_name_stays_unknown(self):
        """ФормаЗаписи + NO_TABULAR_NO_WIDGETS → нераспарсенная объектная форма."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ФормаЗаписи",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.UNKNOWN

    def test_platform_object_forma_stays_unknown(self):
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="Форма",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.UNKNOWN

    def test_unknown_name_pattern_stays_unknown(self):
        """Имя не распознано ни как сервисное, ни как hint → UNKNOWN."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="НекийНеизвестныйОбъект",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.UNKNOWN

    def test_damaged_json_reason_stays_unknown(self):
        """UNKNOWN резон → не угадываем SERVICE."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=UnindexedReason.UNKNOWN,
        )
        assert result == FormClass.UNKNOWN

    def test_no_legacy_json_reason_stays_unknown(self):
        """Нет legacy JSON — нечего проверять, не можем дать SERVICE."""
        classify_no_widgets_form = _import_classify_no_widgets()
        result = classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=UnindexedReason.NO_LEGACY_JSON,
        )
        assert result == FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# 4. Детерминизм и чистота
# ---------------------------------------------------------------------------

class TestClassifyNoWidgetsFormPurity:

    def test_deterministic_repeated_calls(self):
        """Повторные вызовы с теми же аргументами дают одинаковый результат."""
        classify_no_widgets_form = _import_classify_no_widgets()
        args = dict(form_name="ФормаНастройки", reason=UnindexedReason.NO_TABULAR_NO_WIDGETS)
        results = {classify_no_widgets_form(**args) for _ in range(5)}
        assert len(results) == 1

    def test_does_not_mutate_reason(self):
        """Функция не изменяет переданный reason (enum — immutable, но проверяем контракт)."""
        classify_no_widgets_form = _import_classify_no_widgets()
        reason = UnindexedReason.NO_TABULAR_NO_WIDGETS
        classify_no_widgets_form(form_name="ФормаНастройки", reason=reason)
        assert reason == UnindexedReason.NO_TABULAR_NO_WIDGETS


# ---------------------------------------------------------------------------
# 5. Регрессии существующих сценариев classify_form
# ---------------------------------------------------------------------------

class TestClassifyFormRegression:
    """classify_form не должна измениться — только добавляется новая функция."""

    def test_empty_elements_still_unknown(self):
        """classify_form([]) → UNKNOWN — контракт не нарушен."""
        assert classify_form("ФормаНастройки", []) == FormClass.UNKNOWN

    def test_objekt_path_still_object(self):
        elements = [{"type": "Field", "data_path": "Объект.Реквизит"}]
        assert classify_form("ФормаЭлемента", elements) == FormClass.OBJECT

    def test_service_name_with_elements_still_service(self):
        elements = [{"type": "Field", "data_path": None}]
        assert classify_form("ПомощникПодключения", elements) == FormClass.SERVICE

    def test_no_widgets_form_no_change_in_classify_form(self):
        """classify_form не получает UnindexedReason — пустой список → UNKNOWN.
        Изменение касается только classify_no_widgets_form.
        """
        assert classify_form("ФормаНастройки", []) == FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# 6. classify_empty_tree_form регрессии (не должны сломаться)
# ---------------------------------------------------------------------------

class TestClassifyEmptyTreeFormRegression:

    def test_service_pattern_still_service(self):
        fc, reason = classify_empty_tree_form("ПомощникПодключения")
        assert fc == FormClass.SERVICE
        assert reason == "by_service_pattern"

    def test_platform_name_still_unknown(self):
        fc, reason = classify_empty_tree_form("ФормаЗаписи")
        assert fc == FormClass.UNKNOWN
        assert reason == "platform_object_name_unparsed"

    def test_hint_name_still_unknown(self):
        """classify_empty_tree_form сама по себе не меняется — hint остаётся UNKNOWN."""
        fc, reason = classify_empty_tree_form("ФормаНастройки")
        assert fc == FormClass.UNKNOWN
        assert reason == "empty_tree_name_hint"

    def test_unknown_pattern_still_unknown(self):
        fc, reason = classify_empty_tree_form("НекийОбъект")
        assert fc == FormClass.UNKNOWN
        assert reason == "unparsed_empty_tree"
