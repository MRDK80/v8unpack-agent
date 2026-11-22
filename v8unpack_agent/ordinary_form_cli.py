"""Command-line entry point for writing ordinary form build inputs."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from v8unpack_agent.ordinary_form_data import OrdinaryFormDataError
from v8unpack_agent.ordinary_form_edits import (
    add_ordinary_form_button,
    remove_ordinary_form_menu_item,
)
from v8unpack_agent.ordinary_form_restore import restore_ordinary_form
from v8unpack_agent.skd_edit import replace_skd_query


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v8unpack-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    restore = commands.add_parser(
        "restore-ordinary-form",
        help="write ordinary form source files for v8unpack.build",
        description="Restore <stem>.json, <stem>.id.json, <stem>.elem.json and <stem>.obj.bsl.",
        epilog="Overlay these files at form_dir in a copy of the owner tree, then run v8unpack.build.",
    )
    restore.add_argument("data", type=Path, help="ordinary-form/1 JSON from capture_ordinary_form")
    restore.add_argument("destination", type=Path, help="new output directory with an existing parent")
    restore.add_argument("--element", help="elements.data key of a root Button or CommandPanel")
    restore.add_argument("--right-edge", type=int, help="new right-edge coordinate, with --element")
    button = commands.add_parser("add-ordinary-form-button", help="restore a form with a new button and handler")
    button.add_argument("data", type=Path)
    button.add_argument("destination", type=Path)
    button.add_argument("--prototype", required=True, help="elements.data key of a root button")
    button.add_argument("--name", required=True, help="new button identifier")
    button.add_argument("--caption", required=True)
    button.add_argument("--handler", required=True, help="new BSL procedure identifier")
    button.add_argument("--handler-body", required=True, type=Path, help="UTF-8 file with procedure statements")
    button.add_argument("--vertical-offset", type=int, required=True)

    menu = commands.add_parser("remove-ordinary-form-menu-item", help="restore a form without one menu item")
    menu.add_argument("data", type=Path)
    menu.add_argument("destination", type=Path)
    menu.add_argument("--panel", required=True, help="elements.data key of a root command panel")
    menu.add_argument("--name", required=True, help="internal menu item name")

    skd = commands.add_parser("replace-skd-query", help="write a new SKD Template.bin for v8unpack.build")
    skd.add_argument("template", type=Path, help="source Template.bin from an unpacked ERF")
    skd.add_argument("destination", type=Path, help="new template file with an existing parent")
    skd.add_argument("--dataset", required=True, help="dataset name")
    skd.add_argument("--query-file", required=True, type=Path, help="UTF-8 file containing the replacement query")

    args = parser.parse_args(argv)
    if args.command == "replace-skd-query":
        return _edit_skd(args)

    try:
        data = json.loads(args.data.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        print("Could not read an ordinary-form/1 JSON file.", file=sys.stderr)
        return 2

    try:
        if args.command == "add-ordinary-form-button":
            data = add_ordinary_form_button(
                data, prototype=args.prototype, name=args.name, caption=args.caption,
                handler=args.handler, handler_body=args.handler_body.read_text(encoding="utf-8-sig"),
                vertical_offset=args.vertical_offset,
            )
        elif args.command == "remove-ordinary-form-menu-item":
            data = remove_ordinary_form_menu_item(data, panel=args.panel, name=args.name)
        restore_ordinary_form(
            data, args.destination, element=getattr(args, "element", None),
            right_edge=getattr(args, "right_edge", None),
        )
    except OrdinaryFormDataError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("Could not write ordinary form files. Destination must be new.", file=sys.stderr)
        return 5
    return 0


def _edit_skd(args: argparse.Namespace) -> int:
    try:
        query = args.query_file.read_text(encoding="utf-8-sig")
        replace_skd_query(args.template, args.destination, dataset=args.dataset, query=query)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("Could not read input or write a new SKD template file.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
