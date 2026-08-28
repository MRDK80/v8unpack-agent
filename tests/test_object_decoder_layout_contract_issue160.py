"""Контракт входного layout ``object_decoder`` — #160.

Решение этапа анализа: normalized input rejected explicitly.

``decode_object_attributes()`` принимает только raw-header layout — JSON
с верхнеуровневым ключом ``header``. Нормализованная структура
``{"Properties": [...], "TabularSections": [...]}`` является *выходом*
декодера (``DecodeResult.data``), а не альтернативным входным форматом:
она даёт ``ok=False`` с ``DecodeError.HEADER_MISSING`` и не принимается
passthrough.

Тесты здесь — guard: если кто-то позже начнёт принимать плоские
``Properties`` без ``header``, эти проверки обязаны упасть.

Refs #148, #151, #163, #172, #180.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from v8unpack_agent.catalog_resolver import resolve_data_path
from v8unpack_agent.object_decoder import DecodeError, decode_object_attributes

try:  # зависит от rootdir/conftest
    from tests.test_object_decoder import MINIMAL_CATALOG_WITH_TS
except ImportError:  # pragma: no cover
    from test_object_decoder import MINIMAL_CATALOG_WITH_TS

# Плоский payload, намеренно неподдерживаемый как вход декодера.
NORMALIZED_EMPTY: dict = {"Properties": [], "TabularSections": []}

NORMALIZED_WITH_PROPERTY: dict = {
    "Properties": [
        {"Name": "SyntheticLeak", "Type": "String", "Synonym": "SyntheticLeak"},
    ],
    "TabularSections": [],
}

NORMALIZED_WITH_TABULAR_SECTION: dict = {
    "Properties": [],
    "TabularSections": [
        {
            "Name": "SyntheticSection",
            "Properties": [
                {
                    "Name": "SyntheticColumn",
                    "Type": "String",
                    "Synonym": "SyntheticColumn",
                },
            ],
        },
    ],
}


def _write_json(directory: Path, name: str, payload: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _property_names(data: dict | None) -> list[str]:
    return [prop.get("Name") for prop in (data or {}).get("Properties", [])]


def test_normalized_empty_payload_rejected(tmp_path: Path) -> None:
    """Минимальный нормализованный payload не является допустимым входом."""
    obj_json = _write_json(tmp_path, "Catalog.json", NORMALIZED_EMPTY)

    result = decode_object_attributes(obj_json)

    assert result.ok is False
    assert result.error is DecodeError.HEADER_MISSING
    assert result.data["Properties"] == []
    assert result.data["TabularSections"] == []


def test_normalized_property_is_not_passthrough(tmp_path: Path) -> None:
    """Плоский реквизит не попадает в DecodeResult.data."""
    obj_json = _write_json(tmp_path, "Catalog.json", NORMALIZED_WITH_PROPERTY)

    result = decode_object_attributes(obj_json)

    assert result.ok is False
    assert result.error is DecodeError.HEADER_MISSING
    assert "SyntheticLeak" not in _property_names(result.data)
    assert result.data["Properties"] == []


def test_normalized_tabular_section_is_not_passthrough(tmp_path: Path) -> None:
    """Плоская табличная часть не принимается; data остаётся пустым."""
    obj_json = _write_json(tmp_path, "Catalog.json", NORMALIZED_WITH_TABULAR_SECTION)

    result = decode_object_attributes(obj_json)

    assert result.ok is False
    assert result.error is DecodeError.HEADER_MISSING
    assert result.data["TabularSections"] == []
    assert result.data["Properties"] == []


def test_raw_header_positive_control(tmp_path: Path) -> None:
    """Raw-header layout остаётся поддерживаемым входом."""
    obj_json = _write_json(tmp_path, "Catalog.json", copy.deepcopy(MINIMAL_CATALOG_WITH_TS))

    result = decode_object_attributes(obj_json)

    assert result.ok is True
    assert result.error is None
    assert result.data["Properties"]
    assert result.data["TabularSections"]


def test_raw_header_is_authoritative_over_flat_keys(tmp_path: Path) -> None:
    """При header + плоских ключах результат строится только из header.

    Зафиксированный контракт: верхнеуровневые ``Properties`` игнорируются
    и не подмешиваются в результат. Никакого passthrough нет даже тогда,
    когда raw-header успешно декодирован.
    """
    payload = copy.deepcopy(MINIMAL_CATALOG_WITH_TS)
    payload["Properties"] = copy.deepcopy(NORMALIZED_WITH_PROPERTY["Properties"])
    payload["TabularSections"] = []
    obj_json = _write_json(tmp_path, "Catalog.json", payload)

    from_header = decode_object_attributes(
        _write_json(tmp_path, "Pure.json", copy.deepcopy(MINIMAL_CATALOG_WITH_TS))
    )
    result = decode_object_attributes(obj_json)

    assert result.ok is True
    assert result.error is None
    assert "SyntheticLeak" not in _property_names(result.data)
    assert _property_names(result.data) == _property_names(from_header.data)
    assert len(result.data["TabularSections"]) == len(from_header.data["TabularSections"])


def test_normalized_payload_differs_from_bare_missing_header(tmp_path: Path) -> None:
    """Реалистичный плоский payload и пустой dict дают один и тот же отказ.

    Старый ``test_missing_header`` проверяет ``{"data": []}``; здесь важно,
    что осмысленный нормализованный layout не получает особого обращения.
    """
    bare = decode_object_attributes(_write_json(tmp_path, "Bare.json", {"data": []}))
    normalized = decode_object_attributes(
        _write_json(tmp_path, "Flat.json", NORMALIZED_WITH_PROPERTY)
    )

    assert bare.ok is False
    assert normalized.ok is False
    assert bare.error is DecodeError.HEADER_MISSING
    assert normalized.error is DecodeError.HEADER_MISSING


def test_catalog_resolver_on_normalized_json_returns_unresolved(tmp_path: Path) -> None:
    """resolve_data_path() отказывает без исключения и ничего не выдумывает."""
    obj_json = _write_json(tmp_path, "Catalog.json", NORMALIZED_WITH_PROPERTY)

    binding = resolve_data_path("Объект.SyntheticLeak", obj_json)

    assert binding.resolved is False
    assert binding.data_path == "Объект.SyntheticLeak"
    assert binding.value_type is None
    assert binding.synonym is None
