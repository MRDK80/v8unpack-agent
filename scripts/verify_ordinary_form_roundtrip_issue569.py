"""Compare ordinary forms after a v8unpack or Designer round trip."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from v8unpack import helper
from v8unpack.container import Container

from v8unpack_agent.ordinary_form_data import capture_ordinary_form
from v8unpack_agent.ordinary_form_restore import restore_ordinary_form


def read_designer_streams(path: Path):
    with TemporaryDirectory() as work, path.open("rb") as stream:
        container = Container()
        container.read(stream)
        if set(container.files) != {"form", "module"}:
            raise ValueError("expected the form and module streams")
        container.extract(work)
        return helper.brace_file_read(work, "form"), (Path(work) / "module").read_bytes()


def normalized_module(data: bytes) -> str:
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def compare_designer(source: Path, rebuilt: Path, position: list[int], value: int | None) -> bool:
    expected_layout, expected_module = read_designer_streams(source)
    layout, module = read_designer_streams(rebuilt)
    if position:
        expected_layout = copy.deepcopy(expected_layout)
        parent = expected_layout
        for index in position[:-1]:
            parent = parent[index]
        parent[position[-1]] = str(value)
    return layout == expected_layout and normalized_module(module) == normalized_module(expected_module)


def compare_normalized(
    source: Path, rebuilt: Path, form_dir: str, element: str | None, right_edge: int | None,
) -> bool:
    before = capture_ordinary_form(source, form_dir)
    after = capture_ordinary_form(rebuilt, form_dir)
    with TemporaryDirectory() as work:
        restored = restore_ordinary_form(before, Path(work) / "form", element=element, right_edge=right_edge)
        expected = next(file for file in restored if file.name.endswith(".elem.json"))
        return (
            before["identity"] == after["identity"]
            and before["module"] == after["module"]
            and json.loads(expected.read_text(encoding="utf-8")) == after["elements"]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    normalized = commands.add_parser("normalized")
    normalized.add_argument("source", type=Path, help="original normalized extraction")
    normalized.add_argument("rebuilt", type=Path, help="normalized extraction of rebuilt container")
    normalized.add_argument("form_dir", help="relative POSIX path to the directory of the selected form")
    normalized.add_argument("--element")
    normalized.add_argument("--right-edge", type=int)
    designer = commands.add_parser("designer")
    designer.add_argument("source", type=Path, help="original Form.bin from Designer")
    designer.add_argument("rebuilt", type=Path, help="Form.bin from Designer after assembly")
    designer.add_argument("--coordinate-path", type=int, nargs="+", default=[])
    designer.add_argument("--right-edge", type=int)
    args = parser.parse_args()
    if args.command == "normalized":
        equal = compare_normalized(args.source, args.rebuilt, args.form_dir, args.element, args.right_edge)
    else:
        if bool(args.coordinate_path) != (args.right_edge is not None) or any(i < 0 for i in args.coordinate_path):
            parser.error("specify --coordinate-path and --right-edge together, with nonnegative indices")
        equal = compare_designer(args.source, args.rebuilt, args.coordinate_path, args.right_edge)
    print("Equivalent" if equal else "Different")
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
