"""Integrity of the whole frozen R10 ledger, including unsupported and timings."""
import gzip
import hashlib
import json
from pathlib import Path


def test_full_recorded_matrix_is_retained():
    data = Path(__file__).resolve().parents[1] / 'docs/research/minimal_context_issue496'
    folder = data / 'results'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
               for name, expected in manifest.items())
    raw = gzip.decompress((folder / 'records.jsonl.gz').read_bytes())
    metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(raw).hexdigest() == metadata['raw_sha256']
    records = [json.loads(line) for line in raw.splitlines()]
    seal = json.loads((data / 'seal.json').read_text(encoding='utf-8'))
    assert [record['row'] for record in records] == seal['plan']
    assert len(records) == 1440
    assert sum(record['result']['model_requested'] for record in records) == 800
    assert sum(record['result']['outcome'] == 'unsupported' for record in records) == 640
    assert all(record['source_unchanged'] and record['trial_wall_seconds'] >= 0 for record in records)
