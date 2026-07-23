"""Semantic summary for elem-based 1C forms.

The module does not parse raw *.elem.json directly.  It builds a compact,
deterministic view over the canonical ``parse_elem_json(form_dir)`` result.

The summary is form-kind neutral: it applies to any elem-based form,
ordinary or managed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Any

from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json


@dataclass(frozen=True)
class FormSummary:
    """Compact semantic view of any elem-based form element tree."""

    elements: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_form_summary(form_dir: Path) -> FormSummary:
    """Build a semantic summary for a form directory.

    ``form_dir`` is the directory that contains ``*.elem.json`` and optionally
    the form BSL module.  The function delegates real elem parsing to
    ``parse_elem_json`` and only maps normalized elements to summary buckets.
    """

    result = parse_elem_json(Path(form_dir))
    return build_form_summary_from_elem_index(result)


def build_form_summary_from_elem_index(
    result: ElemIndexResult,
) -> FormSummary:
    """Build a semantic summary from ``ElemIndexResult``.

    This helper keeps the mapping testable without writing temporary elem files.
    """

    if not result.elem_index_ok:
        return FormSummary(warnings=list(result.warnings))

    elements: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []

    for raw in result.elements:
        item = _normalized_item(raw)
        source = str(raw.get("source") or "")

        if source == "props":
            attributes.append(_attribute_item(raw))
        elif source == "commands":
            commands.append(_command_item(raw))
        else:
            elements.append(item)

        data_path = raw.get("data_path")
        if data_path:
            relations.append({
                "element": item["name"],
                "target": str(data_path),
                "kind": "data",
            })

        handler = raw.get("handler")
        if handler:
            events.append({
                "name": str(handler),
                "element": item["name"],
            })
            relations.append({
                "element": item["name"],
                "target": str(handler),
                "kind": "event",
            })

    return FormSummary(
        elements=_deduplicate(elements),
        attributes=_deduplicate(attributes),
        commands=_deduplicate(commands),
        events=_deduplicate(events),
        relations=_deduplicate(relations),
        warnings=list(result.warnings),
    )


def to_normalized_json(summary: FormSummary) -> str:
    """Return a deterministic UTF-8 friendly JSON representation."""

    return json.dumps(
        {
            "elements": summary.elements,
            "attributes": summary.attributes,
            "commands": summary.commands,
            "events": summary.events,
            "relations": summary.relations,
            "warnings": summary.warnings,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _normalized_item(raw: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": str(raw.get("name") or ""),
        "kind": str(raw.get("type") or "Unknown"),
    }

    for key in ("path", "parent", "parent_path", "page", "source"):
        value = raw.get(key)
        if value not in (None, ""):
            item[key] = str(value)

    return item


def _attribute_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("name") or ""),
        "type": str(raw.get("type") or "Unknown"),
    }


def _command_item(raw: dict[str, Any]) -> dict[str, Any]:
    item = {
        "name": str(raw.get("name") or ""),
        "type": str(raw.get("type") or "Unknown"),
    }
    handler = raw.get("handler")
    if handler:
        item["handler"] = str(handler)
    return item


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    return result
