"""Published experiment ledger integrity, including retained failed procedure (#495)."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'docs/research'


@pytest.mark.parametrize('version,count', [('v1', 353), ('v2', 2880)])
def test_published_ledger_integrity(version, count):
    folder = ROOT / ('marker_experiment_issue495/v1-partial' if version == 'v1'
                     else 'marker_experiment_v2_issue495/results')
    metadata = json.loads((folder / ('status.json' if version == 'v1' else 'metadata.json')).read_text(encoding='utf-8'))
    raw = gzip.decompress((folder / 'records.jsonl.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == metadata['raw_sha256']
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == count
    assert len({tuple(sorted(record['row'].items())) for record in records}) == count
    assert all(record['source_unchanged'] for record in records)
    if version == 'v1':
        assert metadata['status'] == 'stopped_protocol_deviation'
    else:
        assert sum(record['result']['model_requested'] for record in records) == 1600
        assert sum(record['result']['outcome'] == 'unsupported' for record in records) == 1280
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
                   for name, expected in manifest.items())
