"""Replay historical experiments in their original pinned source tree, offline."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'docs/research/frozen_history_issue496/source.zip'
SNAPSHOT_SHA256 = '1511d66ce25396916ef4f42ebdb09b0eea24430c5d56a8239de80ee123e60e0f'


def replay_history(dataset: str) -> dict:
    """Original seals/source checks remain active; this gives NO admission to new code."""
    if dataset not in {'484', '485'}:
        raise ValueError('unknown historical dataset')
    raw = SNAPSHOT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SNAPSHOT_SHA256:
        raise ValueError('frozen source archive changed')
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(SNAPSHOT) as archive:
            archive.extractall(target)  # Exact trusted archive hash checked above.
        for name in ('agent_client_issue484', 'agent_acceptance_issue485'):
            source = ROOT / 'docs/research' / name
            destination = target / 'docs/research' / name
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / 'seal.json', destination / 'seal.json')
            for path in (source / 'results').rglob('*'):
                if not path.is_file():
                    continue
                copied = destination / 'results' / path.relative_to(source / 'results')
                copied.parent.mkdir(parents=True, exist_ok=True)
                if copied.exists() and copied.read_bytes() != path.read_bytes():
                    raise ValueError('pinned data conflict')
                shutil.copy2(path, copied)
        program = '''import json,sys,unicodedata
unicodedata.unidata_version=sys.argv[1]
if sys.argv[2]=='484':
 from scripts.audit_client_results_issue484 import audit
else:
 from scripts.audit_acceptance_results_issue485 import audit
print(json.dumps(audit(),ensure_ascii=False))
'''
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(target)
        result = subprocess.run([sys.executable, '-c', program, unicodedata.unidata_version, dataset],
                                cwd=target, env=environment, capture_output=True, text=True,
                                encoding='utf-8', timeout=300, check=False)
        if result.returncode != 0:
            raise RuntimeError('historical source replay failed')
        return json.loads(result.stdout)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', choices=['484', '485'])
    args = parser.parse_args()
    summary = replay_history(args.dataset)
    print('PASS historical pinned replay:', summary['rows'])
