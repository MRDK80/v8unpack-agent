"""Verify every archived Unicode study record with pinned code, without network."""
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
    if phase not in ('unicode','decode'):raise ValueError('unknown phase')
    target=data/'results'/phase;manifest=read(target/'manifest.json');metadata=read(target/'metadata.json')
    if set(manifest)!={'records.jsonl.gz','runtime.json','runtime-end.json','summary.json','metadata.json'}:raise ValueError('manifest inventory')
    for name,digest in manifest.items():
        if sha((target/name).read_bytes())!=digest:raise ValueError('archive bytes '+name)
    raw=gzip.decompress((target/'records.jsonl.gz').read_bytes())
    if not raw.endswith(b'\n')or len(raw)!=metadata['raw_bytes']or sha(raw)!=metadata['raw_sha256']:raise ValueError('raw ledger')
    seal=read(data/'seal.json');code=read(data/'code-seal.json')
    if any(metadata.get(k)!=v for k,v in seal.items()):raise ValueError('metadata seal')
    if sha((data/'source.zip').read_bytes())!=seal['source_zip_sha256']:raise ValueError('ZIP drift')
    if sha((data/'code-seal.json').read_bytes())!=seal['code_seal_sha256']or code['sha']!=seal['code_sha']:raise ValueError('code seal')
    for name,digest in seal['input_sha256'].items():
        if sha((data/name).read_bytes())!=digest:raise ValueError('input drift')
    runtime=read(target/'runtime.json')
    if runtime!=read(target/'runtime-end.json')or runtime!=read(data/'runtime-preregistered.json'):raise ValueError('runtime drift')
    config=read(data/'config.json')
    if set(runtime['profiles'])!=set(config['profiles']):raise ValueError('profile inventory')
    for profile,actual in runtime['profiles'].items():
        if actual['profile']!=profile or actual['model']!=config['profiles'][profile]or actual['ollama']['version']!=config['server_version']:
            raise ValueError('model digest/options drift')
    rows=[json.loads(x)for x in raw.split(b'\n')if x]
    if len(rows)!=metadata['rows']or len(rows)!=config['rows'][phase]:raise ValueError('row count')
    if[r['protocol_row']if phase=='unicode'else r['row']for r in rows]!=read(data/'plan.json')[phase]:raise ValueError('plan drift')
    prefix='docs/research/unicode_study_issue519/'
    with zipfile.ZipFile(data/'source.zip')as archive:
        names=set(code['files'])|{prefix+'code-seal.json'}
        if len(archive.namelist())!=len(names)or set(archive.namelist())!=names:raise ValueError('source inventory')
        for name in names:
            relative=PurePosixPath(name)
            if relative.is_absolute()or'..'in relative.parts or chr(92)in name:raise ValueError('unsafe source path')
            if name in code['files']and sha(archive.read(name))!=code['files'][name]:raise ValueError('source bytes')
            if name.startswith(prefix)and archive.read(name)!=(data/name[len(prefix):]).read_bytes():raise ValueError('published input bytes')
    return rows,runtime,metadata


def replay(data,phase):
    rows,runtime,metadata=verify_archive(data,phase)
    if sys.platform!='linux'or sys.version_info[:2]!=(3,12)or unicodedata.unidata_version!=runtime['environment']['unidata']:
        raise ValueError('Integrity PASS; full effects replay requires Linux/Python3.12/recorded Unicode')
    with tempfile.TemporaryDirectory()as temporary:
        root=Path(temporary)
        with zipfile.ZipFile(data/'source.zip')as archive:
            for member in archive.infolist():
                path=root.joinpath(*PurePosixPath(member.filename).parts)
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.read(member))
        local=root/'docs/research/unicode_study_issue519'
        for name in ['source.zip','seal.json']:(local/name).write_bytes((data/name).read_bytes())
        ledger=root/'records.jsonl';ledger.write_bytes(gzip.decompress((data/'results'/phase/'records.jsonl.gz').read_bytes()))
        program='''import json,pathlib,sys
from scripts.unicode_study_issue519 import audit_records,phase_cases,summarize_phase
records=[json.loads(x)for x in pathlib.Path(sys.argv[1]).read_bytes().split(b'\\n')if x]
audit_records(sys.argv[2],records)
assert summarize_phase(sys.argv[2],records,phase_cases(sys.argv[2]))==json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8')),'scoring drift'
print('PASS full pinned replay',sys.argv[2],len(records))
'''
        subprocess.run([sys.executable,'-c',program,str(ledger),phase,str(data/'results'/phase/'summary.json')],cwd=root,
            env=dict(os.environ,PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1'),check=True)
    return {'phase':phase,'rows':len(rows),'code_sha':metadata['code_sha']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['unicode','decode']);args=parser.parse_args()
    data=Path(__file__).resolve().parent
    for phase in [args.phase]if args.phase else ['unicode','decode']:print(replay(data,phase))


if __name__=='__main__':main()
