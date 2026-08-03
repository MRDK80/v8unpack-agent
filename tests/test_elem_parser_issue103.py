"""TDD-тесты для issue #103: ФормаСписка/ФормаВыбора с TabularField.

Проверяем:
1. extract_legacy_list_form_elements корректно извлекает имена колонок
   из TabularField по маппингу числовых индексов на реквизиты объекта.
2. Label (0fc7e20d-...) и CommandBar (e69bf21d-...) не включаются.
3. Дубликаты схлопываются.
4. Фолбэк в parse_elem_json переключается на новый экстрактор,
   когда elem.json пуст и big-json содержит TabularField.
5. CommonForm (без объекта-владельца) возвращает пустой список без ошибки.
6. obj_json=None / пустой — не падает, возвращает пустой список.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from v8unpack_agent.elem_parser import extract_legacy_list_form_elements


# ---------------------------------------------------------------------------
# Константы UUID (взяты из elem_parser / документации issue #103)
# ---------------------------------------------------------------------------
_TABULAR_FIELD_UUID = "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1"
_INPUT_FIELD_UUID   = "381ed624-9217-4e63-85db-c4c3cb87daae"
_LABEL_UUID         = "0fc7e20d-e3ed-4dc4-a20d-4d3c5e27e4f4"
_COMMAND_BAR_UUID   = "e69bf21d-6d62-4c1e-9bea-b1f6e71f77e4"


# ---------------------------------------------------------------------------
# Вспомогательные фабрики фикстур
# ---------------------------------------------------------------------------

def _make_tabular_field_block(column_indices: list[int]) -> list:
    """Минимальный блок TabularField с числовыми индексами колонок.

    Структура (упрощённая):
      [UUID, ..., [...columns...]]
    Колонка: [str(index), <...padding...>]
    """
    columns = [[str(idx), "0", "0"] for idx in column_indices]
    return [_TABULAR_FIELD_UUID, "0", "0", "0", columns]


def _make_obj_json(attributes: list[tuple[int, str]]) -> dict:
    """Минимальный obj_json с маппингом index -> name.

    attributes: список (numeric_index, attribute_name)
    """
    return {
        "TabularFieldAttributeMap": {
            str(idx): name for idx, name in attributes
        }
    }


# ---------------------------------------------------------------------------
# Тест 1: базовый happy-path — индексы маппятся на имена
# ---------------------------------------------------------------------------

class TestExtractLegacyListFormElementsBasic:

    def test_column_names_resolved(self):
        """Числовые индексы колонок разрешаются в имена реквизитов."""
        form_json = _make_tabular_field_block([10, 11, 3])
        obj_json = _make_obj_json([(10, "Наименование"), (11, "Код"), (3, "Владелец")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        names = [el["name"] for el in result]
        assert "Наименование" in names
        assert "Код" in names
        assert "Владелец" in names

    def test_source_marker(self):
        """Каждый элемент содержит source='legacy_list_form_json'."""
        form_json = _make_tabular_field_block([10])
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        assert result
        assert all(el["source"] == "legacy_list_form_json" for el in result)

    def test_type_is_tabular_column(self):
        """type каждого элемента — 'TabularFieldColumn'."""
        form_json = _make_tabular_field_block([10])
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        assert result
        assert all(el["type"] == "TabularFieldColumn" for el in result)

    def test_data_path_prefix(self):
        """data_path формируется как 'Список.<Имя>'."""
        form_json = _make_tabular_field_block([10])
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        assert result
        assert result[0]["data_path"] == "Список.Наименование"


# ---------------------------------------------------------------------------
# Тест 2: Label и CommandBar не попадают в результат
# ---------------------------------------------------------------------------

class TestExcludeNonDataWidgets:

    def test_label_excluded(self):
        """Виджет Label (0fc7e20d-...) не включается в результат."""
        form_json = [
            [_TABULAR_FIELD_UUID, "0", "0", "0", [["10", "0", "0"]]],
            [_LABEL_UUID, "0", "0", "0", [["10", "0", "0"]]],
        ]
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        assert all(el["source"] == "legacy_list_form_json" for el in result)
        types = [el["type"] for el in result]
        assert "Label" not in types

    def test_command_bar_excluded(self):
        """Виджет CommandBar (e69bf21d-...) не включается в результат."""
        form_json = [
            [_TABULAR_FIELD_UUID, "0", "0", "0", [["10", "0", "0"]]],
            [_COMMAND_BAR_UUID, "0", "0", "0", [["10", "0", "0"]]],
        ]
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        types = [el["type"] for el in result]
        assert "CommandBar" not in types


# ---------------------------------------------------------------------------
# Тест 3: Дубликаты схлопываются
# ---------------------------------------------------------------------------

class TestDeduplication:

    def test_duplicates_collapsed(self):
        """Два TabularField-блока с одинаковым индексом → один элемент."""
        form_json = [
            [_TABULAR_FIELD_UUID, "0", "0", "0", [["10", "0", "0"]]],
            [_TABULAR_FIELD_UUID, "0", "0", "0", [["10", "0", "0"]]],
        ]
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        names = [el["name"] for el in result]
        assert names.count("Наименование") == 1


# ---------------------------------------------------------------------------
# Тест 4: Индекс без маппинга в obj_json — пропускается
# ---------------------------------------------------------------------------

class TestUnmappedIndex:

    def test_unmapped_index_skipped(self):
        """Индекс, отсутствующий в obj_json, не порождает элемент."""
        form_json = _make_tabular_field_block([10, 99])
        obj_json = _make_obj_json([(10, "Наименование")])  # 99 нет

        result = extract_legacy_list_form_elements(form_json, obj_json)

        names = [el["name"] for el in result]
        assert "Наименование" in names
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Тест 5: obj_json=None / пустой — не падает
# ---------------------------------------------------------------------------

class TestNullObjJson:

    def test_obj_json_none_returns_empty(self):
        form_json = _make_tabular_field_block([10])
        result = extract_legacy_list_form_elements(form_json, None)
        assert result == []

    def test_obj_json_empty_dict_returns_empty(self):
        form_json = _make_tabular_field_block([10])
        result = extract_legacy_list_form_elements(form_json, {})
        assert result == []

    def test_form_json_none_returns_empty(self):
        result = extract_legacy_list_form_elements(None, _make_obj_json([(10, "Наименование")]))
        assert result == []


# ---------------------------------------------------------------------------
# Тест 6: вложенная структура — TabularField глубоко в дереве
# ---------------------------------------------------------------------------

class TestNestedTabularField:

    def test_nested_tabular_field_found(self):
        """TabularField, вложенный в несколько уровней, тоже находится."""
        inner = [_TABULAR_FIELD_UUID, "0", "0", "0", [["10", "0", "0"]]]
        form_json = {"root": {"group": {"panel": [inner]}}}
        obj_json = _make_obj_json([(10, "Наименование")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        names = [el["name"] for el in result]
        assert "Наименование" in names


# ---------------------------------------------------------------------------
# Тест 7: порядок колонок сохраняется
# ---------------------------------------------------------------------------

class TestColumnOrder:

    def test_column_order_preserved(self):
        """Порядок колонок из TabularField сохраняется в результате."""
        form_json = _make_tabular_field_block([10, 11, 3])
        obj_json = _make_obj_json([(10, "Наименование"), (11, "Код"), (3, "Владелец")])

        result = extract_legacy_list_form_elements(form_json, obj_json)

        names = [el["name"] for el in result]
        assert names == ["Наименование", "Код", "Владелец"]
