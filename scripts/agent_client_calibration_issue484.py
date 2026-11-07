"""Freeze/run/offline-replay clean calibration of the fixed reference client."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from examples.agent_reference_client import LocalOllama, run_reference
from v8unpack_agent.action_validation import create_report_contract
from v8unpack_agent.modules import ModuleEntry, ModuleIndex, classify_bsl_bytes

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/research/agent_client_issue484'
SEALED = sorted(['examples/agent_reference_client.py', 'examples/validated_report_executor.py',
          'examples/__init__.py', 'scripts/agent_client_calibration_issue484.py',
          'docs/research/agent_client_issue484/cases.json',
          'docs/research/agent_client_issue484/protocol.json'] +
          [p.relative_to(ROOT).as_posix() for p in (ROOT / 'v8unpack_agent').glob('*.py')])


def environment() -> dict[str, str]:
    return {'python': sys.version.split()[0], 'unidata_version': unicodedata.unidata_version}



def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, value: Any) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require(condition: Any, reason: str) -> None:
    if not condition:raise ValueError(reason)


def plan() -> list[dict[str, Any]]:
    protocol, cases = read(DATA / 'protocol.json'), read(DATA / 'cases.json')
    rows = [{'mode': 'deterministic', 'profile': 'qwen25', 'seed': 17, 'case': c['id']} for c in cases]
    for profile in protocol['profiles']:
        for seed in protocol['seeds']:
            for index, case in enumerate(cases):
                modes = protocol['modes'] if (seed + index) % 2 else list(reversed(protocol['modes']))
                for mode in modes:
                    rows.append({'mode': mode, 'profile': profile, 'seed': seed, 'case': case['id']})
    require(len(rows) == protocol['rows'], 'plan_count')
    return rows


def freeze() -> None:
    require(not (DATA / 'seal.json').exists(), 'seal_already_exists')
    seal = {'schema': 'client_calibration_seal/1', 'environment': environment(),
            'implementation_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'files': {p: digest((ROOT / p).read_bytes()) for p in SEALED}, 'plan': plan()}
    write(DATA / 'seal.json', seal)
    print('Frozen seal SHA256:', digest((DATA / 'seal.json').read_bytes()))


def verify() -> dict[str, Any]:
    seal = read(DATA / 'seal.json')
    require(set(seal['files']) == set(SEALED), 'seal_file_set')
    for path, sha in seal['files'].items():
        require(digest((ROOT / path).read_bytes()) == sha, 'sealed_file_changed:' + path)
    require(seal['plan'] == plan(), 'sealed_plan_changed')
    require(subprocess.run(['git', 'merge-base', '--is-ancestor', seal['implementation_sha'], 'HEAD'], cwd=ROOT, check=False).returncode == 0,
            'implementation_not_ancestor')
    return seal


def trial(case: dict[str, Any], row: dict[str, Any], folder: Path, chat: Any) -> dict[str, Any]:
    source_root, output = folder / 'input', folder / 'output'
    source_root.mkdir(parents=True);output.mkdir()
    raw = case['source'].encode('utf-8')
    entry = ModuleEntry('object', 'metadata_object', 'Catalog', 'Demo',
        'Catalog/Demo/Catalog.obj.bsl', classify_bsl_bytes(raw), len(raw), digest(raw))
    path = source_root.joinpath(*entry.path.parts);path.parent.mkdir(parents=True);path.write_bytes(raw)
    contract = create_report_contract(source_root, ModuleIndex.from_entries([entry]), entry.module_id,
        operation=case['operation'], declaration_name='ТекстЗапроса' if case['operation'] == 'returned_string' else None)
    result = run_reference(contract, root=source_root, output_dir=output,
        mode=row['mode'], profile=row['profile'], seed=row['seed'], chat=chat, calibration=True)
    file = output / 'report.json'
    return {'row': row, 'result': result, 'source_unchanged': path.read_bytes() == raw,
            'report_file': file.read_text(encoding='utf-8') if file.exists() else None,
            'output_files': sorted(p.name for p in output.iterdir())}


def load_records(output: Path) -> list[dict[str, Any]]:
    data = output / 'records.jsonl'
    if data.exists():raw = data.read_text(encoding='utf-8')
    else:raw = gzip.decompress((output / 'records.jsonl.gz').read_bytes()).decode('utf-8')
    return [json.loads(line) for line in raw.splitlines()]


def run(output: Path, endpoint: str, resume: bool) -> None:
    seal = verify()
    client = LocalOllama(endpoint)
    require(environment() == seal['environment'], 'run_environment_changed')
    runtime = {'environment': environment(),
               'profiles': {p: client.verify(p) for p in read(DATA / 'protocol.json')['profiles']}}
    if resume:
        records = load_records(output)
        require([r['row'] for r in records] == seal['plan'][:len(records)], 'resume_prefix')
        require(read(output / 'runtime.json') == runtime, 'resume_runtime_changed')
        audit(output, complete=False)
    else:
        require(not output.exists(), 'output_exists')
        output.mkdir(parents=True)
        write(output / 'runtime.json', runtime);records = []
    cases = {c['id']: c for c in read(DATA / 'cases.json')}
    with (output / 'records.jsonl').open('a', encoding='utf-8', newline='\n') as writer:
        for index, row in enumerate(seal['plan'][len(records):], start=len(records)):
            if (output / 'PAUSE').exists():
                print('Paused before next trial', index, flush=True);return
            record = trial(cases[row['case']], row, output / 'artifacts' / f'{index:04d}', client.chat)
            writer.write(json.dumps(record, ensure_ascii=False) + '\n');writer.flush();os.fsync(writer.fileno())
            print(index + 1, '/', len(seal['plan']), row, record['result']['outcome'], flush=True)


def normalized(record: dict[str, Any], unicode_version: str) -> dict[str, Any]:
    record = copy.deepcopy(record)
    for step in record['result']['trace']:
        step.pop('wall_seconds', None)
        for message in step['request']['messages']:
            if message.get('role') == 'tool' and message.get('tool_name') == 'read_source':
                try:payload = json.loads(message.get('content', ''))
                except (ValueError, TypeError):continue
                if type(payload) is dict and payload.get('schema') == 'evidence_context/1':
                    for module in payload['modules']:
                        if module['diagnostics'] is not None:
                            require(module['diagnostics']['unidata_version'] == unicode_version,
                                    'diagnostic_runtime_mismatch')
                            module['diagnostics']['unidata_version'] = '<recorded-runtime>'
                    message['content'] = json.dumps(payload, ensure_ascii=False)

    return record


def parse_report(content: str) -> Any:
    def unique(pairs: Any) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in value, 'duplicate_json_key');value[key] = item
        return value
    return json.loads(content, object_pairs_hook=unique)


def equal_report(actual: Any, expected: dict[str, Any]) -> bool:
    if type(actual) is not dict or set(actual) != set(expected):return False
    if 'query' in expected:return type(actual['query']) is str and actual['query'] == expected['query']
    names = actual['function_names']
    return (type(names) is list and all(type(n) is str for n in names) and
            len(names) == len(set(names)) and set(names) == set(expected['function_names']))


def audit(output: Path, *, complete: bool = True) -> dict[str, Any]:
    seal = verify();records = load_records(output)
    expected_plan = seal['plan'] if complete else seal['plan'][:len(records)]
    require([r['row'] for r in records] == expected_plan, 'incomplete_or_changed_plan')
    cases = {c['id']: c for c in read(DATA / 'cases.json')}
    # Offline: shape/pinned runtime only, never call client's network functions.
    runtime = read(output / 'runtime.json')
    from examples.agent_reference_client import PROFILES, SERVER_VERSION
    require(runtime['environment'] == seal['environment'], 'recorded_environment_changed')
    require(set(runtime['profiles']) == set(read(DATA / 'protocol.json')['profiles']), 'runtime_profiles')
    for p, value in runtime['profiles'].items():
        require(value == {'ollama': {'version': SERVER_VERSION}, 'profile': p, 'model': PROFILES[p]}, 'runtime_changed')
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {'total': 0, 'correct': 0, 'unsupported': 0,
        'no_report': 0, 'errors': 0, 'limits': 0, 'wrong_commits': 0, 'request_wall_seconds': 0.0})
    for record in records:
        row, result = record['row'], record['result'];case = cases[row['case']]
        steps = iter(result['trace']);consumed = 0
        def replay(body: dict[str, Any], steps: Any = steps) -> dict[str, Any]:
            nonlocal consumed
            step = next(steps);consumed += 1
            require(body == step['request'], 'request_history_changed')
            if 'error' in step:raise OSError('recorded failure')
            return copy.deepcopy(step['response'])
        with tempfile.TemporaryDirectory() as folder:
            reproduced = trial(case, row, Path(folder), replay)
        require(consumed == len(result['trace']), 'unconsumed_responses')
        require(normalized(record, seal['environment']['unidata_version']) ==
                normalized(reproduced, unicodedata.unidata_version), 'event_or_file_replay_mismatch')
        require(record['source_unchanged'], 'source_modified')
        require(record['output_files'] in ([], ['report.json']), 'unexpected_artifacts')
        key = row['mode'] + ':' + ('none' if row['mode'] == 'deterministic' else row['profile']) + ':' + case['operation']
        g = groups[key];g['total'] += 1
        actual = None
        content = result['reader_output']['content'] if row['mode'] == 'reader' and result['reader_output'] else record['report_file']
        if content is not None:
            try:actual = parse_report(content)
            except (ValueError, TypeError):pass
        correct = equal_report(actual, case['expected'])
        g['correct'] += int(correct)
        g['unsupported'] += int(result['outcome'] == 'unsupported')
        g['no_report'] += int(not correct)
        g['errors'] += int(result['error'] is not None and result['outcome'] != 'unsupported')
        g['limits'] += int(result['limited'])
        g['wrong_commits'] += int(record['report_file'] is not None and not correct)
        g['request_wall_seconds'] += sum(s.get('wall_seconds', 0) for s in result['trace'])
    threshold = read(DATA / 'protocol.json')['thresholds']['native_profile_task_correct_min']
    for key, g in groups.items():
        g['meets_calibration_metrics'] = (g['correct'] / g['total'] >= threshold and
            g['unsupported'] == g['no_report'] == g['errors'] == g['limits'] == g['wrong_commits'] == 0)
        # Reader is not admitted as a writer, even if its answer is accurate.
        g['native_admission'] = complete and key.startswith('native:') and g['meets_calibration_metrics']
    summary = {'rows': len(records), 'groups': dict(groups),
        'seal_sha256': digest((DATA / 'seal.json').read_bytes()),
        'claim': 'Clean compatibility only; no attack resistance or default latency promotion. Reader never writes; deterministic never calls model.'}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'run', 'audit'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.command == 'freeze':freeze()
    else:
        require(args.output is not None, 'output_required')
        if args.command == 'run':run(args.output, args.endpoint, args.resume)
        else:
            summary = audit(args.output);write(args.output / 'summary.json', summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':main()
