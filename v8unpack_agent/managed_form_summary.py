"""
managed_form_summary.py

Extracts semantic summary from a managed form *.elem.json payload.

Public API
----------
ManagedFormSummary  – dataclass with semantic fields only (no noise)
build_managed_form_summary(payload: dict) -> ManagedFormSummary
to_normalized_json(summary: ManagedFormSummary) -> str
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Noise keys that are stripped from every element/attribute/command/event.
# Layout, identity and GUID-like fields are classified as noise.
# ---------------------------------------------------------------------------
_MANAGED_NOISE_KEYS: frozenset[str] = frozenset(
    {
        # identity
        "id",
        "ref",
        "uuid",
        "guid",
        # layout / cosmetics
        "left",
        "top",
        "width",
        "height",
        "color",
        "font",
        "style",
        "visible",
        "enabled",
        "readOnly",
    }
)

# Pattern that matches UUID v4 / GUID strings (8-4-4-4-12 hex groups).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Keys whose values are considered semantic sections inside "data".
_SEMANTIC_SECTION_NAMES: frozenset[str] = frozenset(
    {"attributes", "commands", "elements", "events"}
)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class ManagedFormSummary:
    """Semantic summary of a managed form. All noise is already removed."""

    attributes: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    elements: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_guid(value: Any) -> bool:
    """Return True if *value* is a string that looks like a GUID."""
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _strip_noise(item: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *item* without noise keys and without GUID values."""
    return {
        k: v
        for k, v in item.items()
        if k not in _MANAGED_NOISE_KEYS and not _is_guid(v)
    }


def _collect_sections(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """
    Collect semantic sections from the ``data`` block, handling three layouts:

    1. ``data["-pages-"][page_name][section_name]`` — pages wrapper
    2. ``data[section_name]`` — flat sections at the top level of ``data``
    3. ``data["section/subsection/name"]`` — slash-separated composite keys
    """
    sections: dict[str, list] = {s: [] for s in _SEMANTIC_SECTION_NAMES}

    for key, value in data.items():
        if key == "-pages-" and isinstance(value, dict):
            # Layout 1: pages wrapper
            for _page_name, page_content in value.items():
                if isinstance(page_content, dict):
                    for section_name, items in page_content.items():
                        if section_name in _SEMANTIC_SECTION_NAMES and isinstance(
                            items, list
                        ):
                            sections[section_name].extend(items)
        elif key in _SEMANTIC_SECTION_NAMES and isinstance(value, list):
            # Layout 2: flat section
            sections[key].extend(value)
        elif "/" in key and isinstance(value, list):
            # Layout 3: slash-separated key, e.g. "form/main/elements"
            parts = key.split("/")
            last_part = parts[-1]
            if last_part in _SEMANTIC_SECTION_NAMES:
                sections[last_part].extend(value)

    return sections


def _derive_relations(
    elements: list[dict[str, Any]],
    commands: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """
    Derive element→target relations from element metadata.

    - ``dataPath`` present → relation kind ``"data"``
    - ``command`` present  → relation kind ``"command"``
    - Neither              → append a warning
    """
    command_names: set[str] = {c["name"] for c in commands if "name" in c}
    relations: list[dict[str, Any]] = []

    for elem in elements:
        name = elem.get("name", "<unknown>")
        if "dataPath" in elem:
            relations.append(
                {"element": name, "target": elem["dataPath"], "kind": "data"}
            )
        elif "command" in elem and elem["command"] in command_names:
            relations.append(
                {"element": name, "target": elem["command"], "kind": "command"}
            )
        else:
            warnings.append(
                f"relation not derivable for element '{name}': "
                "no dataPath or command binding found"
            )

    return relations


def _parse_copyinfo(
    copyinfo: list[Any],
) -> list[dict[str, Any]]:
    """
    Extract attribute type and name from ``copyinfo`` rows.

    Each row layout: [uuid0, uuid1, <anything>, [type_str, name_str, ...]]
    Fields 0 and 1 are UUIDs (dropped). Field 3.0 is the metadata type,
    field 3.1 is the attribute name.
    """
    attributes: list[dict[str, Any]] = []
    for row in copyinfo:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        meta = row[3]
        if not isinstance(meta, (list, tuple)) or len(meta) < 2:
            continue
        attr_type = meta[0]
        attr_name = meta[1]
        if isinstance(attr_type, str) and isinstance(attr_name, str):
            attributes.append({"name": attr_name, "type": attr_type})
    return attributes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_managed_form_summary(payload: dict[str, Any]) -> ManagedFormSummary:
    """
    Build a :class:`ManagedFormSummary` from a raw ``*.elem.json`` payload.

    Noise (id / ref / GUID values / layout keys) is stripped from every item.
    Relations are derived from element metadata where possible; a warning is
    recorded for each element whose binding cannot be determined.
    """
    summary = ManagedFormSummary()

    # --- copyinfo takes priority for attributes (carries type + name) ---
    copyinfo = payload.get("copyinfo")
    if isinstance(copyinfo, list):
        summary.attributes.extend(_parse_copyinfo(copyinfo))

    # --- data sections ---
    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}

    sections = _collect_sections(data)

    # Merge attributes from data only if copyinfo didn't already provide them.
    if not summary.attributes:
        summary.attributes = [
            _strip_noise(item)
            for item in sections["attributes"]
            if isinstance(item, dict)
        ]
    summary.commands = [
        _strip_noise(item)
        for item in sections["commands"]
        if isinstance(item, dict)
    ]
    summary.elements = [
        _strip_noise(item)
        for item in sections["elements"]
        if isinstance(item, dict)
    ]
    summary.events = [
        _strip_noise(item)
        for item in sections["events"]
        if isinstance(item, dict)
    ]

    # Derive relations from the *raw* (pre-strip) elements so we can still
    # read dataPath / command before stripping.
    raw_elements = [
        item for item in sections["elements"] if isinstance(item, dict)
    ]
    summary.relations = _derive_relations(
        raw_elements, summary.commands, summary.warnings
    )

    return summary


def to_normalized_json(summary: ManagedFormSummary) -> str:
    """
    Serialize *summary* to a deterministic JSON string.

    - Keys are sorted at every nesting level (``sort_keys=True``).
    - Output is UTF-8 friendly (``ensure_ascii=False``).
    - Lists are sorted by their JSON representation to guarantee
      stable ordering regardless of the original item order in the payload.
    """

    def _sort_list(lst: list[Any]) -> list[Any]:
        try:
            return sorted(
                lst,
                key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False),
            )
        except TypeError:
            return lst

    doc = {
        "attributes": _sort_list(summary.attributes),
        "commands": _sort_list(summary.commands),
        "elements": _sort_list(summary.elements),
        "events": _sort_list(summary.events),
        "relations": _sort_list(summary.relations),
        "warnings": sorted(summary.warnings),
    }
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)
