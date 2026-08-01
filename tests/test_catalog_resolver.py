"""Синтетические тесты для catalog_resolver (issue #76).

Покрывают:
- успешную резолюцию реквизита верхнего уровня;
- отсутствие Catalog.json → resolved=False, без исключений;
- нераспознанный путь (реквизит не найден) → resolved=False;
- вложенный путь Объект.ТЧ.Реквизит → успешная резолюция;
- object_json_path: корректно находит JSON-файл объекта по FormEntry.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from v8unpack_agent.catalog_resolver import (
    ResolvedBinding,
    object_json_path,
    resolve_data_path,
)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def catalog_json(tmp_path: Path) -> Path:
    """Синтетический Catalog.json с реквизитами и табличной частью."""
    data = {
        "Name": "Банки",
        "Properties": [
            {
                "Name": "Наименование",
                "Type": "String",
                "Synonym": "Наименование банка",
            },
            {
                "Name": "КорСчёт",
                "Type": "String",
                "Synonym": "Кор. счёт",
            },
        ],
        "TabularSections": [
            {
                "Name": "КонтактнаяИнформация",
                "Properties": [
                    {
                        "Name": "Тип",
                        "Type": "EnumRef.ТипыКонтактнойИнформации",
                        "Synonym": "Тип контакта",
                    },
                    {
                        "Name": "Представление",
                        "Type": "String",
                        "Synonym": None,
                    },
                ],
            }
        ],
    }
    # Путь: cf_export/Catalog/Банки/Catalog.json
    catalog_dir = tmp_path / "Catalog" / "Банки"
    catalog_dir.mkdir(parents=True)
    json_file = catalog_dir / "Catalog.json"
    json_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return json_file


def _make_form_entry(form_path: Path) -> MagicMock:
    """Минимальная заглушка FormEntry."""
    entry = MagicMock()
    entry.form_path = form_path
    return entry


# ---------------------------------------------------------------------------
# resolve_data_path: успешная резолюция реквизита верхнего уровня
# ---------------------------------------------------------------------------


def test_resolve_top_level_attribute(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.Наименование", catalog_json)

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is True
    assert binding.attribute_name == "Наименование"
    assert binding.value_type == "String"
    assert binding.synonym == "Наименование банка"
    assert binding.data_path == "Банки.Наименование"


def test_resolve_another_top_level_attribute(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.КорСчёт", catalog_json)

    assert binding.resolved is True
    assert binding.attribute_name == "КорСчёт"
    assert binding.value_type == "String"


# ---------------------------------------------------------------------------
# resolve_data_path: отсутствие Catalog.json → resolved=False, нет исключений
# ---------------------------------------------------------------------------


def test_resolve_missing_catalog_json(tmp_path: Path) -> None:
    missing = tmp_path / "Catalog" / "Несуществующий" / "Catalog.json"
    binding = resolve_data_path("Несуществующий.Реквизит", missing)

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is False
    assert binding.value_type is None
    assert binding.synonym is None


def test_resolve_missing_does_not_raise(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_file.json"
    # Не должно бросать исключение
    binding = resolve_data_path("Объект.Реквизит", missing)
    assert binding.resolved is False


# ---------------------------------------------------------------------------
# resolve_data_path: нераспознанный путь (реквизит не найден)
# ---------------------------------------------------------------------------


def test_resolve_unknown_attribute(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.НесуществующийРеквизит", catalog_json)

    assert binding.resolved is False
    assert binding.attribute_name == "НесуществующийРеквизит"


def test_resolve_single_segment_path(catalog_json: Path) -> None:
    """Путь из одного сегмента — нераспознан (нет разделителя)."""
    binding = resolve_data_path("ТолькоОбъект", catalog_json)
    assert binding.resolved is False


# ---------------------------------------------------------------------------
# resolve_data_path: вложенный путь Объект.ТЧ.Реквизит
# ---------------------------------------------------------------------------


def test_resolve_nested_tabular_attribute(catalog_json: Path) -> None:
    binding = resolve_data_path(
        "Банки.КонтактнаяИнформация.Тип", catalog_json
    )

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is True
    assert binding.attribute_name == "Тип"
    assert binding.value_type == "EnumRef.ТипыКонтактнойИнформации"
    assert binding.synonym == "Тип контакта"


def test_resolve_nested_unknown_tabular_part(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.НесуществующаяТЧ.Реквизит", catalog_json)
    assert binding.resolved is False


def test_resolve_nested_unknown_attribute_in_tabular(catalog_json: Path) -> None:
    binding = resolve_data_path(
        "Банки.КонтактнаяИнформация.НесущРеквизит", catalog_json
    )
    assert binding.resolved is False


# ---------------------------------------------------------------------------
# object_json_path: находит JSON-файл объекта по FormEntry
# ---------------------------------------------------------------------------


def test_object_json_path_finds_object_json(tmp_path: Path) -> None:
    # Структура: cf_export/Catalog/Банки/CatalogForm/ФормаЭлемента/
    form_path = tmp_path / "Catalog" / "Банки" / "CatalogForm" / "ФормаЭлемента"
    form_path.mkdir(parents=True)

    # Создаём Банки.json на уровне объекта
    object_dir = tmp_path / "Catalog" / "Банки"
    object_json = object_dir / "Банки.json"
    object_json.write_text('{"Name": "Банки"}', encoding="utf-8")

    entry = _make_form_entry(form_path)
    result = object_json_path(entry)

    assert result is not None
    assert result == object_json


def test_object_json_path_fallback_to_type_name(tmp_path: Path) -> None:
    # Структура: cf_export/Catalog/Банки/CatalogForm/ФормаЭлемента/
    form_path = tmp_path / "Catalog" / "Банки" / "CatalogForm" / "ФормаЭлемента"
    form_path.mkdir(parents=True)

    # Файл с именем типа, а не объекта
    object_dir = tmp_path / "Catalog" / "Банки"
    type_json = object_dir / "Catalog.json"
    type_json.write_text('{"Name": "Банки"}', encoding="utf-8")

    entry = _make_form_entry(form_path)
    result = object_json_path(entry)

    assert result is not None
    assert result == type_json


def test_object_json_path_returns_none_when_missing(tmp_path: Path) -> None:
    form_path = tmp_path / "Catalog" / "Банки" / "CatalogForm" / "ФормаЭлемента"
    form_path.mkdir(parents=True)
    # Никаких .json файлов нет

    entry = _make_form_entry(form_path)
    result = object_json_path(entry)

    assert result is None


def test_object_json_path_returns_none_on_invalid_entry() -> None:
    """Невалидный form_path → None без исключений."""
    entry = MagicMock()
    entry.form_path = Path("/nonexistent/path/that/does/not/exist")
    result = object_json_path(entry)
    assert result is None
