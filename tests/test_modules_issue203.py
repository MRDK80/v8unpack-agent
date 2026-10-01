"""Контракт ModuleEntry / ModuleIndex (issue #203)."""

from __future__ import annotations

import json
import random
from dataclasses import FrozenInstanceError, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, get_args

import pytest

from v8unpack_agent.common_modules import CommonModuleEntry
from v8unpack_agent.modules import (
    MODULE_INDEX_SCHEMA,
    MODULE_KINDS,
    MODULE_READ_STATUSES,
    OWNER_KINDS,
    ModuleEntry,
    ModuleIndex,
    ModuleKind,
    ModuleReadStatus,
    OwnerKind,
    classify_bsl_bytes,
    module_entry_from_common_module,
    validate_relative_module_path,
)

SHA = "0" * 64
BS = chr(92)


def make(**overrides: Any) -> ModuleEntry:
    fields: dict[str, Any] = {
        "module_kind": "object",
        "owner_kind": "metadata_object",
        "metadata_type": "Catalog",
        "owner_name": "Alpha",
        "relative_path": "Catalog/Alpha/Catalog.obj.bsl",
        "read_status": "ok",
    }
    fields.update(overrides)
    return ModuleEntry(**fields)


def config(kind: str, suffix: str) -> ModuleEntry:
    return make(
        module_kind=kind,
        owner_kind="configuration",
        metadata_type=None,
        owner_name=None,
        relative_path=f"Configuration.{suffix}.bsl",
    )


# --- закрытые наборы по #202 -------------------------------------------------


def test_closed_sets_match_issue202_inventory() -> None:
    assert sorted(get_args(ModuleKind)) == [
        "command",
        "common_module",
        "external_connection",
        "form",
        "managed_application",
        "manager",
        "object",
        "ordinary_application",
        "record_set",
        "service",
        "session",
        "value_manager",
    ]
    assert sorted(get_args(OwnerKind)) == [
        "common_command",
        "common_form",
        "common_module",
        "configuration",
        "extension",
        "external_data_processor",
        "external_report",
        "metadata_object",
        "metadata_object_command",
        "metadata_object_form",
    ]
    assert set(get_args(ModuleReadStatus)) == {
        "ok",
        "empty",
        "whitespace_only",
        "missing",
        "read_error",
    }
    assert MODULE_KINDS == frozenset(get_args(ModuleKind))
    assert OWNER_KINDS == frozenset(get_args(OwnerKind))
    assert MODULE_READ_STATUSES == frozenset(get_args(ModuleReadStatus))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("module_kind", "form_descent_part"),
        ("owner_kind", "unknown_owner"),
        ("read_status", "nonempty"),
        ("read_status", "missing_file"),
    ],
)
def test_values_outside_closed_sets_are_rejected(
    field: str, value: str
) -> None:
    with pytest.raises(ValueError):
        make(**{field: value})


# --- неизменяемость ----------------------------------------------------------


def test_entry_and_index_are_frozen() -> None:
    entry = make()
    index = ModuleIndex.from_entries([entry])
    with pytest.raises(FrozenInstanceError):
        entry.__setattr__("read_status", "empty")
    with pytest.raises(FrozenInstanceError):
        index.__setattr__("entries", ())
    assert isinstance(index.entries, tuple)
    assert hash(entry) == hash(make())


def test_index_copies_input_sequence() -> None:
    source = [make()]
    index = ModuleIndex.from_entries(source)
    source.append(make(owner_name="Beta", relative_path="Catalog/B/x.bsl"))
    assert index.total == 1


# --- metadata_type и владелец ------------------------------------------------


def test_obj_suffix_is_disambiguated_by_metadata_type() -> None:
    record_set = make(
        module_kind="record_set",
        metadata_type="InformationRegister",
        relative_path="InformationRegister/Alpha/"
        "InformationRegister.obj.bsl",
    )
    enum_manager = make(
        module_kind="manager",
        metadata_type="Enum",
        relative_path="Enum/Alpha/Enum.obj.bsl",
    )
    value_manager = make(
        module_kind="value_manager",
        metadata_type="Constant",
        relative_path="Constant/Alpha/Constant.obj.bsl",
    )
    ids = {e.module_id for e in (record_set, enum_manager, value_manager)}
    assert ids == {
        "metadata_object:InformationRegister:Alpha:record_set",
        "metadata_object:Enum:Alpha:manager",
        "metadata_object:Constant:Alpha:value_manager",
    }


