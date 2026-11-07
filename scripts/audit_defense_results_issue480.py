"""Offline portable audit of sealed development results; no weights/network."""
from __future__ import annotations

import argparse
import importlib.util
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
    with tempfile.TemporaryDirectory(prefix='defense_audit_') as temporary:
        output = Path(temporary)
        for name in ('runtime.json', 'records.jsonl.gz'):
            shutil.copyfile(data / name, output / name)
        (output / 'reports').mkdir()
        for name, value in study.bench.read(data / 'reports.json').items():
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
