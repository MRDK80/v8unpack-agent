"""
managed_form_adapter.py

Adapts a real v8unpack *.elem.json structure to the normalised payload
expected by build_managed_form_summary().

Real top-level keys: params / props / commands / tree / data
  tree  – recursive element hierarchy  {name, type, child[]}
  props – form requisites              [{name, id, raw, child[]}]
  data  – page/element nodes           {"Page/Elem": {raw, ПутьКДанным, ...}}

Public API
----------
adapt_elem_json(raw: dict) -> dict
    Returns a normalised payload ready for build_managed_form_summary().
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---- noise filters ----------------------------------------------------------

# Pattern: UUID v4 / GUID (with or without braces / quotes is handled by
# checking the string value directly).
_UUID_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)

# Pattern: a string that is *purely* numeric (layout code).
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

# Known noise tokens in raw string fields.
_NOISE_TOKENS: frozenset[str] = frozenset({"Pattern"})

# Pattern for event handler names: contains "При" followed by a capital letter.
_EVENT_RE = re.compile(r"При[A-ZА-Я]")


def _is_noise_string(value: str) -> bool:
    """Return True for GUID-like, numeric-only, or known noise strings."""
    s = value.strip('"').strip()
    return bool(_UUID_RE.match(s)) or bool(_NUMERIC_RE.match(s)) or s in _NOISE_TOKENS


# ---- tree traversal ---------------------------------------------------------


def _walk_tree(node: Any, out: list[dict[str, Any]]) -> None:
    """
    Recursively walk a tree node and collect {name, kind} dicts.

    A node may be:
      - a dict with keys 'name', 'type', optional 'child'
      - a list of such dicts
    """
    if isinstance(node, list):
        for item in node:
            _walk_tree(item, out)
        return
    if not isinstance(node, dict):
        return
    name = node.get("name")
    kind = node.get("type")
    if isinstance(name, str) and name and not _is_noise_string(name):
        entry: dict[str, Any] = {"name": name}
        if isinstance(kind, str) and kind:
            entry["kind"] = kind
        out.append(entry)
    child = node.get("child")
    if child:
        _walk_tree(child, out)


# ---- props extraction -------------------------------------------------------


def _extract_attributes(props: Any) -> list[dict[str, Any]]:
    """
    Extract attribute names from props[].

    Each prop: {name, id, raw, child[]}
    child items also carry a 'name'.
    """
    attrs: list[dict[str, Any]] = []
    if not isinstance(props, list):
        return attrs
    for prop in props:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        if isinstance(name, str) and name and not _is_noise_string(name):
            attrs.append({"name": name})
        for child in prop.get("child", []) or []:
            if not isinstance(child, dict):
                continue
            cname = child.get("name")
            if isinstance(cname, str) and cname and not _is_noise_string(cname):
                attrs.append({"name": cname})
    return attrs


# ---- data section extraction ------------------------------------------------


def _extract_relations_and_events(
    data: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Scan data dict for ПутьКДанным relations and event handler names.

    data keys have the shape "PageName/ElementName".
    Returns (relations, events, warnings).
    """
    relations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return relations, events, warnings

    for composite_key, node in data.items():
        if not isinstance(node, dict):
            continue

        # Derive element name from composite key "Page/Element"
        parts = composite_key.split("/")
        element_name = parts[-1] if parts else composite_key

        # ---- relations from ПутьКДанным ----
        data_path = node.get("ПутьКДанным")
        if isinstance(data_path, str) and data_path.strip():
            relations.append(
                {
                    "element": element_name,
                    "target": data_path.strip(),
                    "kind": "data",
                }
            )

        # ---- events from raw string values ----
        raw = node.get("raw")
        if isinstance(raw, (str, list)):
            candidates = [raw] if isinstance(raw, str) else raw
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                # raw may contain multiple tokens separated by whitespace
                for token in candidate.split():
                    token = token.strip('"').strip()
                    if _EVENT_RE.search(token) and not _is_noise_string(token):
                        event_entry = {"name": token, "element": element_name}
                        if event_entry not in events:
                            events.append(event_entry)

    return relations, events, warnings


# ---- commands extraction ----------------------------------------------------


def _extract_commands(commands: Any) -> list[dict[str, Any]]:
    """
    Normalise commands section.

    Accepts a list of dicts or a dict keyed by command name.
    """
    result: list[dict[str, Any]] = []
    if isinstance(commands, list):
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            name = cmd.get("name")
            if isinstance(name, str) and name and not _is_noise_string(name):
                entry = {"name": name}
                action = cmd.get("action") or cmd.get("Action")
                if isinstance(action, str) and action:
                    entry["action"] = action
                result.append(entry)
    elif isinstance(commands, dict):
        for name, meta in commands.items():
            if isinstance(name, str) and name and not _is_noise_string(name):
                entry = {"name": name}
                if isinstance(meta, dict):
                    action = meta.get("action") or meta.get("Action")
                    if isinstance(action, str) and action:
                        entry["action"] = action
                result.append(entry)
    return result


# ---- public API -------------------------------------------------------------


def adapt_elem_json(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a real v8unpack *.elem.json *raw* dict to a normalised payload
    accepted by ``build_managed_form_summary()``.

    Mapping:
      elements   <- recursive walk of raw["tree"]
      attributes <- raw["props"][].name + raw["props"][].child[].name
      commands   <- raw["commands"] (list or dict)
      relations  <- raw["data"]["X/Y"]["ПутьКДанным"]
      events     <- best-effort from raw["data"]["X/Y"]["raw"] (ПриСобытие pattern)

    Returns a dict with keys: elements, attributes, commands, relations,
    events, warnings (embedded in _adapter_meta for caller transparency).

    The returned dict is structured so that ``build_managed_form_summary``
    can consume it directly via its ``data`` key holding a flat-section layout:
      payload["data"]["elements"] = [...]
      payload["data"]["attributes"] = [...]
      etc.

    Additionally, pre-extracted ``relations`` and ``events`` are injected
    as top-level keys so callers can merge them after calling
    build_managed_form_summary if needed.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"adapt_elem_json expects dict, got {type(raw).__name__}")

    warnings: list[str] = []

    # --- elements from tree ---
    elements: list[dict[str, Any]] = []
    _walk_tree(raw.get("tree", []), elements)

    # --- attributes from props ---
    attributes = _extract_attributes(raw.get("props"))

    # --- commands ---
    commands = _extract_commands(raw.get("commands"))

    # --- relations + events from data ---
    relations, events, data_warnings = _extract_relations_and_events(
        raw.get("data")
    )
    warnings.extend(data_warnings)

    if warnings:
        logger.warning(
            "adapt_elem_json produced %d warning(s): %s",
            len(warnings),
            warnings,
        )

    # Build a payload in the flat-section layout understood by
    # build_managed_form_summary: data[section_name] = list
    payload: dict[str, Any] = {
        "data": {
            "elements": elements,
            "attributes": attributes,
            "commands": commands,
            "events": events,
        },
        # Expose pre-built relations so the caller can inject them directly
        # into ManagedFormSummary.relations (build_managed_form_summary
        # derives relations from element dataPath/command binding;
        # adapter-built relations come from the real data section).
        "_adapter_relations": relations,
        "_adapter_warnings": warnings,
    }
    return payload