def test_configuration_owner_has_no_type_and_name() -> None:
    entry = config("session", "seance")
    assert entry.module_id == "configuration:::session"
    with pytest.raises(ValueError):
        make(owner_kind="configuration", owner_name=None)
    with pytest.raises(ValueError):
        make(owner_kind="configuration", metadata_type=None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"metadata_type": None},
        {"owner_name": None},
        {"metadata_type": "Справочник"},
        {"metadata_type": "Catalog/X"},
        {"owner_name": ""},
        {"owner_name": "Alpha/Beta"},
        {"owner_name": "Alpha..Beta"},
        {"owner_name": "1Alpha"},
    ],
)
def test_invalid_owner_fields_are_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        make(**overrides)


def test_form_owner_uses_qualified_name() -> None:
    entry = make(
        module_kind="form",
        owner_kind="metadata_object_form",
        owner_name="Товары.ФормаЭлемента",
        relative_path="Catalog/Товары/CatalogForm/ФормаЭлемента/"
        "CatalogForm.obj.bsl",
    )
    assert entry.module_id == (
        "metadata_object_form:Catalog:Товары.ФормаЭлемента:form"
    )


# --- граница пути: POSIX и Windows semantics независимо от ОС ----------------

ABSOLUTE_PATHS = [
    "/export/Catalog/Alpha/Catalog.obj.bsl",
    "//server/share/Catalog.obj.bsl",
    "C:/export/Catalog.obj.bsl",
    "c:Catalog.obj.bsl",
    f"C:{BS}export{BS}Catalog.obj.bsl",
    f"{BS}{BS}server{BS}share{BS}Catalog.obj.bsl",
    f"{BS}Catalog.obj.bsl",
]


@pytest.mark.parametrize("value", ABSOLUTE_PATHS)
def test_absolute_paths_are_rejected(value: str) -> None:
    assert PurePosixPath(value).is_absolute() or (
        PureWindowsPath(value).drive or PureWindowsPath(value).root
    )
    with pytest.raises(ValueError):
        validate_relative_module_path(value)
    with pytest.raises(ValueError):
        make(relative_path=value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        f"Catalog{BS}Alpha{BS}Catalog.obj.bsl",
        "Catalog/../Catalog.obj.bsl",
        "../Catalog.obj.bsl",
        "./Catalog.obj.bsl",
        "Catalog//Catalog.obj.bsl",
        "Catalog/Alpha/",
        "Catalog/Alpha/Catalog.obj.bsl/",
        "Catalog/Al:pha/Catalog.obj.bsl",
        "Catalog/Alpha./Catalog.obj.bsl",
        "Catalog/Alpha /Catalog.obj.bsl",
        "Catalog/CON/Catalog.obj.bsl",
        "Catalog/nul.txt/Catalog.obj.bsl",
        "Catalog/Alpha/Catalog.obj.json",
        "Catalog/Alpha/Catalog.obj.BSL",
        "Catalog/Al\x00pha/Catalog.obj.bsl",
        "Catalog/Al\npha/Catalog.obj.bsl",
    ],
)
def test_other_invalid_path_forms_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_module_path(value)


def test_non_string_path_is_rejected() -> None:
    with pytest.raises(TypeError):
        make(relative_path=Path("Catalog", "Alpha", "Catalog.obj.bsl"))


def test_valid_relative_path_is_kept_verbatim() -> None:
    value = "Catalog/Товары/Catalog.mgr.bsl"
    assert validate_relative_module_path(value) == value
    assert make(relative_path=value).path == PurePosixPath(value)
    assert config("ordinary_application", "802").relative_path == (
        "Configuration.802.bsl"
    )


# --- статусы чтения ---------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "empty"),
        (b"\xef\xbb\xbf", "empty"),
        (b" \r\n\t", "whitespace_only"),
        (b"\xef\xbb\xbf \n", "whitespace_only"),
        ("Процедура А()\nКонецПроцедуры\n".encode(), "ok"),
        (b"\xff\xfe\xfa", "read_error"),
    ],
)
def test_classify_bsl_bytes(data: bytes, expected: str) -> None:
    assert classify_bsl_bytes(data) == expected


