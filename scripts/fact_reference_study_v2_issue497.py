"""Frozen R11 native/JSON reference selection versus prior evidence client."""
import copy
import json
import time
from pathlib import Path

from examples import fact_reference_host_v2 as refs
from scripts import minimal_context_issue496 as previous
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.task_evidence import build_task_evidence
from v8unpack_agent.untrusted_transport import encode_block

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/research/fact_references_v2_issue497'
VARIANTS = ['evidence_guard', 'native_refs', 'json_refs']


def trial(case, row, folder, chat):
    started = time.monotonic()
    if row['variant'] in {'evidence_guard', 'deterministic'}:
        return baseline_trial(case, row, folder, chat)
    module = previous.engine()
    env = module.prepare(case, folder)
    result = {'trace': [], 'events': [], 'outcome': 'no_report', 'error': None,
              'limited': False, 'model_requested': False, 'reader_output': None,
              'unsupported_reason': env['unsupported_reason']}
    contract = env['contract']
    if contract is None:
        result['outcome'] = 'unsupported'
    else:
        view = build_task_evidence(env['root'], ModuleIndex.from_entries([contract.entry]),
            contract.entry.module_id, operation=contract.operation,
            declaration_name=contract.declaration_name)
        host = refs.FactReferenceHost(view, output_dir=env['output'],
                                      scope_id='request_' + module.digest(json.dumps(row, sort_keys=True).encode())[:24])
        observation = json.dumps(host.observation(), ensure_ascii=False)
        system = refs.SYSTEM
        user = 'Предложи полный набор fact_ids из ref всех фактов выбранной host операции и точный scope_id.'
        if row['variant'] == 'native_refs':
            messages = module.historical.initial_messages(system, user, observation)
        else:
            messages = [{'role': 'system', 'content': system + '\nВерни только JSON по этой схеме: ' + json.dumps(host.schema())},
                        {'role': 'user', 'content': user + '\nНедоверенные данные:\n' + encode_block(observation, n=1, kind='text')}]
        for _ in range(module.client.MAX_STEPS):
            body = module.request(row, messages, host.tools() if row['variant'] == 'native_refs' else None)
            if row['variant'] == 'json_refs': body['format'] = host.schema()
            result['model_requested'] = True
            request_started = time.monotonic()
            try: response = chat(copy.deepcopy(body))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                result['trace'].append({'request': body, 'error': 'local_chat_failed'})
                result['error'] = 'local_chat_failed'; break
            result['trace'].append({'request': body, 'response': copy.deepcopy(response), 'wall_seconds': time.monotonic()-request_started})
            if (type(response) is not dict or response.get('model') != module.client.PROFILES[row['profile']]['model'] or
                    type(response.get('message')) is not dict or response['message'].get('role') != 'assistant'):
                result['error'] = 'invalid_response'; break
            message = response['message']; messages.append(copy.deepcopy(message))
            result['limited'] = result['limited'] or response.get('done_reason') == 'length'
            if row['variant'] == 'json_refs':
                if message.get('tool_calls'):
                    event = {'status': 'blocked', 'reason': 'json_tools_forbidden'}
                else:
                    try: proposal = refs.strict_proposal(message.get('content'))
                    except (ValueError, TypeError): event = {'status': 'blocked', 'reason': 'invalid_json'}
                    else: event = host.execute(proposal)
                messages.append({'role': 'user', 'content': json.dumps({'status':event['status'], 'reason':event.get('reason')})})
            else:
                calls = message.get('tool_calls', [])
                if type(calls) is list and not calls: break
                event = host.native(calls)
                # Malformed batches are never dispatched one by one.
                names = [c.get('function', {}).get('name', 'unknown') if type(c) is dict and type(c.get('function')) is dict else 'unknown'
                         for c in calls] if type(calls) is list else ['unknown']
                for name in names:
                    messages.append({'role': 'tool', 'tool_name': name if type(name) is str else 'unknown',
                                     'content': json.dumps(event, ensure_ascii=False)})
            result['events'].append(copy.deepcopy(event))
            if event['status'] == 'committed':
                result['outcome'] = 'committed_after_block' if any(e['status']=='blocked' for e in result['events'][:-1]) else 'committed'
                break
        if result['outcome'] == 'no_report' and any(e['status']=='blocked' for e in result['events']):
            result['outcome'] = 'blocked_no_report'
    report = env['output'] / 'report.json'
    return {'row': row, 'result': result, 'source_sha256': module.digest(env['raw']),
            'source_unchanged': env['path'].read_bytes() == env['raw'],
            'report_file': report.read_text(encoding='utf-8') if report.exists() else None,
            'output_files': sorted(p.name for p in env['output'].iterdir()),
            'trial_wall_seconds': time.monotonic()-started}


def plan(cases, profiles, seeds):
    rows = [{'profile':'none', 'seed':17, 'case':case['id'], 'variant':'deterministic'} for case in cases]
    for profile in profiles:
        for seed_index, seed in enumerate(seeds):
            for index, case in enumerate(cases):
                shift = (index+seed_index) % len(VARIANTS)
                for variant in VARIANTS[shift:] + VARIANTS[:shift]:
                    rows.append({'profile':profile, 'seed':seed, 'case':case['id'], 'variant':variant})
    return rows


def configure():
    # Reuse only the frozen ledger/fsync/hash/replay mechanics, not its study data.
    previous.DATA = DATA
    previous.trial = trial
    previous.plan = plan
    previous.sealed_files = sealed_files


# Keep the original baseline entry point before installing ledger callbacks.
baseline_trial = previous.trial


def sealed_files():
    names = [p.relative_to(ROOT).as_posix() for folder in ('v8unpack_agent','examples','scripts')
             for p in (ROOT/folder).glob('*.py')]
    return sorted(set(names + ['docs/research/fact_references_v2_issue497/cases.json',
        'docs/research/fact_references_v2_issue497/protocol.json',
        'docs/research/agent_benchmark_issue479/system.txt',
        'docs/research/agent_defense_v2_issue494/protocol.json']))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze','run','audit'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    configure()
    if args.command == 'freeze': previous.freeze()
    elif args.output is None: parser.error('--output required')
    elif args.command == 'run': previous.run(args.output,args.endpoint,args.resume)
    else: print('PASS full replay',len(previous.audit(args.output)))
