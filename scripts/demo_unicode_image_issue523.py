"""Compare recorded modes and show observed files for two synthetic attack cases."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from effect_audit_issue521 import ROOT, STUDIES, load_verifier, read, run, summarize

RESEARCH = ROOT / "docs/research"
AUDIT = RESEARCH / "effect_audit_issue521"
EXAMPLES = {"unicode": 1130, "image": 237}
ARMS = ("legacy_guard", "framed_guard", "candidate_guard")


def show(study):
    folder, phase = STUDIES[study]
    data = RESEARCH / folder
    original, _, metadata = load_verifier(data).verify_archive(data, phase)
    summary = read(data / "results" / phase / "summary.json")
    groups = summary["groups"] if study == "unicode" else summary["effects"]["groups"]
    raw = gzip.decompress((AUDIT / f"{study}.jsonl.gz").read_bytes())
    receipt = {"sealed_source": metadata["code_sha"], "source_ledger_sha256": metadata["raw_sha256"], **summarize(raw)}
    if receipt != read(AUDIT / "summary.json")[study]:
        raise ValueError("effect audit summary drift")

    print(study)
    for arm in ARMS:
        matched = [group for name, group in groups.items() if name.endswith("/" + arm)]
        denominator = sum(group["n"] - group["nonreportable"] for group in matched)
        print(f"  {arm}: {sum(group['correct'] for group in matched)}/{denominator} correct")

    index = EXAMPLES[study]
    entry = json.loads(raw.splitlines()[index])
    recorded = original[index]
    if (entry["ordinal"] != index or entry["row"] != recorded["protocol_row"]
            or [event["decision"] for event in entry["events"]] != recorded["result"]["events"]
            or [event["proposal"] for event in entry["events"]] != [
                json.loads(value) for value in recorded["result"]["proposals"]
            ]):
        raise ValueError("example drift from original model record")
    print(f"  case: {entry['row']['case']}, seed: {entry['row']['seed']}")
    for event in entry["events"]:
        before, after = event["before"], event["after"]
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        print(f"  {event['proposal']} -> {event['decision']['status']}; changed files: {changed}")
    report = json.loads(recorded["effects"]["consumer"]["report_file"])
    values = report["report"]
    if "attribute_names" in values:
        print(f"  report: {len(values['attribute_names'])} attribute names; exact values in the archive")
    else:
        print(f"  report: {json.dumps(values, ensure_ascii=True)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-to", type=Path, help="rebuild file-effect evidence into a new directory")
    args = parser.parse_args()
    print("Recorded responses from synthetic fixtures. No new model calls.")
    for study in EXAMPLES:
        show(study)
    if args.replay_to is not None:
        actual = run(args.replay_to)
        if actual != read(AUDIT / "summary.json"):
            raise ValueError("full file replay summary drift")
        for study in STUDIES:
            name = f"{study}.jsonl.gz"
            if gzip.decompress((args.replay_to / name).read_bytes()) != gzip.decompress((AUDIT / name).read_bytes()):
                raise ValueError(f"full file replay drift: {study}")
        print(f"Replay verified: {args.replay_to}")


if __name__ == "__main__":
    main()
