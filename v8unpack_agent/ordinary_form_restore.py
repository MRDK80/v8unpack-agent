"""Restore upstream input files from a captured ordinary form."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from v8unpack_agent.ordinary_form_data import (
    OrdinaryFormDataError,
    validate_ordinary_form_data,
)

_LAYOUT_ELEMENT_TYPES = {
    "Button": "6ff79819-710e-4145-97cd-1618da79e3e2",
    "CommandPanel": "e69bf21d-97b2-4f37-86db-675aea9ec2cb",
}


def _change_right_edge(elements: dict[str, Any], element: str, right_edge: int) -> None:
    nodes = [
        node for node in elements["tree"]
        if "/".join(part for part in (node.get("page"), node["name"]) if isinstance(part, str) and part)
        == element
    ]
    kind = nodes[0].get("type") if len(nodes) == 1 else None
    type_id = _LAYOUT_ELEMENT_TYPES.get(kind) if isinstance(kind, str) else None
    if type_id is None:
        raise OrdinaryFormDataError("right-edge edit requires a unique root Button or CommandPanel")

    record = elements["data"].get(element)
    raw = record.get("raw") if isinstance(record, dict) else None
    if not isinstance(raw, list) or len(raw) != 6 or raw[0] != type_id:
        raise OrdinaryFormDataError("unrecognized raw layout for right-edge edit")
    layout = raw[3]
    if (
        not isinstance(layout, list) or len(layout) not in (24, 25) or layout[0] != "8"
        or any(
            not isinstance(value, str) or len(value) > 6
            or not value.removeprefix("-").isascii() or not value.removeprefix("-").isdigit()
            for value in layout[1:5]
        )
    ):
        raise OrdinaryFormDataError("unrecognized coordinate block for right-edge edit")
    left = int(layout[1])
    previous_right = int(layout[3])
    if not -32768 <= left < previous_right <= 32767:
        raise OrdinaryFormDataError("invalid existing horizontal coordinates")
    if type(right_edge) is not int or not left < right_edge <= 32767:
        raise OrdinaryFormDataError("right edge must be an integer greater than the left edge and at most 32767")
    layout[3] = str(right_edge)


def restore_ordinary_form(
    data: dict[str, Any],
    destination: Path,
    *,
    element: str | None = None,
    right_edge: int | None = None,
) -> tuple[Path, ...]:
    """Write four form files to a new directory for overlay on the owner tree."""
    validate_ordinary_form_data(data)
    if (element is None) != (right_edge is None):
        raise OrdinaryFormDataError("element and right edge must be provided together")
    elements = data["elements"]
    if element is not None and right_edge is not None:
        elements = copy.deepcopy(elements)
        _change_right_edge(elements, element, right_edge)

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("restore destination already exists")
    stem = data["stem"]
    files = {
        f"{stem}.json": data["metadata"],
        f"{stem}.id.json": data["identity"],
        f"{stem}.elem.json": elements,
    }
    module_name = f"{stem}.obj.bsl"
    with TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / "form"
        staged.mkdir()
        for name, payload in files.items():
            content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            (staged / name).write_bytes(content.encode("utf-8"))
        (staged / module_name).write_bytes(data["module"].encode("utf-8"))
        staged.rename(destination)
    return tuple(destination / name for name in (*files, module_name))
