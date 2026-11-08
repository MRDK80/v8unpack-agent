"""Pinned R11 source ZIP integrity and retained incomplete v1, all platforms."""
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/research'


@pytest.mark.parametrize('version',['v1','v2'])
def test_pinned_source_zip_contains_exact_sealed_bytes(version):
    folder=DATA/('fact_references_issue497' if version=='v1' else 'fact_references_v2_issue497')
    archive=folder/'v1-partial/source.zip' if version=='v1' else folder/'source.zip'
    seal=json.loads((folder/'seal.json').read_text(encoding='utf-8'))
    expected=(json.loads((folder/'v1-partial/status.json').read_text(encoding='utf-8'))['source_zip_sha256']
              if version=='v1' else 'b1b347d726b0c1c5fc004d8adb084c2f2dee675711c8813b2c26347093eb2b6f')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
    with zipfile.ZipFile(archive) as source:
        seal_name=(folder.relative_to(ROOT)/'seal.json').as_posix()
        assert set(source.namelist())==set(seal['files'])|{seal_name}
        assert source.read(seal_name)==(folder/'seal.json').read_bytes()
        for name,digest in seal['files'].items():
            assert hashlib.sha256(source.read(name)).hexdigest()==digest


def test_v1_partial_is_retained_without_claiming_completion():
    folder=DATA/'fact_references_issue497'
    status=json.loads((folder/'v1-partial/status.json').read_text(encoding='utf-8'))
    raw=gzip.decompress((folder/'v1-partial/records.jsonl.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==status['raw_sha256']
    rows=[json.loads(line) for line in raw.splitlines()]
    seal=json.loads((folder/'seal.json').read_text(encoding='utf-8'))
    assert status['status']=='STOPPED_INCOMPLETE' and len(rows)==50
    assert [row['row'] for row in rows]==seal['plan'][:50]
    assert all(row['source_unchanged'] for row in rows)


@pytest.mark.parametrize('version',['v1','v2'])
def test_full_replay_on_recorded_unicode_runtime(version):
    if sys.version_info[:2]!=(3,12) or unicodedata.unidata_version!='15.0.0':
        pytest.skip('frozen archive replay requires recorded Python3.12/Unicode15')
    subprocess.run([sys.executable,str(DATA/'fact_references_v2_issue497/replay_archive.py'),
                    '--version',version],cwd=ROOT,check=True,timeout=300)


def test_full_v2_ledger_manifest_scoring_and_posthoc_diagnostics():
    folder=DATA/'fact_references_v2_issue497'
    result=folder/'results'
    manifest=json.loads((result/'manifest.json').read_text(encoding='utf-8'))
    assert {p.name for p in result.iterdir()}==set(manifest)|{'manifest.json'}
    for name,digest in manifest.items():
        assert hashlib.sha256((result/name).read_bytes()).hexdigest()==digest
    raw=gzip.decompress((result/'records.jsonl.gz').read_bytes())
    metadata=json.loads((result/'metadata.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(raw).hexdigest()==metadata['raw_sha256']
    rows=[json.loads(line) for line in raw.splitlines()]
    seal=json.loads((folder/'seal.json').read_text(encoding='utf-8'))
    assert [row['row'] for row in rows]==seal['plan'] and len(rows)==2562
    assert sum(row['result']['model_requested'] for row in rows)==1560
    assert sum(not row['result']['model_requested'] and row['row']['variant']!='deterministic' for row in rows)==960
    assert all(row['source_unchanged'] and len(row['result']['trace'])<=4 for row in rows)
    checkpoint=metadata['controlled_checkpoint']
    assert hashlib.sha256(raw[:checkpoint['raw_bytes']]).hexdigest()==checkpoint['raw_sha256']
    from scripts.summarize_fact_references_issue497 import summarize
    cases=json.loads((folder/'cases.json').read_text(encoding='utf-8'))
    summary=json.loads((result/'summary.json').read_text(encoding='utf-8'))
    assert summarize(rows,cases)==summary
    assert all(value['new_supported_task_admission']=='NO-GO' for key,value in summary['groups'].items()
               if key.endswith(('/native_refs','/json_refs')))
    spec=importlib.util.spec_from_file_location('posthoc_diagnostics',folder/'diagnostics.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.diagnose(rows)==json.loads((result/'diagnostics.json').read_text(encoding='utf-8'))
