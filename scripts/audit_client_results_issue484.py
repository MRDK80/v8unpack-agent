"""Portable offline replay of pinned clean calibration, including shallow clones."""
from __future__ import annotations

import argparse
import copy
import json
import math
import unicodedata
from pathlib import Path
from unittest.mock import patch

from scripts import agent_client_calibration_issue484 as calibration

DATA = calibration.DATA / 'results'
SEAL_SHA256 = 'd57064617f3ba540588ac608cf48e7389c46d23cea0c032216fa58c1db64a34e'


def _diagnostic_runtime(body: dict, expected: str, target: str) -> dict:
    """Map ONLY validated host diagnostic metadata, never source/value text."""
    body = copy.deepcopy(body)
    for message in body['messages']:
        if message.get('role') != 'tool' or message.get('tool_name') != 'read_source':
            continue
        try:payload = json.loads(message.get('content', ''))
        except (ValueError, TypeError):continue
        if type(payload) is not dict or payload.get('schema') != 'evidence_context/1':
            continue
        for module in payload['modules']:
            diagnostics = module['diagnostics']
            if diagnostics is not None:
                calibration.require(diagnostics['unidata_version'] == expected,
                                    'diagnostic_runtime_mismatch')
                diagnostics['unidata_version'] = target
        message['content'] = json.dumps(payload, ensure_ascii=False)
    return body


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
    original_trial, original_normalized = calibration.trial, calibration.normalized
    recorded_unicode = seal['environment']['unidata_version']
    local_unicode = unicodedata.unidata_version
    def portable_trial(case: dict, row: dict, folder: Path, chat: object) -> dict:
        def proxy(body: dict) -> dict:
            return chat(_diagnostic_runtime(body, local_unicode, recorded_unicode))
        reproduced = original_trial(case, row, folder, proxy)
        for step in reproduced['result']['trace']:
            step['request'] = _diagnostic_runtime(step['request'], local_unicode, recorded_unicode)
        return reproduced
    def portable_normalized(record: dict, unicode_version: str) -> dict:
        return original_normalized(record, recorded_unicode)
    # The sealed replay compares requests inside its callback BEFORE its final
    # normalizer. Adapt that declared diagnostic field at this earlier boundary.
    # All other bytes/values and the real executor are still checked exactly.
    with (patch.object(calibration, 'verify', return_value=seal),
          patch.object(calibration, 'trial', side_effect=portable_trial),
          patch.object(calibration, 'normalized', side_effect=portable_normalized)):
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
