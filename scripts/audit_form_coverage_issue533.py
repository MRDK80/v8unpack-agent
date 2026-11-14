"""Compare installed analyzer against raw export positions, emitting only aggregates."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from v8unpack_agent.form_evidence import (
    MAX_BYTES,
    FormSource,
    analyze_form,
    make_form_report,
)
from v8unpack_agent.managed_forms import discover_elem_forms


def inspect_export(root):
    counts = Counter()
    outcomes = {}
    for index, entry in enumerate(discover_elem_forms(root)):
        paths = [root / path if path else None for path in (entry.meta_json_path, entry.elem_json_path)]
        raw = [None, None]
        for slot, path in enumerate(paths):
            if path is None or not path.is_file():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                counts["unavailable:unsafe_source_path"] += 1
                break
            with path.open("rb") as stream:
                raw[slot] = stream.read(MAX_BYTES + 1)
            if len(raw[slot]) > MAX_BYTES:
                counts["incomplete:source_budget"] += 1
                break
        else:
            evidence = analyze_form(FormSource(f"audit-{index}", *raw))
            counts[f"{evidence.status}:{evidence.reason or 'none'}"] += 1
            if evidence.status == "complete":
                metadata, elements = (json.loads(data.decode("utf-8-sig")) for data in raw)
                version = metadata["Версия элементов формы"]
                name_position = 4 if version == "0-27" else 3
                expected = {"attribute_names": [record["raw"][name_position][1:-1]
                            for record in elements["props"] or []]}
                actual = make_form_report(evidence.contract())
                if actual != expected:
                    raise ValueError(f"raw projection mismatch at anonymous ordinal {index}")
                digest = hashlib.sha256(json.dumps(expected, ensure_ascii=True).encode()).hexdigest()
                outcomes[index] = digest
    return {"forms": sum(counts.values()), "statuses": dict(counts)}, outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", nargs=3, type=Path)
    parser.add_argument("--private-receipt", required=True, type=Path,
                        help="local-only ordinal digests for comparison across analyzer revisions")
    args = parser.parse_args()
    results, receipts = {}, {}
    for label, root in zip("ABC", args.exports, strict=True):
        results[label], receipts[label] = inspect_export(root.resolve(strict=True))
    with args.private_receipt.open("x", encoding="utf-8") as stream:
        json.dump(receipts, stream)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
