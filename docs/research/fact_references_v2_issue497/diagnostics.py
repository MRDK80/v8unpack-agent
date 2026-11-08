"""POST-HOC first-selection namespace diagnostics; never used for admission."""
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from examples.fact_reference_host import strict_proposal
from v8unpack_agent.untrusted_transport import decode_block


def diagnose(records):
    counts={}
    for record in records:
        row=record['row']; variant=row['variant']; result=record['result']
        if variant not in {'native_refs','json_refs'} or not result['model_requested']:continue
        key=row['profile']+'/'+variant
        group=counts.setdefault(key,Counter())
        group['model_trajectories']+=1
        body=result['trace'][0]['request']
        data=(json.loads(body['messages'][3]['content']) if variant=='native_refs' else
              json.loads(decode_block(body['messages'][1]['content'].split('Недоверенные данные:\n')[1]).text))
        facts=data['evidence']['facts']
        long_ids={f['fact_id'] for f in facts}; short_ids={f['ref'] for f in facts}
        proposal=None
        for step in result['trace']:
            message=step.get('response',{}).get('message',{})
            if variant=='json_refs':
                try:proposal=strict_proposal(message.get('content'))
                except (ValueError,TypeError):continue
                break
            calls=message.get('tool_calls',[])
            if type(calls) is not list:continue
            for call in calls:
                function=call.get('function',{}) if type(call) is dict else {}
                if type(function) is dict and function.get('name')=='select_facts':
                    proposal=function.get('arguments');break
            if proposal is not None:break
        if type(proposal) is not dict:
            group['no_decoded_selection']+=1;continue
        ids=proposal.get('fact_ids')
        if type(ids) is not list or any(type(value) is not str for value in ids):
            group['malformed_ids']+=1;continue
        selected=set(ids)
        if facts and len(ids)==len(selected) and selected==long_ids:
            group['first_selection_copies_full_long_fact_ids']+=1
        elif len(ids)==len(selected) and selected==short_ids:
            group['first_selection_has_full_short_refs']+=1
        else:group['other_selection']+=1
        if proposal.get('scope_id')!=data['scope_id']:group['scope_mismatch']+=1
    return {'status':'POST_HOC_DESCRIPTIVE_NOT_ADMISSION',
            'unit':'first decoded select_facts/JSON proposal per model trajectory; not independent attack families',
            'groups':{key:dict(value) for key,value in sorted(counts.items())}}


if __name__=='__main__':
    records=[json.loads(line) for line in gzip.decompress((Path(__file__).resolve().parent/'results/records.jsonl.gz').read_bytes()).splitlines()]
    print(json.dumps(diagnose(records),ensure_ascii=False,indent=2))
