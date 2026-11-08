"""Full frozen-source R12 replay, no models/network/Git (recorded Unicode)."""
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
if sys.version_info[:2]!=(3,12) or unicodedata.unidata_version!='15.0.0':
    raise ValueError('recorded Python3.12/Unicode15.0.0 required')
archive=HERE/'source.zip'
if hashlib.sha256(archive.read_bytes()).hexdigest()!='58e825fed954224aac349bb13bb22622cfd9ca7f0f7a86d28cdeaab209b35034':
    raise ValueError('pinned source ZIP changed')
folder=HERE/'results'
manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
for name,digest in manifest.items():
    if Path(name).name!=name or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=digest:
        raise ValueError('result archive changed')
raw=gzip.decompress((folder/'records.jsonl.gz').read_bytes())
metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
if hashlib.sha256(raw).hexdigest()!=metadata['raw_sha256']:
    raise ValueError('original raw changed')
code='''
import gzip,json,sys,tempfile
from pathlib import Path
from scripts import bounded_recovery_study_issue498 as s
s.configure();seal=s.ledger.verify()
raw=gzip.decompress(Path(sys.argv[1]).read_bytes())
rows=[json.loads(line) for line in raw.split(b'\\n') if line]
assert len(rows)==882 and [r['row'] for r in rows]==seal['plan']
cases={c['id']:c for c in s.ledger.read(s.DATA/'cases.json')}
for record in rows:
    with tempfile.TemporaryDirectory() as temporary:
        s.replay(cases[record['row']['case']],record['row'],record,Path(temporary)/'trial')
    assert record['source_unchanged']
print('PASS all882 clock/request/effect replay; no models')
'''
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    with zipfile.ZipFile(archive) as source:
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in source.namelist()):
            raise ValueError('unsafe archive name')
        source.extractall(root)
    subprocess.run([sys.executable,'-c',code,str(folder/'records.jsonl.gz')],cwd=root,
        env=dict(os.environ,PYTHONPATH=str(root)),check=True,timeout=300)
