"""Explicit edits for ordinary form layout version 0-27."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from v8unpack_agent.ordinary_form_data import (
    OrdinaryFormDataError,
    validate_ordinary_form_data,
)

_BUTTON = "6ff79819-710e-4145-97cd-1618da79e3e2"
_PANEL = "e69bf21d-97b2-4f37-86db-675aea9ec2cb"
_BUILTIN_ACTION = "fbe38877-b914-4fd5-8540-07dde06ba2e1"
_USER_ACTION = "e1692cc2-605b-4535-84dd-28440238746c"
_MENU = "b78f2e80-ec68-11d4-9dcf-0050bae2bc79"


def _root_element(data: dict[str, Any], key: str, kind: str, type_id: str):
    matches = [
        node for node in data["elements"]["tree"]
        if node.get("type") == kind
        and "/".join(part for part in (node.get("page"), node["name"]) if part) == key
    ]
    if len(matches) != 1:
        raise OrdinaryFormDataError(f"expected a unique root {kind}")
    record = data["elements"]["data"][key]
    raw = record["raw"]
    if len(raw) != 6 or raw[0] != type_id:
        raise OrdinaryFormDataError(f"unsupported {kind} layout")
    return matches[0], record, raw


def _identifier(value: str) -> bool:
    return isinstance(value, str) and value.isidentifier() and value[0].isalpha()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def add_ordinary_form_button(
    data: dict[str, Any], *, prototype: str, name: str, caption: str,
    handler: str, handler_body: str, vertical_offset: int,
) -> dict[str, Any]:
    """Duplicate a root button and bind it to a new BSL procedure."""
    validate_ordinary_form_data(data)
    if not _identifier(name) or not _identifier(handler):
        raise OrdinaryFormDataError("expected BSL identifiers for button and handler")
    if not caption or not caption.isprintable() or not handler_body.strip():
        raise OrdinaryFormDataError("expected a caption and BSL procedure body")
    if type(vertical_offset) is not int or vertical_offset == 0 or abs(vertical_offset) > 32767:
        raise OrdinaryFormDataError("expected a nonzero vertical offset in layout units")

    result = deepcopy(data)
    node, record, _ = _root_element(result, prototype, "Button", _BUTTON)
    elements = result["elements"]
    key = "/".join(part for part in (node.get("page"), name) if part)
    if any(name.casefold() == existing.rsplit("/", 1)[-1].casefold() for existing in elements["data"]):
        raise OrdinaryFormDataError("button name already exists in the form")
    if handler.casefold() in result["module"].casefold():
        raise OrdinaryFormDataError("handler name already appears in the module")

    new_record = deepcopy(record)
    raw = new_record["raw"]
    try:
        action = raw[2][2]
        layout = raw[3]
        properties = raw[2][1]
        if (
            action[:1] != ["1"] or action[1][0] != "0"
            or action[1][1] not in {_USER_ACTION, _BUILTIN_ACTION}
            or layout[0] != "8" or properties[1] != "14" or raw[4][0] != "14"
            or len(properties[2]) != 3 or properties[2][:2] != ["1", "1"]
            or len(properties[2][2]) != 2
        ):
            raise ValueError
        new_id = max(
            int(entry["id"]) for entry in elements["data"].values()
            if isinstance(entry, dict) and "id" in entry
        ) + 1
        top, bottom = (int(layout[i]) + vertical_offset for i in (2, 4))
        if not -32768 <= top < bottom <= 32767:
            raise ValueError
        for index in (6, 7):
            anchor = layout[index]
            if anchor[0] != "0" or anchor[1][0] != "2" or anchor[2] != ["2", "-1", "6", "0"]:
                raise ValueError
            anchor[1][3] = str(int(anchor[1][3]) + vertical_offset)
        layout[2], layout[4] = str(top), str(bottom)
        binding = [
            "3", _quote(handler),
            ["1", '""', ["1", "0"], ["1", "0"], ["1", "0"],
             ["3", "0", ["0"], '""', "-1", "-1", "1", "0"], ["0", "0", "0"]],
        ]
        raw[2][2] = ["1", ["0", _USER_ACTION, binding]]
        properties[2][2][1] = _quote(caption)
        raw[1] = new_record["id"] = str(new_id)
        raw[4][1] = _quote(name)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise OrdinaryFormDataError("unsupported button prototype or position") from exc

    elements["tree"].append({**node, "name": name})
    elements["data"][key] = new_record
    module = result["module"]
    newline = "\r\n" if "\r\n" in module else "\n"
    body_lines = handler_body.splitlines()
    result["module"] = module + newline.join([
        "", "", f"Процедура {handler}(Кнопка)", *body_lines, "КонецПроцедуры", "",
    ])
    return result


def remove_ordinary_form_menu_item(
    data: dict[str, Any], *, panel: str, name: str,
) -> dict[str, Any]:
    """Remove one named item from a root command panel's flat custom menu."""
    validate_ordinary_form_data(data)
    result = deepcopy(data)
    _, _, raw = _root_element(result, panel, "CommandPanel", _PANEL)
    try:
        command_table = raw[2][1][7]
        command_count = int(command_table[4])
        if command_count < 0 or len(command_table) != 7 + command_count or command_table[-2] != "1":
            raise ValueError
        menu = command_table[-1]
        count = int(menu[4])
        if menu[:4] != ["5", _MENU, "4", "0"] or count < 1 or len(menu) != 6 + 2 * count:
            raise ValueError
        items = menu[6:-1:2]
        if any(not isinstance(item, list) or len(item) != 16 or item[0] != "8" for item in items):
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise OrdinaryFormDataError("unsupported command panel menu layout") from exc
    matches = [index for index, item in enumerate(items) if item[1] == _quote(name) and item[5] == "1"]
    if len(matches) != 1:
        raise OrdinaryFormDataError("expected one actionable menu item with the supplied name")
    offset = 5 + 2 * matches[0]
    del menu[offset:offset + 2]
    menu[4] = str(count - 1)
    return result
