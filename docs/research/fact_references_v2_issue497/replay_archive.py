"""Offline full replay from pinned source ZIP, independent of later product changes.

Run on the recorded Python 3.12 / Unicode 15.0.0; no model/network/Git needed.
"""
import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--version',choices=['v1','v2'],default='v2')
args=parser.parse_args()
if sys.version_info[:2]!=(3,12) or unicodedata.unidata_version!='15.0.0':
    raise ValueError('recorded Python 3.12 / Unicode 15.0.0 required')
if args.version=='v1':
    data=HERE.parent/'fact_references_issue497'/'v1-partial'
    archive=data/'source.zip'
    expected='eb1f39a1d364c90e730dc8a04e11b26ded3ab912bf2694e2901a6c2c15384059'
    module='fact_reference_study_issue497'
    count=50
else:
    data=HERE/'results'
    archive=HERE/'source.zip'
    expected='b1b347d726b0c1c5fc004d8adb084c2f2dee675711c8813b2c26347093eb2b6f'
    module='fact_reference_study_v2_issue497'
    count=2562
if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
    raise ValueError('frozen source ZIP changed')
metadata=json.loads((data/('status.json' if args.version=='v1' else 'metadata.json')).read_text(encoding='utf-8'))
raw=gzip.decompress((data/'records.jsonl.gz').read_bytes())
if hashlib.sha256(raw).hexdigest()!=metadata['raw_sha256']:
    raise ValueError('original raw ledger changed')
if (data/'manifest.json').exists():
    manifest=json.loads((data/'manifest.json').read_text(encoding='utf-8'))
    for name,expected_hash in manifest.items():
        if Path(name).name!=name or hashlib.sha256((data/name).read_bytes()).hexdigest()!=expected_hash:
            raise ValueError('result archive changed')
code='''
import gzip,json,sys,tempfile
from pathlib import Path
from scripts import MODULE as study
study.configure()
seal=study.previous.verify()
records=[json.loads(line) for line in gzip.decompress(Path(sys.argv[1]).read_bytes()).splitlines()]
assert len(records)==int(sys.argv[2])
assert [record['row'] for record in records]==seal['plan'][:len(records)]
cases={case['id']:case for case in study.previous.read(study.DATA/'cases.json')}
for record in records:
    with tempfile.TemporaryDirectory() as temporary:
        study.previous.replay(cases[record['row']['case']],record['row'],record,Path(temporary)/'trial')
    assert record['source_unchanged']
print('PASS full frozen-source replay',len(records),'rows; no model calls')
'''.replace('MODULE',module)
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    with zipfile.ZipFile(archive) as source:
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in source.namelist()):
            raise ValueError('unsafe archive name')
        source.extractall(root)
    env=dict(os.environ,PYTHONPATH=str(root))
    subprocess.run([sys.executable,'-c',code,str(data/'records.jsonl.gz'),str(count)],
                   cwd=root,env=env,check=True,timeout=300)
