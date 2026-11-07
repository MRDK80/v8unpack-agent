"""Portable offline replay of pinned clean calibration, including shallow clones."""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path
from unittest.mock import patch

from scripts import agent_client_calibration_issue484 as calibration

DATA = calibration.DATA / 'results'
SEAL_SHA256 = 'd57064617f3ba540588ac608cf48e7389c46d23cea0c032216fa58c1db64a34e'


def audit(data: Path = DATA) -> dict:
    require = calibration.require
    require(calibration.digest((calibration.DATA / 'seal.json').read_bytes()) == SEAL_SHA256, 'seal_changed')
    seal = calibration.read(calibration.DATA / 'seal.json')
    require(set(seal['files']) == set(calibration.SEALED), 'sealed_file_set')
    for name, expected in seal['files'].items():
        require(calibration.digest((calibration.ROOT / name).read_bytes()) == expected, 'sealed_source_changed')
    require(seal['plan'] == calibration.plan(), 'sealed_plan_changed')
    manifest = calibration.read(data / 'manifest.json')
    require(manifest['schema'] == 'client_calibration_results/1', 'results_schema')
    require(manifest['seal_sha256'] == SEAL_SHA256, 'result_seal')
    require(set(manifest['files']) == {'records.jsonl.gz', 'runtime.json', 'summary.json'}, 'result_files')
    for name, expected in manifest['files'].items():
        require(calibration.digest((data / name).read_bytes()) == expected, 'result_hash_mismatch')
    # Source hashes/known seal are sufficient to replay an archive without .git.
    # Do not require the old commit object in a depth-1 Actions checkout.
    with patch.object(calibration, 'verify', return_value=seal):
        actual = calibration.audit(data)
    expected = calibration.read(data / 'summary.json')
    comparison = copy.deepcopy(actual)
    require(set(actual['groups']) == set(expected['groups']), 'summary_groups')
    for key, group in actual['groups'].items():
        measured = expected['groups'][key]['request_wall_seconds']
        require(math.isclose(group['request_wall_seconds'], measured, rel_tol=1e-12, abs_tol=1e-6),
                'summary_timing_mismatch')
        # CPython 3.12 float sum can differ from 3.10 in the last digits.
        comparison['groups'][key]['request_wall_seconds'] = measured
    require(comparison == expected, 'summary_mismatch')
    return actual


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    args = parser.parse_args()
    result = audit(args.data)
    print(f"AUDIT PASS: {result['rows']} calibration rows; full requests/events/files replayed")
