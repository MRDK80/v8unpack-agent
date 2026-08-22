"""Синтетические тесты для catalog_resolver (issue #76, обновлено в #148).

Покрывают:
- успешную резолюцию реквизита верхнего уровня;
- отсутствие Catalog.json → resolved=False, без исключений;
- нераспознанный путь (реквизит не найден) → resolved=False;
- вложенный путь Объект.ТЧ.Реквизит → успешная резолюция;
- object_json_path: корректно находит JSON-файл объекта по FormEntry.

Обновление #148: фикстура ``catalog_json`` раньше содержала нормализованный
JSON (``{"Properties": [...], "TabularSections": [...]}``) — формат, который
``resolve_data_path`` читала собственным ``json.loads``. Реальная выгрузка
v8unpack содержит верхнеуровневый ключ ``header``, и теперь резолюция идёт
через ``object_decoder.decode_object_attributes``. Поэтому фикстура
переведена на raw-header, а ожидаемые имя/тип/синоним выводятся из декодера,
а не хардкодятся: тесты проверяют согласованность двух модулей.

Сама raw-header структура не дублируется — она переиспользуется из
tests/test_object_decoder.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from v8unpack_agent.catalog_resolver import (
    ResolvedBinding,
    clear_object_cache,
    object_json_path,
    resolve_data_path,
)
from v8unpack_agent.object_decoder import decode_object_attributes

try:  # зависит от rootdir/conftest
    from tests.test_object_decoder import MINIMAL_CATALOG_WITH_TS
except ImportError:  # pragma: no cover
    from test_object_decoder import MINIMAL_CATALOG_WITH_TS


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Кэш декодирования не должен протекать между тестами."""
    clear_object_cache()
    yield
    clear_object_cache()


@pytest.fixture()
def catalog_json(tmp_path: Path) -> Path:
    """Production-подобный Catalog.json: raw-header, как в выгрузке v8unpack."""
    # Путь: cf_export/Catalog/Банки/Catalog.json
    catalog_dir = tmp_path / "Catalog" / "Банки"
    catalog_dir.mkdir(parents=True)
    json_file = catalog_dir / "Catalog.json"
    json_file.write_text(
        json.dumps(MINIMAL_CATALOG_WITH_TS, ensure_ascii=False), encoding="utf-8"
    )
    return json_file


@pytest.fixture()
def decoded(catalog_json: Path) -> dict:
    """Нормализованное представление того же файла из object_decoder."""
    result = decode_object_attributes(catalog_json)
    assert result.ok, "raw-header фикстура должна декодироваться"
    return result.data


@pytest.fixture()
def top_properties(decoded: dict) -> list[dict]:
    props = decoded.get("Properties") or []
    assert props, "фикстура должна содержать верхнеуровневые реквизиты"
    return props


@pytest.fixture()
def tabular_pair(decoded: dict) -> tuple[dict, dict]:
    for section in decoded.get("TabularSections") or []:
        props = section.get("Properties") or []
        if props:
            return section, props[0]
    pytest.skip("в фикстуре нет табличной части с реквизитами")


def _make_form_entry(form_path: Path) -> MagicMock:
    """Минимальная заглушка FormEntry."""
    entry = MagicMock()
    entry.form_path = form_path
    return entry


# ---------------------------------------------------------------------------
# Фикстура действительно raw-header, а не нормализованный JSON (#148)
# ---------------------------------------------------------------------------


def test_fixture_uses_raw_header_layout(catalog_json: Path) -> None:
    """Иначе тесты снова проверяли бы формат, которого нет в выгрузке."""
    raw = json.loads(catalog_json.read_text(encoding="utf-8"))

    assert "header" in raw
    for legacy_key in ("Properties", "Attributes", "props", "attributes"):
        assert legacy_key not in raw


# ---------------------------------------------------------------------------
# resolve_data_path: успешная резолюция реквизита верхнего уровня
# ---------------------------------------------------------------------------