def test_empty_missing_whitespace_and_read_error_are_distinct() -> None:
    statuses = ["ok", "empty", "whitespace_only", "missing", "read_error"]
    entries = [
        make(
            owner_name=f"M{i}",
            relative_path=f"Catalog/M{i}/Catalog.obj.bsl",
            read_status=status,
        )
        for i, status in enumerate(statuses)
    ]
    index = ModuleIndex.from_entries(entries)
    for status in statuses:
        assert index.filter(read_status=status).total == 1


def test_missing_entry_keeps_expected_path_without_size() -> None:
    entry = make(read_status="missing")
    assert entry.relative_path == "Catalog/Alpha/Catalog.obj.bsl"
    with pytest.raises(ValueError):
        make(read_status="missing", size_bytes=0)
    with pytest.raises(ValueError):
        make(read_status="missing", sha256=SHA)


@pytest.mark.parametrize(
    "overrides",
    [
        {"size_bytes": -1},
        {"size_bytes": True},
        {"size_bytes": 1.5},
        {"sha256": "A" * 64},
        {"sha256": "0" * 63},
    ],
)
def test_invalid_size_and_hash_are_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        make(**overrides)


# --- идентификатор и дубликаты ----------------------------------------------


def test_duplicate_module_id_is_rejected() -> None:
    first = make()
    second = make(relative_path="Catalog/Alpha2/Catalog.obj.bsl")
    assert first.module_id == second.module_id
    with pytest.raises(ValueError, match="duplicate module_id"):
        ModuleIndex.from_entries([first, second])


def test_duplicate_module_id_is_case_insensitive() -> None:
    first = make()
    second = make(
        owner_name="ALPHA",
        relative_path="Catalog/ALPHA2/Catalog.obj.bsl",
    )
    with pytest.raises(ValueError, match="duplicate module_id"):
        ModuleIndex.from_entries([first, second])


def test_case_insensitive_duplicate_path_is_rejected() -> None:
    first = make()
    second = make(
        owner_name="Beta",
        relative_path="catalog/alpha/catalog.obj.bsl",
    )
    with pytest.raises(ValueError, match="duplicate relative_path"):
        ModuleIndex.from_entries([first, second])


def test_module_id_does_not_depend_on_layout() -> None:
    first = make(relative_path="Catalog/Alpha/Catalog.obj.bsl")
    second = replace(first, relative_path="Catalogs/Alpha/Ext/Object.bsl")
    assert first.module_id == second.module_id


def test_get_by_module_id() -> None:
    entry = make()
    index = ModuleIndex.from_entries([entry, config("session", "seance")])
    assert index.get("metadata_object:catalog:alpha:object") is entry
    assert index.get("missing:id") is None


# --- сортировка и фильтрация ------------------------------------------------


def sample_entries() -> list[ModuleEntry]:
    return [
        config("managed_application", "app"),
        config("session", "seance"),
        make(
            owner_name="Zulu",
            relative_path="Catalog/Zulu/Catalog.obj.bsl",
        ),
        make(
            owner_name="alpha",
            relative_path="Catalog/alpha/Catalog.obj.bsl",
        ),
        make(
            module_kind="manager",
            owner_name="Beta",
            relative_path="Catalog/Beta/Catalog.mgr.bsl",
        ),
        make(
            module_kind="common_module",
            owner_kind="common_module",
            metadata_type="CommonModule",
            owner_name="Common",
            relative_path="CommonModule/Common/CommonModule.obj.bsl",
            read_status="empty",
        ),
        make(
            module_kind="record_set",
            metadata_type="InformationRegister",
            owner_name="Reg",
            relative_path="InformationRegister/Reg/"
            "InformationRegister.obj.bsl",
            read_status="whitespace_only",
        ),
    ]


def test_sorting_is_independent_of_input_order() -> None:
    entries = sample_entries()
    expected = ModuleIndex.from_entries(entries)
    rng = random.Random(203)
    for _ in range(20):
        shuffled = entries[:]
        rng.shuffle(shuffled)
        assert ModuleIndex.from_entries(shuffled) == expected
    assert [e.relative_path for e in expected] == [
        "Catalog/alpha/Catalog.obj.bsl",
        "Catalog/Beta/Catalog.mgr.bsl",
        "Catalog/Zulu/Catalog.obj.bsl",
        "CommonModule/Common/CommonModule.obj.bsl",
        "Configuration.app.bsl",
        "Configuration.seance.bsl",
        "InformationRegister/Reg/InformationRegister.obj.bsl",
    ]


