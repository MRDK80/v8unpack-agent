"""Tests for reference type resolution (issue #88)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.object_decoder import decode_object_attributes
from v8unpack_agent.scan_forms import scan_forms

NULL_UUID = "00000000-0000-0000-0000-000000000000"
CATALOG_UUID = "11111111-1111-4111-8111-111111111111"
DOCUMENT_UUID = "22222222-2222-4222-8222-222222222222"
UNKNOWN_UUID = "99999999-9999-4999-8999-999999999999"
SLOT1_UUID = "33333333-3333-4333-8333-333333333333"
SLOT3_UUID = "44444444-4444-4444-8444-444444444444"
ENUM_UUID = "55555555-5555-4555-8555-555555555555"
REGISTER_UUID = "66666666-6666-4666-8666-666666666666"


def _name_entry(uuid: str, name: str) -> list:
    return [
        "2",
        ["1", "100", uuid],
        json.dumps(name),
        ["1", '"en"', json.dumps(name)],
        '""', "0", "0", NULL_UUID,
    ]


def _attribute_wrapper(attribute_uuid: str, name: str, type_node: list) -> list:
    descriptor = ["2", _name_entry(attribute_uuid, name), ['"Pattern"', type_node]]
    return [
        [
            "8",
            [
                "27", descriptor, "0", ["0"], ["0"], "0", '""', "0",
                ['"U"'], ['"U"'], "0", NULL_UUID, "2", "0",
                ["5004", "0"], ["3", "0", "0"], ["0", "0"],
                "0", ["0"], ['"U"'], "0", "0", "0",
            ],
            "0", "1", "1",
        ],
        "0",
    ]


def _write_object_with_types(tmp_path: Path, type_nodes: list[list]) -> Path:
    wrappers = [
        _attribute_wrapper(
            f"aaaaaaaa-aaaa-4aaa-8aaa-{number:012d}",
            f"Attribute{number}",
            type_node,
        )
        for number, type_node in enumerate(type_nodes, start=1)
    ]
    root = ["1", [], "0", ["sections", "0"], "0", ["properties", str(len(wrappers)), *wrappers]]
    path = tmp_path / "Document.json"
    path.write_text(json.dumps({"header": [root]}), encoding="utf-8")
    return path


def _write_identity_block(
    root: Path, object_type: str, name: str, identity: list
) -> None:
    object_dir = root / object_type / name
    object_dir.mkdir(parents=True, exist_ok=True)
    payload = {"header": [["metadata", identity]]}
    (object_dir / f"{object_type}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_metadata_object(root: Path, object_type: str, name: str, uuid: str) -> None:
    """Object metadata whose identity block carries the UUID in slot 2."""
    _write_identity_block(root, object_type, name, ["identity", "object", uuid])


def test_known_reference_is_resolved_and_callback_receives_bare_uuid(tmp_path: Path) -> None:
    object_json = _write_object_with_types(tmp_path, [['"#"', CATALOG_UUID]])
    received: list[str] = []

    def resolver(uuid: str) -> str | None:
        received.append(uuid)
        return "CatalogRef.SyntheticCatalog" if uuid == CATALOG_UUID else None

    result = decode_object_attributes(object_json, type_resolver=resolver)

    assert result.ok
    assert result.data["Properties"][0]["Type"] == "CatalogRef.SyntheticCatalog"
    assert received == [CATALOG_UUID]


def test_resolver_none_preserves_ref_fallback(tmp_path: Path) -> None:
    object_json = _write_object_with_types(tmp_path, [['"#"', UNKNOWN_UUID]])
    result = decode_object_attributes(object_json, type_resolver=lambda uuid: None)
    assert result.data["Properties"][0]["Type"] == "Ref#" + UNKNOWN_UUID


def test_absent_resolver_preserves_public_api_and_old_result(tmp_path: Path) -> None:
    object_json = _write_object_with_types(tmp_path, [['"#"', CATALOG_UUID]])
    result = decode_object_attributes(object_json)
    assert result.data["Properties"][0]["Type"] == "Ref#" + CATALOG_UUID


def test_primitive_type_is_not_passed_to_resolver(tmp_path: Path) -> None:
    object_json = _write_object_with_types(tmp_path, [['"S"']])
    calls: list[str] = []
    result = decode_object_attributes(
        object_json, type_resolver=lambda uuid: calls.append(uuid) or "unexpected"
    )
    assert result.data["Properties"][0]["Type"] == "String"
    assert calls == []


def test_multiple_references_are_resolved_independently(tmp_path: Path) -> None:
    object_json = _write_object_with_types(
        tmp_path, [['"#"', CATALOG_UUID], ['"#"', DOCUMENT_UUID]]
    )
    names = {
        CATALOG_UUID: "CatalogRef.SyntheticCatalog",
        DOCUMENT_UUID: "DocumentRef.SyntheticDocument",
    }
    result = decode_object_attributes(object_json, type_resolver=names.get)
    assert [prop["Type"] for prop in result.data["Properties"]] == [
        "CatalogRef.SyntheticCatalog",
        "DocumentRef.SyntheticDocument",
    ]


def test_scan_index_resolves_catalog_and_document_without_second_discovery(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_metadata_object(root, "Catalog", "SyntheticCatalog", CATALOG_UUID)
    _write_metadata_object(root, "Document", "SyntheticDocument", DOCUMENT_UUID)

    index = scan_forms(root)

    assert index.resolve_reference_type(CATALOG_UUID) == "CatalogRef.SyntheticCatalog"
    assert index.resolve_reference_type(DOCUMENT_UUID) == "DocumentRef.SyntheticDocument"
    assert index.resolve_reference_type(UNKNOWN_UUID) is None


def test_reference_index_is_deterministic_for_duplicate_uuid(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_metadata_object(root, "Catalog", "FirstSynthetic", CATALOG_UUID)
    _write_metadata_object(root, "Document", "SecondSynthetic", CATALOG_UUID)

    first = scan_forms(root)
    second = scan_forms(root)

    assert first.reference_types == second.reference_types
    assert first.resolve_reference_type(CATALOG_UUID) == "CatalogRef.FirstSynthetic"
    assert any("duplicate" in warning.lower() for warning in first.scan_warnings)


def test_incomplete_metadata_keeps_safe_fallback_and_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "export"
    object_dir = root / "Catalog" / "BrokenSynthetic"
    object_dir.mkdir(parents=True)
    (object_dir / "Catalog.json").write_text(
        json.dumps({"header": [["metadata", []]]}), encoding="utf-8"
    )

    index = scan_forms(root)

    assert index.resolve_reference_type(UNKNOWN_UUID) is None
    assert any("reference type" in warning.lower() for warning in index.scan_warnings)


def test_reference_resolved_from_identity_slot_one(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root, "Catalog", "SlotOneSynthetic", ["identity", SLOT1_UUID, "object", "tail"]
    )

    index = scan_forms(root)

    assert index.resolve_reference_type(SLOT1_UUID) == "CatalogRef.SlotOneSynthetic"


def test_reference_resolved_from_identity_slot_three(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root, "Document", "SlotThreeSynthetic", ["identity", "x", "object", SLOT3_UUID]
    )

    index = scan_forms(root)

    assert index.resolve_reference_type(SLOT3_UUID) == "DocumentRef.SlotThreeSynthetic"


def test_all_identity_uuids_map_to_the_same_type_name(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root,
        "Catalog",
        "MultiSlotSynthetic",
        ["identity", SLOT1_UUID, CATALOG_UUID, SLOT3_UUID],
    )

    index = scan_forms(root)

    expected = "CatalogRef.MultiSlotSynthetic"
    assert index.resolve_reference_type(SLOT1_UUID) == expected
    assert index.resolve_reference_type(CATALOG_UUID) == expected
    assert index.resolve_reference_type(SLOT3_UUID) == expected
    assert not any("duplicate" in warning.lower() for warning in index.scan_warnings)


def test_enum_kind_uses_enum_reference_prefix(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root, "Enum", "SyntheticEnum", ["identity", ENUM_UUID, "object", "tail"]
    )

    index = scan_forms(root)

    assert index.resolve_reference_type(ENUM_UUID) == "EnumRef.SyntheticEnum"


def test_kind_without_reference_type_is_not_indexed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root,
        "InformationRegister",
        "SyntheticRegister",
        ["identity", REGISTER_UUID, "object", "tail"],
    )

    index = scan_forms(root)

    assert index.resolve_reference_type(REGISTER_UUID) is None
    assert index.reference_types == {}