def test_resolve_top_level_attribute(
    catalog_json: Path, top_properties: list[dict]
) -> None:
    prop = top_properties[0]
    data_path = f"Банки.{prop['Name']}"

    binding = resolve_data_path(data_path, catalog_json)

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is True
    assert binding.object_type == "Catalog"
    assert binding.attribute_name == prop["Name"]
    assert binding.value_type == prop["Type"]
    assert binding.synonym == prop["Synonym"]
    assert binding.data_path == data_path


def test_resolve_every_top_level_attribute(
    catalog_json: Path, top_properties: list[dict]
) -> None:
    """Все реквизиты, которые видит декодер, должны резолвиться резолвером."""
    for prop in top_properties:
        binding = resolve_data_path(f"Банки.{prop['Name']}", catalog_json)

        assert binding.resolved is True, prop["Name"]
        assert binding.value_type == prop["Type"]


def test_resolve_top_level_attribute_ignores_case(
    catalog_json: Path, top_properties: list[dict]
) -> None:
    """Имена метаданных 1С регистронезависимы."""
    prop = top_properties[0]
    binding = resolve_data_path(f"Банки.{str(prop['Name']).upper()}", catalog_json)

    assert binding.resolved is True
    assert binding.value_type == prop["Type"]


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


def test_resolve_broken_json_does_not_raise(tmp_path: Path) -> None:
    """Повреждённый файл объекта — тоже штатный best-effort случай (#148)."""
    broken = tmp_path / "Catalog" / "Банки" / "Catalog.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{ это не json", encoding="utf-8")

    binding = resolve_data_path("Банки.Наименование", broken)

    assert binding.resolved is False
    assert binding.value_type is None


# ---------------------------------------------------------------------------
# resolve_data_path: нераспознанный путь (реквизит не найден)
# ---------------------------------------------------------------------------


def test_resolve_unknown_attribute(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.НесуществующийРеквизит", catalog_json)

    assert binding.resolved is False
    assert binding.attribute_name == "НесуществующийРеквизит"
    assert binding.value_type is None
    assert binding.synonym is None


def test_resolve_single_segment_path(catalog_json: Path) -> None:
    """Путь из одного сегмента — нераспознан (нет разделителя)."""
    binding = resolve_data_path("ТолькоОбъект", catalog_json)
    assert binding.resolved is False


# ---------------------------------------------------------------------------
# resolve_data_path: вложенный путь Объект.ТЧ.Реквизит
# ---------------------------------------------------------------------------


def test_resolve_nested_tabular_attribute(
    catalog_json: Path, tabular_pair: tuple[dict, dict]
) -> None:
    section, prop = tabular_pair
    data_path = f"Банки.{section['Name']}.{prop['Name']}"

    binding = resolve_data_path(data_path, catalog_json)

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is True
    assert binding.attribute_name == prop["Name"]
    assert binding.value_type == prop["Type"]
    assert binding.synonym == prop["Synonym"]
    assert binding.data_path == data_path


def test_resolve_nested_unknown_tabular_part(catalog_json: Path) -> None:
    binding = resolve_data_path("Банки.НесуществующаяТЧ.Реквизит", catalog_json)
    assert binding.resolved is False


def test_resolve_nested_unknown_attribute_in_tabular(
    catalog_json: Path, tabular_pair: tuple[dict, dict]
) -> None:
    section, _ = tabular_pair
    binding = resolve_data_path(
        f"Банки.{section['Name']}.НесущРеквизит", catalog_json
    )

    assert binding.resolved is False
    assert binding.value_type is None


def test_tabular_attribute_is_not_resolved_as_top_level(
    catalog_json: Path, top_properties: list[dict], tabular_pair: tuple[dict, dict]
) -> None:
    """Реквизит табличной части не должен находиться на верхнем уровне."""
    _, prop = tabular_pair
    top_names = {str(p.get("Name", "")).casefold() for p in top_properties}
    if str(prop["Name"]).casefold() in top_names:
        pytest.skip("имя реквизита ТЧ совпадает с верхнеуровневым")

    assert resolve_data_path(f"Банки.{prop['Name']}", catalog_json).resolved is False


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
