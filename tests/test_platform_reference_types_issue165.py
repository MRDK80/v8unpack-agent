"""Статическая таблица платформенных типов (issue #165).

Проверяется контракт констант, порядок резолюции
``reference_types`` → таблица → ``None`` и неизменность schema v2.

Поведение декодера при ``None`` от резолвера (сохранение ``Ref#uuid``)
покрыто в ``tests/test_reference_type_resolution.py`` (issue #88) и здесь
не дублируется.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from v8unpack_agent.platform_reference_types import (
    PLATFORM_REFERENCE_TYPES,
    PLATFORM_TYPES_WITHOUT_XDTO_NAME,
)
from v8unpack_agent.scan_forms import FormScanIndex, scan_forms

UNKNOWN_UUID = "99999999-9999-4999-8999-999999999999"
A_ONLY_ABSENT = "e72fe022-682f-445d-bba5-3bbd9ff02242"
SIDE_OBSERVATION = "90d4887a-f541-490b-b51b-c8a00bb332de"
NAME_PATTERN = re.compile(r"^[a-z0-9]+:[A-Za-z][A-Za-z0-9]*$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _write_catalog(root: Path, name: str, uuid: str) -> None:
    """Минимальная синтетическая метадата справочника с UUID в identity-слоте."""
    obj_dir = root / "Catalog" / name
    obj_dir.mkdir(parents=True, exist_ok=True)
    payload = {"header": [["metadata", ["object", uuid]]]}
    (obj_dir / "Catalog.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# 1. Мощность таблицы и множества исключений: 13 + 1 = 14.
def test_table_cardinality_is_thirteen_plus_one() -> None:
    assert len(PLATFORM_REFERENCE_TYPES) == 13
    assert len(PLATFORM_TYPES_WITHOUT_XDTO_NAME) == 1
    assert len(PLATFORM_REFERENCE_TYPES) + len(PLATFORM_TYPES_WITHOUT_XDTO_NAME) == 14


# 2. Пересечение двух множеств пусто.
def test_table_and_exclusions_are_disjoint() -> None:
    assert not set(PLATFORM_REFERENCE_TYPES) & PLATFORM_TYPES_WITHOUT_XDTO_NAME


# 3. Ключи — UUID нижним регистром, значения — префиксная форма XDTO.
def test_keys_and_values_have_canonical_shape() -> None:
    for uuid, type_name in PLATFORM_REFERENCE_TYPES.items():
        assert UUID_PATTERN.match(uuid), uuid
        assert NAME_PATTERN.match(type_name), type_name
        assert "." not in type_name
    for uuid in PLATFORM_TYPES_WITHOUT_XDTO_NAME:
        assert UUID_PATTERN.match(uuid), uuid


# 4. Позиции вне доказанного объёма в константы не попадают.
def test_out_of_scope_uuids_are_absent() -> None:
    known = set(PLATFORM_REFERENCE_TYPES) | PLATFORM_TYPES_WITHOUT_XDTO_NAME
    assert A_ONLY_ABSENT not in known
    assert SIDE_OBSERVATION not in known


# 5. Пустой индекс: резолюция идёт через статическую таблицу.
def test_platform_uuid_resolves_from_table() -> None:
    index = FormScanIndex()
    for uuid, type_name in PLATFORM_REFERENCE_TYPES.items():
        assert index.resolve_reference_type(uuid) == type_name


# 6. Приоритет индекса выгрузки при искусственной коллизии.
def test_reference_types_wins_over_static_table() -> None:
    uuid = next(iter(PLATFORM_REFERENCE_TYPES))
    index = FormScanIndex(reference_types={uuid: "CatalogRef.SyntheticCatalog"})
    assert index.resolve_reference_type(uuid) == "CatalogRef.SyntheticCatalog"
    assert index.reference_types == {uuid: "CatalogRef.SyntheticCatalog"}


# 7. Explicit fallback: UUID без имени и неизвестный UUID дают None.
def test_exclusions_and_unknown_uuid_return_none() -> None:
    index = FormScanIndex()
    for uuid in PLATFORM_TYPES_WITHOUT_XDTO_NAME:
        assert index.resolve_reference_type(uuid) is None
    assert index.resolve_reference_type(UNKNOWN_UUID) is None


# 8. Тест-охранник: пересечение ключей таблицы с индексом пусто на синтетике.
def test_index_keys_do_not_intersect_static_table(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _write_catalog(root, "SyntheticCatalog", "11111111-1111-4111-8111-111111111111")
    _write_catalog(root, "OtherSynthetic", "22222222-2222-4222-8222-222222222222")

    index = scan_forms(root)

    assert not set(index.reference_types) & set(PLATFORM_REFERENCE_TYPES)
    assert not set(index.reference_types) & PLATFORM_TYPES_WITHOUT_XDTO_NAME


# 9. Таблица не сериализуется: schema v2 не меняется.
def test_static_table_is_not_serialized(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _write_catalog(root, "SyntheticCatalog", "11111111-1111-4111-8111-111111111111")

    index = scan_forms(root)
    payload = index.to_dict()

    assert payload["schema_version"] == 2
    assert "platform_reference_types" not in payload
    assert set(payload["reference_types"]) == set(index.reference_types)
    assert not set(payload["reference_types"]) & set(PLATFORM_REFERENCE_TYPES)

    saved = tmp_path / "index.json"
    index.save(saved)
    raw = saved.read_text(encoding="utf-8")
    for type_name in PLATFORM_REFERENCE_TYPES.values():
        assert type_name not in raw
