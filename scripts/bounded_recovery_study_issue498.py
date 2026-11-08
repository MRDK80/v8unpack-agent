"""Frozen bounded JSON recovery study; no changes to R11 source or data."""
import copy
import json
import math
import os
import sys
import time
import unicodedata
from pathlib import Path

from examples.bounded_fact_agent import BoundedRecovery, TimedLocalOllama
from scripts import fact_reference_study_v2_issue497 as historical
from scripts import minimal_context_issue496 as ledger
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.task_evidence import build_task_evidence

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'docs/research/bounded_recovery_issue498'


def trial(case,row,folder,chat,*,clock=None):
    started=time.monotonic()
    if row['variant']=='deterministic':
        record=historical.baseline_trial(case,row,folder,chat)
        record['clock_trace']=[]
        return record
    module=ledger.engine();env=module.prepare(case,folder)
    ticks=[];base=None
    def recording_clock():
        nonlocal base
        if clock is None:
            now=time.monotonic()
            if base is None:base=now
            value=now-base
        else:value=clock()
        ticks.append(value)
        return value
    contract=env['contract']
    if contract is None:
        result={'trace':[],'events':[],'states':['unsupported'],'outcome':'unsupported',
            'error':None,'limited':False,'model_requested':False,'reader_output':None,
            'unsupported_reason':env['unsupported_reason'],'stop_reason':'unsupported',
            'usage_complete':True}
    else:
        evidence=build_task_evidence(env['root'],ModuleIndex.from_entries([contract.entry]),
            contract.entry.module_id,operation=contract.operation,declaration_name=contract.declaration_name)
        canonical=dict(row,variant='json_refs')
        scope='request_'+module.digest(json.dumps(canonical,sort_keys=True).encode())[:24]
        agent=BoundedRecovery(evidence,output_dir=env['output'],scope_id=scope,
            profile=row['profile'],seed=row['seed'],calibration=True,clock=recording_clock)
        result=agent.run(chat)
    path=env['output']/'report.json'
    return {'row':row,'result':result,'clock_trace':ticks,
        'source_sha256':module.digest(env['raw']),'source_unchanged':env['path'].read_bytes()==env['raw'],
        'report_file':path.read_text(encoding='utf-8') if path.exists() else None,
        'output_files':sorted(p.name for p in env['output'].iterdir()),
        'trial_wall_seconds':time.monotonic()-started}


def plan(cases,profiles,seeds):
    rows=[{'profile':'none','seed':17,'case':case['id'],'variant':'deterministic'} for case in cases]
    rows += [{'profile':profile,'seed':seed,'case':case['id'],'variant':'json_recovery'}
             for profile in profiles for seed in seeds for case in cases]
    return rows


def sealed_files():
    names=[p.relative_to(ROOT).as_posix() for folder in ('v8unpack_agent','examples','scripts')
           for p in (ROOT/folder).glob('*.py')]
    names+=['docs/research/bounded_recovery_issue498/cases.json',
            'docs/research/bounded_recovery_issue498/protocol.json',
            'docs/research/agent_benchmark_issue479/system.txt',
            'docs/research/agent_defense_v2_issue494/protocol.json']
    names += ['docs/research/fact_references_v2_issue497/results/'+name
              for name in ('records.jsonl.gz','summary.json','metadata.json','manifest.json')]
    return sorted(set(names))


def replay(case,row,recorded,folder):
    steps=iter(recorded['result']['trace']);ticks=iter(recorded['clock_trace'])
    values=recorded['clock_trace']
    if (any(type(v) not in {int,float} or not math.isfinite(v) or v<0 for v in values)
            or values!=sorted(values) or values and values[0]!=0):
        raise ValueError('invalid recorded admission clock')
    def clock():return next(ticks)
    def chat(body,*,timeout_seconds):
        step=next(steps)
        if body!=step['request'] or timeout_seconds!=step['timeout_seconds']:
            raise ValueError('replay request/timeout mismatch')
        if 'error' in step:
            if step['error']=='local_chat_timeout':raise TimeoutError('recorded timeout')
            raise RuntimeError('recorded failure')
        return copy.deepcopy(step['response'])
    result=trial(case,row,folder,chat,clock=clock)
    if (next(steps,None) is not None or next(ticks,None) is not None
            or ledger.normalize(result)!=ledger.normalize(recorded)):
        raise ValueError('replay decision/effect mismatch')
    return result


def configure():
    ledger.DATA=DATA;ledger.trial=trial;ledger.plan=plan;ledger.sealed_files=sealed_files


def load_records(path):
    raw=path.read_bytes() if path.exists() else b''
    if raw and not raw.endswith(b'\n'):raise ValueError('unfinished ledger line; explicit disposition required')
    return [json.loads(line) for line in raw.split(b'\n') if line]


def audit(output,complete=True):
    import tempfile
    seal=ledger.verify();records=load_records(output/'records.jsonl')
    if [r['row'] for r in records]!=(seal['plan'] if complete else seal['plan'][:len(records)]):
        raise ValueError('incomplete/changed ledger')
    cases={c['id']:c for c in ledger.read(DATA/'cases.json')}
    for index,record in enumerate(records):
        with tempfile.TemporaryDirectory() as temporary:
            replay(cases[record['row']['case']],record['row'],record,Path(temporary)/'trial')
        folder=output/'artifacts'/f'{index:05d}'
        inputs=[p for p in (folder/'input').rglob('*') if p.is_file()]
        if len(inputs)!=1 or ledger.file_hash(inputs[0])!=record['source_sha256']:
            raise ValueError('stored source changed')
        if sorted(p.name for p in (folder/'output').iterdir())!=record['output_files']:
            raise ValueError('stored output inventory changed')
        path=folder/'output/report.json'
        expected=record['report_file'].encode('utf-8') if record['report_file'] is not None else None
        if (path.read_bytes() if path.exists() else None)!=expected:
            raise ValueError('stored effect changed')
    return records


def run(output,endpoint,resume=False):
    seal=ledger.verify();model=TimedLocalOllama(endpoint)
    runtime={'environment':{'python':sys.version.split()[0],'unidata':unicodedata.unidata_version},
             'profiles':{p:model.verify(p) for p in ledger.read(DATA/'protocol.json')['profiles']}}
    if resume:
        if ledger.read(output/'runtime.json')!=runtime:raise ValueError('runtime changed')
        records=audit(output,complete=False)
    else:
        output.mkdir(parents=True,exist_ok=False)
        (output/'runtime.json').write_text(json.dumps(runtime,indent=2),encoding='utf-8')
        records=[]
    cases={c['id']:c for c in ledger.read(DATA/'cases.json')}
    with (output/'records.jsonl').open('a',encoding='utf-8',newline='\n') as stream:
        for index in range(len(records),len(seal['plan'])):
            if (output/'PAUSE').exists():print('PAUSED before',index,flush=True);return
            row=seal['plan'][index];folder=output/'artifacts'/f'{index:05d}'
            if folder.exists():raise ValueError('unfinished trial; explicit disposition required')
            record=trial(cases[row['case']],row,folder,model.chat)
            stream.write(json.dumps(record,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
            print(index+1,'/',len(seal['plan']),record['result']['outcome'],flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','run','audit'])
    parser.add_argument('--output',type=Path);parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--resume',action='store_true');args=parser.parse_args();configure()
    if args.command=='freeze':ledger.freeze()
    elif args.output is None:parser.error('--output required')
    elif args.command=='run':run(args.output,args.endpoint,args.resume)
    else:print('PASS all',len(audit(args.output)),'rows/files/clock replay')
