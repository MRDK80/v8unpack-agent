"""Trace ordinary 0-5-1 records in a decompressed CF catalog, emitting only counts."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

from v8unpack.helper import brace_file_read

from v8unpack_agent.managed_forms import discover_elem_forms


def trace(unpacked: Path, catalog: Path) -> dict[str, int]:
    members: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    for path in catalog.iterdir():
        prefix, dot, suffix = path.name.partition(".")
        members[prefix.lower()][dot + suffix].append(path)

    counts: Counter[str] = Counter()
    for entry in discover_elem_forms(unpacked):
        if not entry.id_json_path or not entry.meta_json_path:
            continue
        metadata = json.loads((unpacked / entry.meta_json_path).read_bytes())
        if metadata.get("Версия элементов формы") != "0-5-1":
            continue
        counts["forms"] += 1
        uuid = str(UUID(json.loads((unpacked / entry.id_json_path).read_bytes())["uuid"]))
        records = members.get(uuid, {})
        if set(records) != {"", ".1"} or any(
            len(paths) != 1 or not paths[0].is_file() for paths in records.values()
        ):
            counts["other_member_layout"] += 1
            continue
        counts["header_and_1_only"] += 1
        raw_path = records[".1"][0]
        help_root = brace_file_read(str(catalog), raw_path.name)
        if metadata.get("form") == [help_root]:
            counts["identical_help_root"] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unpacked", type=Path)
    parser.add_argument("catalog", type=Path, help="directory for one decompressed CF container")
    args = parser.parse_args()
    print(json.dumps(trace(args.unpacked, args.catalog), indent=2))


if __name__ == "__main__":
    main()
