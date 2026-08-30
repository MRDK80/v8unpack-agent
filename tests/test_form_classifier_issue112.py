"""Tests for issue #112 — структурное подтверждение SERVICE для форм без виджетов.

TDD-RED: все тесты должны ПАДАТЬ на main до расширения сигнатуры.

Задача:
    classify_no_widgets_form() принимает необязательный параметр
    has_data_widgets: bool | None = None.

    has_data_widgets вычисляет elem_parser (через _has_tabular_field),
    form_classifier остаётся чистым от JSON-структуры 1С.

Контракт (дополнение к #109):

    has_data_widgets=False  + reason==NO_TABULAR_NO_WIDGETS
        + имя даёт "by_service_pattern" / "empty_tree_name_hint"
        → SERVICE  (двойное структурное подтверждение)

    has_data_widgets=None   + любой reason/имя
        → поведение PR #111 (только эвристика имени) — обратная совместимость

    has_data_widgets=True   + reason==NO_TABULAR_NO_WIDGETS
        → UNKNOWN  (конфликт сигналов: reason говорит «нет виджетов»,
                    флаг говорит «есть» — не угадываем)

    has_data_widgets=False  + reason!=NO_TABULAR_NO_WIDGETS
        → UNKNOWN  (reason не подтверждает категорию C)

Обезличенность: все имена форм — нейтральные синтетические паттерны.
"""
from __future__ import annotations

import pytest

from v8unpack_agent.elem_parser import UnindexedReason
from v8unpack_agent.form_classifier import FormClass, classify_no_widgets_form

# ---------------------------------------------------------------------------
# 1. has_data_widgets=False — двойное подтверждение → SERVICE
# ---------------------------------------------------------------------------

class TestStructuralConfirmationService:
    """has_data_widgets=False усиливает уверенность: виджетов нет структурно."""

    @pytest.mark.parametrize("form_name", [
        "ФормаНастройкиПодключения",
        "ФормаВыбораОтчета",
        "ФормаЗапросаПерезаписи",
        "ФормаПечатиДокумента",
        "НастройкаПараметровОбмена",
        "ВыборТипаЦен",
        "ЗапросПарольяПочты",
        "ПросмотрИнформации",
        "ПечатьЭтикеток",
        "ЗагрузкаИзФайла",
        "ВыгрузкаВФайл",
    ])
    def test_hint_name_with_false_widgets_gives_service(
        self, form_name: str
    ):
        """empty_tree_name_hint + has_data_widgets=False + NO_TABULAR_NO_WIDGETS → SERVICE."""
        result = classify_no_widgets_form(
            form_name=form_name,
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.SERVICE, (
            f"{form_name!r}: ожидался SERVICE, получен {result!r}"
        )

    @pytest.mark.parametrize("form_name", [
        "ПомощникПодключенияЭДО",
        "МастерЗаполненияДанных",
        "ДиалогВыбораПериода",
        "РегистрацияВРеестреФНС",
    ])
    def test_service_pattern_with_false_widgets_gives_service(
        self, form_name: str
    ):
        """by_service_pattern + has_data_widgets=False + NO_TABULAR_NO_WIDGETS → SERVICE."""
        result = classify_no_widgets_form(
            form_name=form_name,
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.SERVICE, (
            f"{form_name!r}: ожидался SERVICE, получен {result!r}"
        )


# ---------------------------------------------------------------------------
# 2. has_data_widgets=True — конфликт сигналов → UNKNOWN
# ---------------------------------------------------------------------------

class TestConflictingSignalsUnknown:
    """reason говорит «нет виджетов», флаг говорит «есть» — конфликт."""

    @pytest.mark.parametrize("form_name", [
        "ФормаНастройки",
        "ПомощникПодключения",
        "НастройкаПараметров",
    ])
    def test_true_widgets_with_no_tabular_reason_gives_unknown(
        self, form_name: str
    ):
        result = classify_no_widgets_form(
            form_name=form_name,
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=True,
        )
        assert result == FormClass.UNKNOWN, (
            f"{form_name!r}: ожидался UNKNOWN (конфликт), получен {result!r}"
        )

    def test_true_widgets_service_pattern_still_unknown(self):
        """Даже надёжный SERVICE_FORM_NAME_PATTERNS не помогает при конфликте."""
        result = classify_no_widgets_form(
            form_name="МастерЗаполнения",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=True,
        )
        assert result == FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# 3. has_data_widgets=False + wrong reason → UNKNOWN
# ---------------------------------------------------------------------------

class TestFalseWidgetsWrongReasonUnknown:
    """Флаг False не помогает, если reason не NO_TABULAR_NO_WIDGETS."""

    @pytest.mark.parametrize("reason", [
        UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP,
        UnindexedReason.TABULAR_FIELD_NO_UUID_HITS,
        UnindexedReason.NO_LEGACY_JSON,
        UnindexedReason.UNKNOWN,
    ])
    def test_false_widgets_wrong_reason_gives_unknown(self, reason: UnindexedReason):
        result = classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=reason,
            has_data_widgets=False,
        )
        assert result == FormClass.UNKNOWN, (
            f"reason={reason!r}: ожидался UNKNOWN, получен {result!r}"
        )


# ---------------------------------------------------------------------------
# 4. has_data_widgets=None — обратная совместимость с PR #111
# ---------------------------------------------------------------------------

class TestNoneWidgetsBackwardCompatibility:
    """has_data_widgets=None = поведение PR #111 без изменений."""

    def test_none_service_pattern_gives_service(self):
        """Поведение PR #111: by_service_pattern → SERVICE."""
        result = classify_no_widgets_form(
            form_name="ПомощникПодключения",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=None,
        )
        assert result == FormClass.SERVICE

    def test_none_hint_name_gives_service(self):
        """Поведение PR #111: empty_tree_name_hint → SERVICE."""
        result = classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=None,
        )
        assert result == FormClass.SERVICE

    def test_none_unknown_pattern_gives_unknown(self):
        result = classify_no_widgets_form(
            form_name="НекийНеизвестныйОбъект",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=None,
        )
        assert result == FormClass.UNKNOWN

    def test_none_platform_name_gives_unknown(self):
        result = classify_no_widgets_form(
            form_name="ФормаЗаписи",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=None,
        )
        assert result == FormClass.UNKNOWN

    def test_positional_call_unchanged(self):
        """Вызов без has_data_widgets (2 аргумента) работает как раньше."""
        result = classify_no_widgets_form(
            "ПомощникПодключения",
            UnindexedReason.NO_TABULAR_NO_WIDGETS,
        )
        assert result == FormClass.SERVICE


