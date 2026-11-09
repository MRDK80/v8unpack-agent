"""Full R12 clock/effect archive integrity and recorded-runtime offline replay."""
import gzip
import hashlib
import json
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/research/bounded_recovery_issue498'


def test_source_zip_exactly_retains_frozen_inputs():
    archive=DATA/'source.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()=='58e825fed954224aac349bb13bb22622cfd9ca7f0f7a86d28cdeaab209b35034'
    seal=json.loads((DATA/'seal.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(archive) as source:
        seal_name=(DATA.relative_to(ROOT)/'seal.json').as_posix()
        assert set(source.namelist())==set(seal['files'])|{seal_name}
        assert json.loads(source.read(seal_name))==seal
        for name,digest in seal['files'].items():assert hashlib.sha256(source.read(name)).hexdigest()==digest


def test_full_ledger_and_fixed_scoring():
    folder=DATA/'results';manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    assert {p.name for p in folder.iterdir()}==set(manifest)|{'manifest.json'}
    for name,digest in manifest.items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest
    raw=gzip.decompress((folder/'records.jsonl.gz').read_bytes())
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(raw).hexdigest()==metadata['raw_sha256']
    rows=[json.loads(line) for line in raw.split(b'\n') if line]
    seal=json.loads((DATA/'seal.json').read_text(encoding='utf-8'))
    assert len(rows)==882 and [r['row'] for r in rows]==seal['plan']
    assert sum(r['result']['model_requested'] for r in rows)==520
    assert all(r['source_unchanged'] and len(r['result']['trace'])<=3 for r in rows)
    from scripts.score_bounded_recovery_issue498 import summarize
    summary=summarize(rows,json.loads((DATA/'cases.json').read_text(encoding='utf-8')))
    assert summary==json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    groups=[g for key,g in summary['groups'].items() if key.endswith('/json_recovery')]
    assert sum(g['correct'] for g in groups)==37
    assert sum(g['wrong'] for g in groups)==0
    assert sum(g['live_correct_after_block'] for g in groups)==17
    assert sum(g['namespace_recovery'] for g in groups)==17
    assert all(g['utility_admission']=='NO-GO' for g in groups)


def test_all_clock_requests_and_actual_effects_replay_without_models():
    if sys.version_info[:2]!=(3,12) or unicodedata.unidata_version!='15.0.0':
        pytest.skip('recorded Python3.12/Unicode15 required for exact request replay')
    subprocess.run([sys.executable,str(DATA/'replay_archive.py')],cwd=ROOT,check=True,timeout=300)