def test_filter_by_kind_owner_and_metadata_type() -> None:
    index = ModuleIndex.from_entries(sample_entries())
    assert index.filter(owner_kind="configuration").total == 2
    assert index.filter(module_kind="object").total == 2
    assert index.filter(metadata_type="Catalog").total == 3
    assert index.filter(
        module_kind="manager", owner_kind="metadata_object"
    ).total == 1
    assert index.filter(owner_kind="extension").total == 0
    assert index.filter() == index


@pytest.mark.parametrize(
    "overrides",
    [
        {"module_kind": "unknown"},
        {"owner_kind": "unknown"},
        {"read_status": "unknown"},
    ],
)
def test_filter_rejects_unknown_values(overrides: dict[str, Any]) -> None:
    index = ModuleIndex.from_entries(sample_entries())
    with pytest.raises(ValueError):
        index.filter(**overrides)


def test_index_rejects_foreign_items() -> None:
    foreign: Any = ("Catalog/Alpha/Catalog.obj.bsl",)
    with pytest.raises(TypeError):
        ModuleIndex(entries=foreign)


# --- сериализация -----------------------------------------------------------


def test_json_is_deterministic_and_round_trips() -> None:
    entries = sample_entries()
    first = ModuleIndex.from_entries(entries).to_json()
    second = ModuleIndex.from_entries(list(reversed(entries))).to_json()
    assert first == second
    assert first.endswith("\n")
    assert "\r" not in first
    assert BS not in first
    data = json.loads(first)
    assert data["schema"] == MODULE_INDEX_SCHEMA
    assert data["total"] == len(entries)
    assert ModuleIndex.from_dict(data) == ModuleIndex.from_entries(entries)


def test_serialization_has_no_bsl_text() -> None:
    entry = make(size_bytes=10, sha256=SHA)
    data = entry.to_dict()
    assert set(data) == {
        "module_id",
        "module_kind",
        "owner_kind",
        "metadata_type",
        "owner_name",
        "relative_path",
        "read_status",
        "size_bytes",
        "sha256",
    }
    assert "bsl_text" not in ModuleIndex.from_entries([entry]).to_json()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema="module_index/0"),
        lambda d: d.update(total=99),
        lambda d: d["entries"][0].update(module_id="x"),
        lambda d: d["entries"][0].update(bsl_text="secret"),
        lambda d: d["entries"][0].update(relative_path="/abs/x.bsl"),
    ],
)
def test_from_dict_rejects_inconsistent_data(mutate: Any) -> None:
    data = ModuleIndex.from_entries([make()]).to_dict()
    mutate(data)
    with pytest.raises(ValueError):
        ModuleIndex.from_dict(data)


# --- обратная совместимость CommonModuleEntry -------------------------------


def test_common_module_entry_adapter_keeps_legacy_type() -> None:
    legacy = CommonModuleEntry(
        name="Alpha",
        bsl_path=Path("CommonModule", "Alpha", "CommonModule.obj.bsl"),
    )
    entry = module_entry_from_common_module(legacy, "ok", size_bytes=5)
    assert entry.relative_path == "CommonModule/Alpha/CommonModule.obj.bsl"
    assert entry.module_id == (
        "common_module:CommonModule:Alpha:common_module"
    )
    assert entry.size_bytes == 5
    assert legacy == CommonModuleEntry(
        name="Alpha",
        bsl_path=Path("CommonModule", "Alpha", "CommonModule.obj.bsl"),
    )


def test_from_dict_rejects_non_list_entries() -> None:
    data = ModuleIndex.from_entries([make()]).to_dict()
    data["entries"] = "not-a-list"
    with pytest.raises(TypeError):
        ModuleIndex.from_dict(data)


def test_common_module_adapter_rejects_unsafe_legacy_path() -> None:
    legacy = CommonModuleEntry(name="Unsafe", bsl_path=Path("..", "x.bsl"))
    with pytest.raises(ValueError):
        module_entry_from_common_module(legacy, "missing")
