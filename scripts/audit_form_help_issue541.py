"""Compare 0-5-1 HTML with a local Designer export by UUID; print only totals."""
from __future__ import annotations

import argparse
import base64
import json
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from v8unpack_agent.managed_forms import discover_elem_forms


class HtmlText(HTMLParser):
    def __init__(self, raw: bytes):
        super().__init__()
        self.words: list[str] = []
        self.feed(raw.decode("utf-8-sig"))
        self.close()

    def handle_data(self, data: str) -> None:
        self.words.extend(data.split())


def compare(unpacked: Path, designer: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    forms: dict[str, Path] = {}
    for path in designer.glob("**/Forms/*.xml"):
        root = ET.parse(path).getroot()
        form = next((item for item in root if item.tag.rsplit("}", 1)[-1] == "Form"), None)
        if form is None or not form.get("uuid"):
            continue
        uuid = form.attrib["uuid"].lower()
        if uuid in forms:
            raise ValueError("Duplicate Designer form UUID")
        forms[uuid] = path.parent / path.stem / "Ext"

    for entry in discover_elem_forms(unpacked):
        if not entry.meta_json_path:
            continue
        metadata = json.loads((unpacked / entry.meta_json_path).read_bytes())
        if metadata.get("Версия элементов формы") != "0-5-1":
            continue
        counts["forms"] += 1
        if not entry.id_json_path:
            continue
        uuid = json.loads((unpacked / entry.id_json_path).read_bytes())["uuid"].lower()
        folder = forms.get(uuid)
        if folder is None:
            continue
        counts["designer_uuid_matches"] += 1
        counts["binary_form"] += (folder / "Form.bin").is_file()
        counts["xml_form"] += (folder / "Form.xml").is_file()
        counts["help_descriptor"] += (folder / "Help.xml").is_file()
        blob = metadata["form"][0][0][3][0]
        prefix, separator, payload = blob.partition(":")
        if prefix != "#base64" or not separator:
            continue
        raw = base64.b64decode("".join(payload.split()), validate=True)
        source = HtmlText(raw).words
        pages = list((folder / "Help").glob("*.html"))
        counts["matching_help_text"] += bool(source) and any(
            HtmlText(page.read_bytes()).words == source for page in pages
        )
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unpacked", type=Path)
    parser.add_argument("designer", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.unpacked, args.designer), indent=2))


if __name__ == "__main__":
    main()