# ---------------------------------------------------------------------------
# 5. Защитные случаи (платформенные имена, пустое имя)
# ---------------------------------------------------------------------------

class TestGuardCasesUnknown:

    def test_platform_name_false_widgets_gives_unknown(self):
        """ФормаЗаписи + has_data_widgets=False — нераспарсенная объектная форма."""
        result = classify_no_widgets_form(
            form_name="ФормаЗаписи",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.UNKNOWN

    def test_platform_forma_false_widgets_gives_unknown(self):
        result = classify_no_widgets_form(
            form_name="Форма",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.UNKNOWN

    def test_empty_name_false_widgets_gives_unknown(self):
        result = classify_no_widgets_form(
            form_name="",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.UNKNOWN

    def test_unknown_name_false_widgets_gives_unknown(self):
        """Имя не в паттернах + has_data_widgets=False → UNKNOWN (не угадываем)."""
        result = classify_no_widgets_form(
            form_name="НекийНеизвестныйОбъект",
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            has_data_widgets=False,
        )
        assert result == FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# 6. Чистота: входные данные не мутируются
# ---------------------------------------------------------------------------

class TestPurity:

    def test_does_not_mutate_reason(self):
        reason = UnindexedReason.NO_TABULAR_NO_WIDGETS
        classify_no_widgets_form(
            form_name="ФормаНастройки",
            reason=reason,
            has_data_widgets=False,
        )
        assert reason == UnindexedReason.NO_TABULAR_NO_WIDGETS

    def test_deterministic_false_widgets(self):
        args = {
            "form_name": "ФормаНастройки",
            "reason": UnindexedReason.NO_TABULAR_NO_WIDGETS,
            "has_data_widgets": False,
        }
        results = {classify_no_widgets_form(**args) for _ in range(5)}
        assert len(results) == 1

    def test_deterministic_true_widgets(self):
        args = {
            "form_name": "ФормаНастройки",
            "reason": UnindexedReason.NO_TABULAR_NO_WIDGETS,
            "has_data_widgets": True,
        }
        results = {classify_no_widgets_form(**args) for _ in range(5)}
        assert len(results) == 1
