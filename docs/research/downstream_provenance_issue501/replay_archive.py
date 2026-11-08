"""Verify complete R15 archive and replay its pinned source without LLM/network."""
from __future__ import annotations

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


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_archive(data):
    results = data / 'results'
    metadata = json.loads((results / 'metadata.json').read_text(encoding='utf-8'))
    manifest = json.loads((results / 'manifest.json').read_text(encoding='utf-8'))
    expected = {'records.jsonl.gz', 'summary.json', 'metadata.json', 'runtime.json', 'runtime-end.json'}
    if set(manifest) != expected:
        raise ValueError('archive manifest inventory changed')
    for name, digest in manifest.items():
        if sha((results / name).read_bytes()) != digest:
            raise ValueError('archive bytes changed: ' + name)
    raw = gzip.decompress((results / 'records.jsonl.gz').read_bytes())
    if len(raw) != metadata['raw_records_bytes'] or sha(raw) != metadata['raw_records_sha256']:
        raise ValueError('original ledger changed')
    if not raw.endswith(b'\n'):
        raise ValueError('unfinished original ledger')
    if sha((data / 'seal.json').read_bytes()) != metadata['seal_sha256']:
        raise ValueError('source seal changed')
    if sha((data / 'source.zip').read_bytes()) != metadata['source_zip_sha256']:
        raise ValueError('pinned source ZIP changed')
    start = json.loads((results / 'runtime.json').read_text(encoding='utf-8'))
    if start != json.loads((results / 'runtime-end.json').read_text(encoding='utf-8')):
        raise ValueError('recorded start/end runtime changed')
    records = [json.loads(line) for line in raw.split(b'\n') if line]
    seal = json.loads((data / 'seal.json').read_text(encoding='utf-8'))
    if metadata['source_head'] != seal['sha']:
        raise ValueError('model source head mismatch')
    if len(records) != 738 or [r['row'] for r in records] != seal['plan']:
        raise ValueError('complete planned rows required')
    with zipfile.ZipFile(data / 'source.zip') as archive:
        names = set(seal['files']) | {'docs/research/downstream_provenance_issue501/seal.json'}
        if set(archive.namelist()) != names or len(archive.namelist()) != len(names):
            raise ValueError('pinned source inventory changed')
        for name, digest in seal['files'].items():
            if sha(archive.read(name)) != digest:
                raise ValueError('pinned source bytes changed')
            prefix = 'docs/research/downstream_provenance_issue501/'
            if name.startswith(prefix) and sha((data / name[len(prefix):]).read_bytes()) != digest:
                raise ValueError('published experiment input changed')
        if archive.read('docs/research/downstream_provenance_issue501/seal.json') != (data / 'seal.json').read_bytes():
            raise ValueError('pinned seal changed')
        protocol = json.loads(archive.read('docs/research/downstream_provenance_issue501/protocol.json'))
        if set(start['profiles']) != set(protocol['profiles']):
            raise ValueError('runtime profile set changed')
        for profile in protocol['profiles']:
            actual = start['profiles'][profile]
            if (actual['model'] != protocol['profile_config'][profile] or actual['profile'] != profile
                    or actual['ollama']['version'] != protocol['server_version']):
                raise ValueError('recorded pinned runtime mismatch')
    return records, metadata, start


def main():
    data = Path(__file__).resolve().parent
    _records, metadata, runtime = verify_archive(data)
    environment = runtime['environment']
    if (sys.platform != 'linux' or sys.version_info[:2] != (3, 12)
            or unicodedata.unidata_version != environment['unidata']):
        raise SystemExit('Full recorded effect replay requires Linux/Python3.12/recorded Unicode; integrity verified.')
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with zipfile.ZipFile(data / 'source.zip') as archive:
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or '..' in relative.parts or chr(92) in member.filename:
                    raise ValueError('unsafe pinned source path')
                target = root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        ledger = root / 'original-records.jsonl'
        ledger.write_bytes(gzip.decompress((data / 'results/records.jsonl.gz').read_bytes()))
        code = '''import json,pathlib,sys
from scripts.downstream_study_issue501 import audit_records,read,DATA
from scripts.score_downstream_issue501 import summarize
records=[json.loads(x) for x in pathlib.Path(sys.argv[1]).read_bytes().split(b'\\n') if x]
audit_records(records)
actual=summarize(records,read(DATA/'cases.json'))
expected=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))
assert actual==expected,'frozen scorer mismatch'
print('PASS all',len(records),'rows/request/clock/effect files and independent scoring')
'''
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-c', code, str(ledger), str(data / 'results/summary.json')],
                       cwd=root, env=env, check=True)
    print('Pinned model source:', metadata['source_head'])


if __name__ == '__main__':
    main()
