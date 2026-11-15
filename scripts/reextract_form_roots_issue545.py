"""Re-extract selected ordinary form roots with a verified UUID casing fallback."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from v8unpack.helper import brace_file_read
from v8unpack.MetaDataObject.Form.FormCore import FormCore
from v8unpack.v8unpack import extract


def alternate_uuid(directory: Path, uuid: str) -> str | None:
    lower = uuid.lower()
    if lower == uuid or any((directory / (uuid + suffix)).exists() for suffix in (".0", ".1")):
        return None
    member = directory / (lower + ".0")
    return lower if member.is_dir() and (member / "form").is_file() else None


def reextract(cf: Path, output: Path, targets: set[str]) -> None:
    output.mkdir(exist_ok=False)
    original = FormCore.decode_includes

    def decode(self, src_dir, dest_dir, dest_path, header_data):
        uuid = self.header["uuid"]
        directory = Path(src_dir)
        alias = alternate_uuid(directory, uuid) if uuid.lower() in targets and self.header.get("Тип формы") == "0" else None
        if alias is None:
            return original(self, src_dir, dest_dir, dest_path, header_data)
        raw = brace_file_read(str(directory / (alias + ".0")), "form")
        root = raw[0]
        if root[0] != "19" or root[1][0] != "16":
            raise ValueError("Unexpected ordinary form layout")
        section = root[2][2]
        if section[0] != str(len(section) - 1):
            raise ValueError("Inconsistent declaration counter")
        names = [row[3][1:-1] for row in section[1:]]
        digest = hashlib.sha256(json.dumps({"attribute_names": names}, ensure_ascii=True).encode()).hexdigest()
        try:
            self.header["uuid"] = alias
            result = original(self, src_dir, dest_dir, dest_path, header_data)
        finally:
            self.header["uuid"] = uuid
        if self.header.get("Версия элементов формы") != "0-19-16":
            raise ValueError("Recovered root version mismatch")
        print(f"FORM_ROOT_PROOF {alias} {digest}", flush=True)
        return result

    FormCore.decode_includes = decode
    try:
        with TemporaryDirectory(prefix="form-root-trace-") as temporary:
            extract(str(cf), str(output), temp_dir=temporary, processes=4)
    finally:
        FormCore.decode_includes = original


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("target_ids", type=Path, help="private JSON list of UUIDs")
    args = parser.parse_args()
    targets = {str(UUID(value)) for value in json.loads(args.target_ids.read_text(encoding="utf-8"))}
    reextract(args.cf.resolve(strict=True), args.output.resolve(), targets)


if __name__ == "__main__":
    main()
