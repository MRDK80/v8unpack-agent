"""Command-line entry point for writing ordinary form build inputs."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from v8unpack_agent.ordinary_form_data import OrdinaryFormDataError
from v8unpack_agent.ordinary_form_restore import restore_ordinary_form


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
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.data.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        print("Could not read an ordinary-form/1 JSON file.", file=sys.stderr)
        return 2

    try:
        restore_ordinary_form(data, args.destination, element=args.element, right_edge=args.right_edge)
    except OrdinaryFormDataError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("Could not write ordinary form files. Destination must be new.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
