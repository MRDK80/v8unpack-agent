"""Matched marker experiment; reuses frozen executor through isolated module hooks."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

from scripts.marker_transport_issue495 import Descriptor, decode, render

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/research/marker_experiment_issue495'


def engine():
    spec = importlib.util.spec_from_file_location('marker_trial_engine', ROOT / 'scripts/agent_acceptance_issue485.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trial(case, row, folder, chat, nonce):
    module = engine()
    original_prompts = module.prompts
    original_observation = module.observation
    variant = row['variant']
    host = Descriptor('UNTRUSTED_DATA_START', 'UNTRUSTED_DATA_FINISH', nonce)
    source = module.source_text(case)
    if variant in ('custom_guard', 'nonce_guard'):
        observation = render(source, host)
        assert decode(observation, host).text == source
        def prompts(task):
            system, user, tools = original_prompts(task)
            return system + '\n' + host.instruction(), user, tools
        module.prompts = prompts
        module.observation = lambda case, variant, env: observation
    else:
        module.observation = original_observation
    record = module.trial(case, row, folder, chat)
    record['marker_descriptor'] = None if variant not in ('custom_guard', 'nonce_guard') else {
        'begin': host.begin, 'end': host.end, 'nonce': host.nonce}
    return record


def plan(cases, profiles, seeds):
    variants = ['legacy_guard', 'framed_guard', 'custom_guard', 'nonce_guard']
    rows = []
    for profile in profiles:
        for seed_index, seed in enumerate(seeds):
            for index, case in enumerate(cases):
                shift = (index + seed_index) % len(variants)
                for variant in variants[shift:] + variants[:shift]:
                    rows.append({'profile': profile, 'seed': seed, 'case': case['id'], 'variant': variant})
    return rows


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(record):
    value = copy.deepcopy(record)
    for step in value['result']['trace']:
        step.pop('wall_seconds', None)
    return value


def replay(case, row, recorded, folder):
    steps = iter(recorded['result']['trace'])
    def chat(request):
        step = next(steps)
        if request != step['request']:
            raise ValueError('replay request mismatch')
        if 'error' in step:
            raise RuntimeError('recorded failure')
        return copy.deepcopy(step['response'])
    host = recorded['marker_descriptor']
    result = trial(case, row, folder, chat, host['nonce'] if host else '0' * 32)
    if next(steps, None) is not None or normalize(result) != normalize(recorded):
        raise ValueError('replay effect mismatch')
    return result


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sealed_files():
    names = ['scripts/marker_experiment_issue495.py','scripts/marker_transport_issue495.py',
             'scripts/agent_acceptance_issue485.py','scripts/agent_benchmark_issue479.py',
             'scripts/audit_client_results_issue484.py',
             'examples/agent_reference_client.py','examples/validated_report_executor.py',
             'docs/research/agent_benchmark_issue479/system.txt',
             'docs/research/marker_experiment_issue495/cases.json',
             'docs/research/agent_defense_v2_issue494/protocol.json']
    return sorted(set(names + [p.relative_to(ROOT).as_posix() for folder in ('v8unpack_agent', 'examples', 'scripts') for p in (ROOT/folder).glob('*.py')]))


def freeze():
    import secrets
    import subprocess
    cases = read(DATA/'cases.json')
    protocol = read(ROOT/'docs/research/agent_defense_v2_issue494/protocol.json')
    rows = plan(cases, protocol['profiles'], protocol['seeds'])
    path = DATA/'seal.json'
    if path.exists():
        raise ValueError('already frozen')
    sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    hashes = {name:file_hash(ROOT/name) for name in sealed_files()}
    for name, expected in hashes.items():
        committed = subprocess.check_output(['git','show',sha+':'+name],cwd=ROOT)
        if hashlib.sha256(committed).hexdigest() != expected:
            raise ValueError('uncommitted source')
    nonces=[]
    for row in rows:
        nonce = 'a'*32 if row['variant']=='custom_guard' else secrets.token_hex(16)
        source = engine().source_text(next(c for c in cases if c['id']==row['case']))
        if nonce in source:
            raise ValueError('freeze collision')
        nonces.append(nonce)
    path.write_text(json.dumps({'sha':sha,'files':hashes,'plan':rows,'nonces':nonces},indent=2)+'\n',encoding='utf-8')
    print('sealed',len(rows),file_hash(path))


def verify():
    seal=read(DATA/'seal.json')
    if set(seal['files']) != set(sealed_files()):
        raise ValueError('file set changed')
    for name, expected in seal['files'].items():
        if file_hash(ROOT/name) != expected:
            raise ValueError('sealed bytes changed: '+name)
    return seal


def run(output, endpoint, resume=False):
    import os
    seal=verify()
    module=engine()
    model=module.client.LocalOllama(endpoint)
    runtime={profile:model.verify(profile) for profile in module.PROFILES}
    if resume:
        if read(output/'runtime.json') != runtime:
            raise ValueError('runtime changed')
        records=audit(output,complete=False)
    else:
        output.mkdir(parents=True,exist_ok=False)
        (output/'runtime.json').write_text(json.dumps(runtime,indent=2),encoding='utf-8')
        records=[]
    cases={c['id']:c for c in read(DATA/'cases.json')}
    with (output/'records.jsonl').open('a',encoding='utf-8',newline='\n') as stream:
        for index in range(len(records),len(seal['plan'])):
            if (output/'PAUSE').exists():
                print('PAUSED before',index,flush=True)
                return
            row=seal['plan'][index]
            folder=output/'artifacts'/f'{index:05d}'
            if folder.exists():
                raise ValueError('unfinished trial; explicit disposition required')
            record=trial(cases[row['case']],row,folder,model.chat,seal['nonces'][index])
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            print(index+1,'/',len(seal['plan']),record['result']['outcome'],flush=True)


def audit(output,complete=True):
    import tempfile
    seal=verify()
    path=output/'records.jsonl'
    records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
    expected=seal['plan'] if complete else seal['plan'][:len(records)]
    if [r['row'] for r in records] != expected:
        raise ValueError('incomplete or changed ledger')
    cases={c['id']:c for c in read(DATA/'cases.json')}
    for index,record in enumerate(records):
        with tempfile.TemporaryDirectory() as directory:
            replay(cases[record['row']['case']],record['row'],record,Path(directory)/'trial')
        folder=output/'artifacts'/f'{index:05d}'
        path=folder/'output/report.json'
        actual=path.read_text(encoding='utf-8') if path.exists() else None
        if actual != record['report_file']:
            raise ValueError('stored effect changed')
        inputs = list((folder/'input').rglob('*'))
        source_files = [p for p in inputs if p.is_file()]
        if len(source_files) != 1 or file_hash(source_files[0]) != record['source_sha256']:
            raise ValueError('stored source changed')
    return records


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['freeze','run','audit'])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.command=='freeze':
        freeze()
    elif args.output is None:
        parser.error('--output required')
    elif args.command=='run':
        run(args.output,args.endpoint,args.resume)
    else:
        print('audited',len(audit(args.output)))
