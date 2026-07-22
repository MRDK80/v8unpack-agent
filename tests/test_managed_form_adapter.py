"""
tests/test_managed_form_adapter.py

Unit tests for managed_form_adapter.adapt_elem_json().
All fixtures are obfuscated: no real GUIDs, no domain names,
no connection strings, no host names.
"""

from __future__ import annotations

import pytest

from v8unpack_agent.managed_form_adapter import adapt_elem_json
from v8unpack_agent.managed_form_summary import (
    ManagedFormSummary,
    build_managed_form_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_adapter(raw: dict) -> ManagedFormSummary:
    """Run full pipeline: adapt_elem_json → build_managed_form_summary."""
    payload = adapt_elem_json(raw)
    summary = build_managed_form_summary(payload)
    # Inject adapter-built relations (not derivable from element dataPath here)
    summary.relations.extend(payload.get("_adapter_relations", []))
    summary.warnings.extend(payload.get("_adapter_warnings", []))
    return summary


# ---------------------------------------------------------------------------
# Fixture: completely empty form (legitimate empty)
# ---------------------------------------------------------------------------

_EMPTY_FORM: dict = {
    "params": {},
    "props": [],
    "commands": [],
    "tree": [],
    "data": {},
}


def test_empty_form_gives_empty_summary() -> None:
    summary = _apply_adapter(_EMPTY_FORM)
    assert summary.elements == []
    assert summary.attributes == []
    assert summary.commands == []
    assert summary.events == []
    assert summary.relations == []


# ---------------------------------------------------------------------------
# Fixture: form with tree elements
# ---------------------------------------------------------------------------

_TREE_FORM: dict = {
    "params": {},
    "props": [],
    "commands": [],
    "tree": [
        {
            "name": "HeaderGroup",
            "type": "Group",
            "child": [
                {"name": "DateField", "type": "Field", "child": []},
                {"name": "NumberField", "type": "Field", "child": []},
            ],
        },
        {"name": "CommandBar", "type": "CommandPanel", "child": []},
    ],
    "data": {},
}


def test_tree_elements_extracted() -> None:
    summary = _apply_adapter(_TREE_FORM)
    names = [e["name"] for e in summary.elements]
    assert "HeaderGroup" in names
    assert "DateField" in names
    assert "NumberField" in names
    assert "CommandBar" in names


def test_tree_element_kind_preserved() -> None:
    summary = _apply_adapter(_TREE_FORM)
    by_name = {e["name"]: e for e in summary.elements}
    assert by_name["DateField"]["kind"] == "Field"
    assert by_name["CommandBar"]["kind"] == "CommandPanel"


# ---------------------------------------------------------------------------
# Fixture: form with props (attributes)
# ---------------------------------------------------------------------------

_PROPS_FORM: dict = {
    "params": {},
    "props": [
        {
            "name": "TopAttr",
            "id": 1,
            "raw": "",
            "child": [
                {"name": "ChildAttr", "id": 2, "raw": ""},
            ],
        },
        {"name": "AnotherAttr", "id": 3, "raw": "", "child": []},
    ],
    "commands": [],
    "tree": [],
    "data": {},
}


def test_props_attributes_extracted() -> None:
    summary = _apply_adapter(_PROPS_FORM)
    names = [a["name"] for a in summary.attributes]
    assert "TopAttr" in names
    assert "ChildAttr" in names
    assert "AnotherAttr" in names


# ---------------------------------------------------------------------------
# Fixture: form with data section (relations + events)
# ---------------------------------------------------------------------------

_DATA_FORM: dict = {
    "params": {},
    "props": [],
    "commands": [],
    "tree": [],
    "data": {
        "MainPage/DocumentTable": {
            "ПутьКДанным": "Document.LineItems",
            "raw": "",
        },
        "MainPage/OrganisationField": {
            "ПутьКДанным": "Document.Organisation",
            "raw": "ПриИзменении",
        },
        "MainPage/NoRelationField": {
            "raw": "SomeNoise 12345",
        },
    },
}


def test_relations_extracted_from_data() -> None:
    summary = _apply_adapter(_DATA_FORM)
    targets = {r["target"] for r in summary.relations}
    assert "Document.LineItems" in targets
    assert "Document.Organisation" in targets


def test_relation_kind_is_data() -> None:
    summary = _apply_adapter(_DATA_FORM)
    for rel in summary.relations:
        assert rel["kind"] == "data"


def test_events_extracted_from_raw() -> None:
    summary = _apply_adapter(_DATA_FORM)
    event_names = [e["name"] for e in summary.events]
    assert "ПриИзменении" in event_names


# ---------------------------------------------------------------------------
# Fixture: commands
# ---------------------------------------------------------------------------

_CMD_FORM: dict = {
    "params": {},
    "props": [],
    "commands": [
        {"name": "SaveAndClose", "action": "SaveAndClose"},
        {"name": "Post", "action": "Post"},
    ],
    "tree": [],
    "data": {},
}


def test_commands_extracted() -> None:
    summary = _apply_adapter(_CMD_FORM)
    names = [c["name"] for c in summary.commands]
    assert "SaveAndClose" in names
    assert "Post" in names


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

_NOISE_FORM: dict = {
    "params": {},
    "props": [
        {
            "name": "11111111-2222-3333-4444-555555555555",  # GUID → noise
            "id": 99,
            "raw": "",
            "child": [],
        },
        {
            "name": "RealAttr",
            "id": 100,
            "raw": "",
            "child": [],
        },
    ],
    "commands": [],
    "tree": [
        {"name": "12345", "type": "Field", "child": []},  # numeric → noise
        {"name": "RealElement", "type": "Field", "child": []},
    ],
    "data": {},
}


def test_guid_names_filtered_from_attributes() -> None:
    summary = _apply_adapter(_NOISE_FORM)
    names = [a["name"] for a in summary.attributes]
    assert "11111111-2222-3333-4444-555555555555" not in names
    assert "RealAttr" in names


def test_numeric_names_filtered_from_elements() -> None:
    summary = _apply_adapter(_NOISE_FORM)
    names = [e["name"] for e in summary.elements]
    assert "12345" not in names
    assert "RealElement" in names


# ---------------------------------------------------------------------------
# Type error guard
# ---------------------------------------------------------------------------


def test_adapt_elem_json_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        adapt_elem_json([])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Empty-when-all-sections-empty invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"tree": [], "props": [], "commands": [], "data": {}},
        {"tree": [], "props": [], "data": {}},
        {},
    ],
)
def test_empty_sections_give_empty_summary(raw: dict) -> None:
    summary = _apply_adapter(raw)
    assert not summary.elements
    assert not summary.attributes
    assert not summary.commands
    assert not summary.events
    assert not summary.relations
