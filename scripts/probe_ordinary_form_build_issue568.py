"""Compare upstream assembly with and without one original form container."""
from __future__ import annotations

import argparse
import json
import shutil
from importlib import import_module
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from v8unpack.container import Container
from v8unpack.organizer_form_elem import OrganizerFormElem

from v8unpack_agent.elem_parser import parse_elem_json

upstream = import_module("v8unpack.v8unpack")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner", type=Path, help="Synthetic source CF or EPF")
    parser.add_argument("form_dir", type=Path, help="Form directory relative to upstream extraction root")
    parser.add_argument("form_bin", type=Path, help="Original Form.bin from Designer, used for the control")
    parser.add_argument("work", type=Path, help="New directory for all outputs")
    args = parser.parse_args()
    work = args.work.resolve()
    relative = args.form_dir
    if relative.is_absolute() or ".." in relative.parts:
        parser.error("form_dir must be a relative path within the extraction")
    if args.owner.suffix.lower() not in {".cf", ".epf"}:
        parser.error("owner must be a CF or EPF")
    work.mkdir()

    extracted = work / "extracted"
    options = {"auto_include": False}
    upstream.extract(str(args.owner), str(extracted), processes=1, options=options)
    form = extracted / relative
    element_files = sorted(form.glob("*.elem.json"))
    if len(element_files) != 1:
        parser.error("expected exactly one element file in form_dir")
    element_name = element_files[0].name
    stem = element_name.removesuffix(".elem.json")
    metadata = read_json(form / f"{stem}.json")
    if metadata.get("Тип формы") != "0" or metadata.get("Версия элементов формы") != "0-27":
        parser.error("expected an ordinary form with element version 0-27")
    form_id = str(UUID(read_json(form / f"{stem}.id.json")["uuid"]))
    index = parse_elem_json(form, write_index=False)
    print(f"Agent element index: ok={index.elem_index_ok}, elements={len(index.elements)}")

    # Recreate the four inputs in a fresh copy of the surrounding owner tree.
    filenames = {stem + suffix for suffix in (".json", ".id.json", ".elem.json", ".obj.bsl")}
    restored = work / "restored"
    shutil.copytree(extracted, restored, ignore=lambda path, names: {
        name for name in names
        if Path(path) == form and name in filenames
    })
    target = restored / relative
    for name in sorted(filenames):
        source = form / name
        if source.is_file():
            shutil.copyfile(source, target / source.name)
    if any(p.name.lower() == "form.bin" for p in restored.rglob("*")):
        parser.error("unexpected Form.bin in normalized sources")

    OrganizerFormElem.pack((
        str(target), "", element_name, str(work), "", "packed.elem.json", None, None, None,
    ))
    print("OrganizerFormElem.pack: OK")

    compress = upstream.compress_and_build

    def replace_with_original(stage_dir, destination, **kwargs):
        candidates = list(Path(stage_dir).glob(f"*/{form_id}.0"))
        if len(candidates) != 1 or not candidates[0].is_dir():
            raise ValueError("expected one ordinary form directory at encode stage 1")
        streams = work / "control-streams"
        with args.form_bin.open("rb") as stream:
            container = Container()
            container.read(stream)
            contents = set(container.files)
            if contents != {"form", "module"}:
                raise ValueError("expected the form and module streams")
            container.extract(str(streams))
        for name in contents:
            shutil.copyfile(streams / name, candidates[0] / name)
        return compress(stage_dir, destination, **kwargs)

    suffix = args.owner.suffix.lower()
    rebuilt = work / f"rebuilt{suffix}"
    upstream.build(str(restored), str(rebuilt), processes=1, options=options)
    control = work / f"control{suffix}"
    with patch.object(upstream, "compress_and_build", replace_with_original):
        upstream.build(str(restored), str(control), processes=1, options=options)


if __name__ == "__main__":
    main()
