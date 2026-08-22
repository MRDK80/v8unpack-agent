"""resolve_data_path на production raw-header layout (issue #148).

Фикстура raw-header переиспользуется из tests/test_object_decoder.py, чтобы
в проекте не появилось второго описания структуры ``header``. Ожидаемые
имя/тип/синоним выводятся из ``decode_object_attributes``, а не хардкодятся:
тест проверяет согласованность object_decoder -> catalog_resolver.

Все данные синтетические: реальная выгрузка, UUID и абсолютные пути не нужны.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from v8unpack_agent.catalog_resolver import (
    ResolvedBinding,
    clear_object_cache,
    resolve_data_path,
)
from v8unpack_agent.object_decoder import decode_object_attributes

try:  # зависит от rootdir/conftest
    from tests.test_object_decoder import MINIMAL_CATALOG_WITH_TS
except ImportError:  # pragma: no cover
    from test_object_decoder import MINIMAL_CATALOG_WITH_TS


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_object_cache()
    yield
    clear_object_cache()


@pytest.fixture()
def raw_object_json(tmp_path: Path) -> Path:
    """Production-подобный Catalog.json с верхнеуровневым ключом header."""
    object_dir = tmp_path / "Catalog" / "Справочник1"
    object_dir.mkdir(parents=True)
    path = object_dir / "Catalog.json"
    path.write_text(
        json.dumps(MINIMAL_CATALOG_WITH_TS, ensure_ascii=False), encoding="utf-8"
    )
    return path


@pytest.fixture()
def decoded(raw_object_json: Path) -> dict:
    result = decode_object_attributes(raw_object_json)
    assert result.ok, "raw-header фикстура должна декодироваться"
    return result.data


@pytest.fixture()
def top_property(decoded: dict) -> dict:
    props = decoded.get("Properties") or []
    assert props, "фикстура должна содержать верхнеуровневые реквизиты"
    return props[0]


@pytest.fixture()
def tabular_pair(decoded: dict) -> tuple[dict, dict]:
    for section in decoded.get("TabularSections") or []:
        props = section.get("Properties") or []
        if props:
            return section, props[0]
    pytest.skip("в фикстуре нет табличной части с реквизитами")


def test_fixture_is_real_raw_header(raw_object_json: Path) -> None:
    """Тест не должен проходить на нормализованном layout (issue #148)."""
    raw = json.loads(raw_object_json.read_text(encoding="utf-8"))

    assert "header" in raw
    for legacy_key in ("Properties", "Attributes", "props", "attributes"):
        assert legacy_key not in raw


def test_top_level_attribute_resolved(raw_object_json: Path, top_property: dict) -> None:
    binding = resolve_data_path(f"Объект.{top_property['Name']}", raw_object_json)

    assert isinstance(binding, ResolvedBinding)
    assert binding.resolved is True
    assert binding.object_type == "Catalog"
    assert binding.attribute_name == top_property["Name"]
    assert binding.value_type == top_property.get("Type")
    assert binding.synonym == top_property.get("Synonym")


def test_top_level_attribute_is_case_insensitive(
    raw_object_json: Path, top_property: dict
) -> None:
    name = str(top_property["Name"]).upper()
    binding = resolve_data_path(f"Объект.{name}", raw_object_json)

    assert binding.resolved is True
    assert binding.value_type == top_property.get("Type")


def test_unknown_top_level_attribute(raw_object_json: Path) -> None:
    """Несуществующий реквизит: данные не выдумываются."""
    binding = resolve_data_path("Объект.НетТакогоРеквизита", raw_object_json)

    assert binding.resolved is False
    assert binding.value_type is None
    assert binding.synonym is None
    assert binding.attribute_name == "НетТакогоРеквизита"


def test_tabular_attribute_resolved(
    raw_object_json: Path, tabular_pair: tuple[dict, dict]
) -> None:
    section, prop = tabular_pair
    binding = resolve_data_path(
        f"Объект.{section['Name']}.{prop['Name']}", raw_object_json
    )

    assert binding.resolved is True
    assert binding.attribute_name == prop["Name"]
    assert binding.value_type == prop.get("Type")
    assert binding.synonym == prop.get("Synonym")


def test_unknown_tabular_section(raw_object_json: Path) -> None:
    binding = resolve_data_path("Объект.НетТакойТЧ.Реквизит", raw_object_json)

    assert binding.resolved is False
    assert binding.value_type is None


def test_unknown_attribute_in_known_tabular_section(
    raw_object_json: Path, tabular_pair: tuple[dict, dict]
) -> None:
    section, _ = tabular_pair
    binding = resolve_data_path(
        f"Объект.{section['Name']}.НетТакогоРеквизита", raw_object_json
    )

    assert binding.resolved is False
    assert binding.synonym is None


def test_tabular_attribute_is_not_found_at_top_level(
    raw_object_json: Path, decoded: dict, tabular_pair: tuple[dict, dict]
) -> None:
    """Реквизит ТЧ не должен резолвиться как реквизит верхнего уровня."""
    _, prop = tabular_pair
    top_names = {
        str(p.get("Name", "")).casefold() for p in decoded.get("Properties") or []
    }
    if str(prop["Name"]).casefold() in top_names:
        pytest.skip("имя реквизита ТЧ совпадает с верхнеуровневым")

    assert resolve_data_path(f"Объект.{prop['Name']}", raw_object_json).resolved is False


def test_missing_file_is_fail_safe(tmp_path: Path) -> None:
    binding = resolve_data_path("Объект.Наименование", tmp_path / "Catalog.json")

    assert binding.resolved is False
    assert binding.value_type is None
    assert binding.synonym is None


def test_broken_json_is_fail_safe(tmp_path: Path) -> None:
    broken = tmp_path / "Catalog.json"
    broken.write_text("{ это не json", encoding="utf-8")

    assert resolve_data_path("Объект.Наименование", broken).resolved is False


@pytest.mark.parametrize("bad_path", ["", "Объект", ".", "Объект.ТЧ.Рек.Свойство"])
def test_unsupported_paths_are_unresolved(bad_path: str, raw_object_json: Path) -> None:
    """Формат data_path не расширялся: одиночный сегмент и глубокие пути."""
    assert resolve_data_path(bad_path, raw_object_json).resolved is False


def test_cache_is_invalidated_when_file_changes(
    raw_object_json: Path, top_property: dict
) -> None:
    """Кэш по (path, mtime_ns, size) не отдаёт устаревший результат."""
    data_path = f"Объект.{top_property['Name']}"
    assert resolve_data_path(data_path, raw_object_json).resolved is True

    raw_object_json.write_text("{ битый json", encoding="utf-8")
    os.utime(raw_object_json, ns=(0, 0))

    assert resolve_data_path(data_path, raw_object_json).resolved is False


NORMALIZED_OBJECT = {
    "Properties": [
        {"Name": "Наименование", "Type": "String", "Synonym": "Наименование"}
    ],
    "TabularSections": [
        {
            "Name": "КонтактнаяИнформация",
            "Properties": [
                {"Name": "Телефон", "Type": "String", "Synonym": "Телефон"}
            ],
        }
    ],
}


def test_normalized_layout_still_supported(tmp_path: Path) -> None:
    """Старые нормализованные fixtures поддержаны в той мере, в какой их
    принимает decode_object_attributes; иначе — fail-safe, а не догадки."""
    object_dir = tmp_path / "Catalog" / "Справочник1"
    object_dir.mkdir(parents=True)
    path = object_dir / "Catalog.json"
    path.write_text(json.dumps(NORMALIZED_OBJECT, ensure_ascii=False), encoding="utf-8")

    decoded = decode_object_attributes(path)
    top = resolve_data_path("Объект.Наименование", path)
    tab = resolve_data_path("Объект.КонтактнаяИнформация.Телефон", path)

    if decoded.ok and (decoded.data.get("Properties") or []):
        assert top.resolved is True
        assert top.value_type == "String"
        assert tab.resolved is True
        assert tab.value_type == "String"
    else:
        assert top.resolved is False
        assert tab.resolved is False
        assert top.value_type is None
