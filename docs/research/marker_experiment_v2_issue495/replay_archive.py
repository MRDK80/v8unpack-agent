"""Verify every published trajectory and native file effect offline (Python 3.12)."""
import gzip
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.marker_experiment_v2_issue495 import DATA, read, replay, verify

seal = verify()
folder = DATA / 'results'
for name, expected in read(folder / 'manifest.json').items():
    if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
        raise ValueError('archive file changed: ' + name)
raw = gzip.decompress((folder / 'records.jsonl.gz').read_bytes())
if hashlib.sha256(raw).hexdigest() != read(folder / 'metadata.json')['raw_sha256']:
    raise ValueError('raw ledger changed')
records = [json.loads(line) for line in raw.splitlines()]
if [row['row'] for row in records] != seal['plan']:
    raise ValueError('plan mismatch')
cases = {case['id']: case for case in read(DATA / 'cases.json')}
for record in records:
    with tempfile.TemporaryDirectory() as temporary:
        replay(cases[record['row']['case']], record['row'], record, Path(temporary) / 'trial')
print('PASS: full offline replay', len(records), 'trajectories; no model requests')
