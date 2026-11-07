"""Pinned final acceptance archive audit; offline, no model weights/network."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts import agent_acceptance_issue485 as study

DATA = study.DATA / 'results'
SEAL_SHA256 = '9efc39545b3bfe71f3be8b2bde77718e45d43746d1bae16ee4cfc693b10c41f9'


def audit(data: Path = DATA) -> dict:
    require = study.require
    require(study.digest((study.DATA / 'seal.json').read_bytes()) == SEAL_SHA256, 'seal_changed')
    study.verify()
    manifest = study.read(data / 'manifest.json')
    require(manifest['schema'] == 'agent_acceptance_results/1', 'results_schema')
    require(manifest['seal_sha256'] == SEAL_SHA256, 'result_seal')
    require(set(manifest['files']) == {'records.jsonl.gz', 'runtime.json', 'faults.json', 'summary.json'}, 'result_file_set')
    for name, expected in manifest['files'].items():
        require(study.digest((data / name).read_bytes()) == expected, 'result_hash_mismatch')
    actual = study.audit(data)
    require(actual == study.read(data / 'summary.json'), 'summary_mismatch')
    return actual


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    args = parser.parse_args()
    summary = audit(args.data)
    print('AUDIT PASS:', summary['rows'], 'sealed acceptance rows; full requests/events/files/faults replayed')
