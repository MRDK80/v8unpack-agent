"""All original phased records and pinned-code actual effects replay, no network."""
from __future__ import annotations

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
from pathlib import Path, PurePosixPath


def sha(raw):return hashlib.sha256(raw).hexdigest()

def read(path):return json.loads(path.read_text(encoding='utf-8'))


def verify_archive(data,phase):
    results=data/'results'/phase;metadata=read(results/'metadata.json');manifest=read(results/'manifest.json')
    if set(manifest)!={'records.jsonl.gz','runtime.json','runtime-end.json','summary.json','metadata.json'}:raise ValueError('manifest inventory')
    for name,digest in manifest.items():
        if sha((results/name).read_bytes())!=digest:raise ValueError('archive bytes '+name)
    raw=gzip.decompress((results/'records.jsonl.gz').read_bytes())
    if not raw.endswith(b'\n')or sha(raw)!=metadata['raw_sha256']or len(raw)!=metadata['raw_bytes']:raise ValueError('original raw ledger changed')
    seal=read(data/(phase+'-seal.json'));code=read(data/'code-seal.json')
    for name,key in [('source.zip','source_zip_sha256'),('code-seal.json','code_seal_sha256'),(phase+'-seal.json','phase_seal_sha256')]:
        if sha((data/name).read_bytes())!=metadata[key]:raise ValueError('source/phase seal drift')
    if metadata['code_sha']!=code['sha']or seal['code_sha']!=code['sha']:raise ValueError('source SHA drift')
    for name,digest in seal['files'].items():
        if sha((data/name).read_bytes())!=digest:raise ValueError('phase input bytes '+name)
    runtime=read(results/'runtime.json')
    if runtime!=read(results/'runtime-end.json'):raise ValueError('start/end runtime changed')
    if runtime!=read(data/'runtime-preregistered.json'):raise ValueError('preregistered runtime mismatch')
    protocol=read(data/'protocol.json')
    if set(runtime['profiles'])!=set(protocol['profiles']):raise ValueError('runtime profiles')
    for p in protocol['profiles']:
        actual=runtime['profiles'][p]
        if actual['profile']!=p or actual['model']!=protocol['profile_config'][p]or actual['ollama']['version']!=protocol['server_version']:raise ValueError('runtime digest/options')
    records=[json.loads(line)for line in raw.split(b'\n')if line]
    if[r['row']for r in records]!=seal['plan']or len(records)!=protocol['rows'][phase]:raise ValueError('complete planned rows required')
    for r in records:
        if(sha(r['nonce'].encode())if r['nonce']else None)!=r['row']['nonce_commitment']:raise ValueError('nonce commitment')
    with zipfile.ZipFile(data/'source.zip')as archive:
        names=set(code['files'])|{'docs/research/final_acceptance_issue502/code-seal.json'}
        if set(archive.namelist())!=names or len(archive.namelist())!=len(names):raise ValueError('sourceZIP inventory')
        for name,digest in code['files'].items():
            if sha(archive.read(name))!=digest:raise ValueError('pinned source '+name)
            prefix='docs/research/final_acceptance_issue502/'
            if name.startswith(prefix)and sha((data/name[len(prefix):]).read_bytes())!=digest:raise ValueError('published code input drift')
        if archive.read('docs/research/final_acceptance_issue502/code-seal.json')!=(data/'code-seal.json').read_bytes():raise ValueError('pinned code seal')
    return records,metadata,runtime


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase');args=parser.parse_args();data=Path(__file__).resolve().parent
    phases=[args.phase]if args.phase else [p.name for p in sorted((data/'results').iterdir())if p.is_dir()]
    for phase in phases:
        _records,metadata,runtime=verify_archive(data,phase)
        if sys.platform!='linux'or sys.version_info[:2]!=(3,12)or unicodedata.unidata_version!=runtime['environment']['unidata']:
            raise SystemExit('Integrity PASS; full recorded effects requireLinux/Python3.12/recordedUnicode')
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary)
            with zipfile.ZipFile(data/'source.zip')as archive:
                for member in archive.infolist():
                    relative=PurePosixPath(member.filename)
                    if relative.is_absolute()or'..'in relative.parts or chr(92)in member.filename:raise ValueError('unsafe sourceZIP path')
                    target=root.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(member))
            localdata=root/'docs/research/final_acceptance_issue502'
            (localdata/'source.zip').write_bytes((data/'source.zip').read_bytes())
            seal=read(data/(phase+'-seal.json'))
            for name in [*seal['files'],phase+'-seal.json']:(localdata/name).write_bytes((data/name).read_bytes())
            ledger=root/'records.jsonl';ledger.write_bytes(gzip.decompress((data/'results'/phase/'records.jsonl.gz').read_bytes()))
            code='''import json,pathlib,sys
from scripts.final_study_issue502 import audit_records,cases_for
from scripts.score_final_issue502 import summarize
r=[json.loads(x)for x in pathlib.Path(sys.argv[1]).read_bytes().split(b'\\n')if x]
audit_records(r,sys.argv[2])
actual=summarize(r,cases_for(sys.argv[2]))
assert actual==json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8')),'scorer drift'
print('PASS all',len(r),sys.argv[2],'request/clock/actualfiles/independent scoring')
'''
            subprocess.run([sys.executable,'-c',code,str(ledger),phase,str(data/'results'/phase/'summary.json')],cwd=root,
                env=dict(os.environ,PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1'),check=True)
        print('Pinned code',metadata['code_sha'],phase)


if __name__=='__main__':main()
