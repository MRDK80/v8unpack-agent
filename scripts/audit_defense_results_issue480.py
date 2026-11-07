"""Offline portable audit of sealed development results; no weights/network."""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sealed480', ROOT / 'scripts' / 'agent_defense_issue480.py')
assert SPEC is not None and SPEC.loader is not None
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)
DATA = study.DATA / 'results'


def audit(data: Path = DATA) -> dict:
    manifest = study.bench.read(data / 'manifest.json')
    study.bench.require(manifest['schema'] == 'defense_results/1', 'result_schema')
    for name, expected in manifest['files'].items():
        study.bench.require(re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None, 'result_path')
        study.bench.require(study.bench.digest((data / name).read_bytes()) == expected, 'result_hash:' + name)
    protocol, cases, bundle = study.frozen()
    runtime = study.bench.read(data / 'runtime.json')
    study.bench.require(set(runtime['profiles']) == set(protocol['profiles']), 'runtime_profile_set')
    for label in protocol['profiles']:
        expected = bundle['profiles'][label]
        study.bench.require(runtime['ollama'] == expected['ollama_expected'], 'runtime_server_changed')
        study.bench.require(runtime['profiles'][label]['name'] == expected['model'], 'runtime_model_changed')
    rows = [json.loads(line) for line in gzip.decompress((data / 'records.jsonl.gz').read_bytes()).splitlines()]
    study.bench.require([row['item'] for row in rows] == study.plan(protocol, cases), 'incomplete_plan')
    reports = study.bench.read(data / 'reports.json')
    for name in reports:
        study.bench.require(re.fullmatch(r'[A-Za-z0-9_-]+\.json', name) is not None, 'report_path')
    expected_names = {'-'.join(str(row['item'][k]) for k in ('profile', 'seed', 'case', 'variant')) + '.json'
                      for row in rows if row['artifact'] is not None}
    study.bench.require(set(reports) == expected_names, 'unexpected_report_set')
    with tempfile.TemporaryDirectory(prefix='defense_audit_') as temporary:
        output = Path(temporary)
        for name in ('runtime.json', 'records.jsonl.gz'):
            shutil.copyfile(data / name, output / name)
        (output / 'reports').mkdir()
        for name, value in reports.items():
            study.bench.require(re.fullmatch(r'[A-Za-z0-9_-]+\.json', name) is not None, 'report_path')
            study.bench.write(output / 'reports' / name, value)
        actual = study.audit(output)
    study.bench.require(actual == study.bench.read(data / 'summary.json'), 'summary_mismatch')
    return actual


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    args = parser.parse_args()
    result = audit(args.data)
    print(f"AUDIT {result['audit']}: {result['records']} sealed development trajectories")
