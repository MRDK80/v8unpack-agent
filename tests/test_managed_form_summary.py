import json

from v8unpack_agent.managed_form_summary import (
    ManagedFormSummary,
    build_managed_form_summary,
    to_normalized_json,
)


def _payload_with_pages():
    return {
        "id": "8f4b0461-7c70-4c9f-9b0a-000000000001",
        "ref": "00000000-0000-0000-0000-000000000002",
        "data": {
            "-pages-": {
                "main": {
                    "elements": [
                        {
                            "name": "CustomerField",
                            "kind": "field",
                            "dataPath": "Object.Customer",
                            "left": 10,
                            "top": 20,
                            "width": 300,
                            "height": 24,
                            "color": "#ffffff",
                            "font": "Arial",
                            "guid": "00000000-0000-0000-0000-000000000003",
                        }
                    ]
                }
            },
            "attributes": [
                {
                    "name": "Customer",
                    "type": "CatalogRef.Customers",
                    "uuid": "00000000-0000-0000-0000-000000000004",
                    "id": "attr-id",
                }
            ],
            "commands": [
                {
                    "name": "Fill",
                    "title": "Fill",
                    "action": "FillOnServer",
                    "ref": "00000000-0000-0000-0000-000000000005",
                }
            ],
            "events": [
                {
                    "name": "OnOpen",
                    "handler": "FormOnOpen",
                    "id": "event-id",
                }
            ],
        },
    }


def _payload_without_pages():
    return {
        "data": {
            "attributes": [
                {
                    "name": "Amount",
                    "type": "Number",
                    "id": "noise-id",
                }
            ],
            "commands": [
                {
                    "name": "Recalculate",
                    "action": "RecalculateOnServer",
                }
            ],
            "elements": [
                {
                    "name": "AmountField",
                    "kind": "field",
                    "dataPath": "Object.Amount",
                    "height": 24,
                    "width": 100,
                }
            ],
        }
    }


def _payload_with_slash_keys():
    return {
        "data": {
            "form/main/elements": [
                {
                    "name": "PostButton",
                    "kind": "button",
                    "command": "Post",
                    "left": 1,
                    "top": 2,
                }
            ],
            "form/main/commands": [
                {
                    "name": "Post",
                    "action": "PostDocument",
                    "uuid": "00000000-0000-0000-0000-000000000006",
                }
            ],
        }
    }


def _payload_with_copyinfo():
    return {
        "copyinfo": [
            [
                "00000000-0000-0000-0000-000000000101",
                "00000000-0000-0000-0000-000000000102",
                None,
                ["CatalogRef.Counterparties", "Counterparty"],
            ],
            [
                "00000000-0000-0000-0000-000000000103",
                "00000000-0000-0000-0000-000000000104",
                None,
                ["Number", "Amount"],
            ],
        ],
        "data": {},
    }


def test_build_summary_returns_dataclass():
    summary = build_managed_form_summary(_payload_with_pages())

    assert isinstance(summary, ManagedFormSummary)
    assert isinstance(summary.attributes, list)
    assert isinstance(summary.commands, list)
    assert isinstance(summary.elements, list)
    assert isinstance(summary.events, list)
    assert isinstance(summary.relations, list)
    assert isinstance(summary.warnings, list)


def test_keeps_semantic_attributes_commands_elements_events():
    summary = build_managed_form_summary(_payload_with_pages())

    assert {"name": "Customer", "type": "CatalogRef.Customers"} in summary.attributes
    assert any(command["name"] == "Fill" for command in summary.commands)
    assert any(element["name"] == "CustomerField" for element in summary.elements)
    assert {"name": "OnOpen", "handler": "FormOnOpen"} in summary.events


def test_derives_relations_when_data_or_command_binding_is_present():
    summary = build_managed_form_summary(_payload_with_pages())

    assert {
        "element": "CustomerField",
        "target": "Object.Customer",
        "kind": "data",
    } in summary.relations


def test_warns_when_relations_are_not_derivable():
    payload = {
        "data": {
            "elements": [
                {
                    "name": "DecorativeGroup",
                    "kind": "group",
                }
            ]
        }
    }

    summary = build_managed_form_summary(payload)

    assert summary.warnings
    assert any("relation" in warning.lower() for warning in summary.warnings)


def test_to_normalized_json_is_deterministic():
    payload_a = _payload_with_pages()
    payload_b = _payload_with_pages()
    payload_b["data"]["commands"] = list(reversed(payload_b["data"]["commands"]))

    json_a = to_normalized_json(build_managed_form_summary(payload_a))
    json_b = to_normalized_json(build_managed_form_summary(payload_b))

    assert json_a == json_b
    assert json_a == json.dumps(json.loads(json_a), ensure_ascii=False, sort_keys=True)


def test_layout_and_id_noise_do_not_affect_normalized_json():
    payload_a = _payload_with_pages()
    payload_b = _payload_with_pages()

    payload_b["id"] = "changed-id"
    payload_b["ref"] = "changed-ref"
    payload_b["data"]["-pages-"]["main"]["elements"][0]["left"] = 999
    payload_b["data"]["-pages-"]["main"]["elements"][0]["top"] = 888
    payload_b["data"]["-pages-"]["main"]["elements"][0]["width"] = 777
    payload_b["data"]["-pages-"]["main"]["elements"][0]["height"] = 666
    payload_b["data"]["-pages-"]["main"]["elements"][0]["color"] = "#000000"
    payload_b["data"]["-pages-"]["main"]["elements"][0]["font"] = "Changed"

    json_a = to_normalized_json(build_managed_form_summary(payload_a))
    json_b = to_normalized_json(build_managed_form_summary(payload_b))

    assert json_a == json_b
    assert "changed-id" not in json_b
    assert "changed-ref" not in json_b
    assert "#000000" not in json_b
    assert "Changed" not in json_b


def test_supports_managed_layout_without_pages():
    summary = build_managed_form_summary(_payload_without_pages())

    assert {"name": "Amount", "type": "Number"} in summary.attributes
    assert any(command["name"] == "Recalculate" for command in summary.commands)
    assert any(element["name"] == "AmountField" for element in summary.elements)


def test_supports_data_keys_with_slashes_without_pages():
    summary = build_managed_form_summary(_payload_with_slash_keys())

    assert any(command["name"] == "Post" for command in summary.commands)
    assert any(element["name"] == "PostButton" for element in summary.elements)
    assert {
        "element": "PostButton",
        "target": "Post",
        "kind": "command",
    } in summary.relations


def test_copyinfo_extracts_attribute_type_and_name_but_drops_uuids():
    summary = build_managed_form_summary(_payload_with_copyinfo())
    normalized = to_normalized_json(summary)

    assert {"name": "Counterparty", "type": "CatalogRef.Counterparties"} in summary.attributes
    assert {"name": "Amount", "type": "Number"} in summary.attributes
    assert "00000000-0000-0000-0000-000000000101" not in normalized
    assert "00000000-0000-0000-0000-000000000102" not in normalized
    assert "00000000-0000-0000-0000-000000000103" not in normalized
    assert "00000000-0000-0000-0000-000000000104" not in normalized
