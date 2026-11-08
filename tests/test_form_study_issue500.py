"""R14 scripted preflight/replay/negative controls; never counted as model results."""
from __future__ import annotations

import copy
import gzip
import json
import os
import subprocess
import sys
import unicodedata
import zipfile
from unittest.mock import patch

import pytest

from examples.agent_reference_client import PROFILES, READER_SYSTEM
from scripts import form_study_issue500 as study
from scripts.score_form_study_issue500 import summarize
from v8unpack_agent.form_evidence import analyze_form


def cases():
    return study.read(study.DATA / 'cases.json')


def response(profile, proposal):
    return {'model': PROFILES[profile]['model'], 'message': {'role': 'assistant',
            'content': json.dumps(proposal, ensure_ascii=True)},
            'done': True, 'done_reason': 'stop', 'eval_count': 25, 'prompt_eval_count': 300}


def _check_mock_archive(folder, records):
    """Complete frozen-source packaging test, entirely scripted and temporary."""
    folder.mkdir()
    files = {n: study.digest((study.ROOT / n).read_bytes()) for n in study.selected_files()}
    rows = [r['row'] for r in records]
    seal = {'sha': 'scripted-test-only', 'files': files, 'plan': rows}
    for name in ('cases.json', 'protocol.json', 'system.txt', 'replay_archive.py'):
        (folder / name).write_bytes((study.DATA / name).read_bytes())
    study.write(folder / 'seal.json', seal)
    with zipfile.ZipFile(folder / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in files: archive.writestr(name, (study.ROOT / name).read_bytes())
        archive.writestr('docs/research/form_evidence_issue500/seal.json', (folder / 'seal.json').read_bytes())
    results = folder / 'results'; results.mkdir()
    raw = b''.join((json.dumps(r, ensure_ascii=True, allow_nan=False) + '\n').encode() for r in records)
    (results / 'records.jsonl.gz').write_bytes(gzip.compress(raw, mtime=0))
    study.write(results / 'summary.json', summarize(records, cases()))
    runtime = {'environment': {'python': sys.version.split()[0], 'unidata': unicodedata.unidata_version},
               'profiles': {p: {'ollama': {'version': '0.16.1'}, 'profile': p, 'model': v}
                            for p, v in PROFILES.items()}}
    study.write(results / 'runtime.json', runtime); study.write(results / 'runtime-end.json', runtime)
    study.write(results / 'metadata.json', {'source_head': seal['sha'],
        'raw_records_bytes': len(raw), 'raw_records_sha256': study.digest(raw),
        'source_zip_sha256': study.digest((folder / 'source.zip').read_bytes()),
        'seal_sha256': study.digest((folder / 'seal.json').read_bytes())})
    study.write(results / 'manifest.json', {p.name: study.digest(p.read_bytes()) for p in results.iterdir()})
    env = dict(os.environ, PYTHONPATH=str(study.ROOT))
    completed = subprocess.run([sys.executable, str(folder / 'replay_archive.py')],
                               capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'PASS all 378' in completed.stdout and 'scripted-test-only' in completed.stdout


def test_plan_and_manual_expectations_and_fixed_reader_system():
    selected = cases(); protocol = study.read(study.DATA / 'protocol.json')
    rows = study.plan(selected, protocol['profiles'], protocol['seeds'])
    assert len(rows) == 378 and len({tuple(sorted(r.items())) for r in rows}) == 378
    assert (study.DATA / 'system.txt').read_text(encoding='utf-8') == READER_SYSTEM
    assert protocol['profile_config'] == PROFILES
    for case in selected:
        evidence = analyze_form(study.case_source(case))
        assert evidence.status == case['expected_source_status']
        if evidence.status == 'complete':
            assert [f.name for f in evidence.facts] == case['expected_report']['attribute_names']
            for profile in PROFILES:
                row = {'profile': profile, 'seed': 17, 'case': case['id'], 'variant': 'form_json_guard'}
                body = study.request_for(row, evidence)
                assert body['messages'][0]['content'] == READER_SYSTEM
                assert len(json.dumps(body['messages'], ensure_ascii=False).encode('utf-8')) < 7500
                assert 'tools' not in body and body['format'] == study.REPORT_SCHEMA


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux effect adapter; unsupported tested by core R14 tests')
def test_all378_scripted_trials_full_request_clock_effect_replay(tmp_path):
    selected = cases(); lookup = {c['id']: c for c in selected}
    protocol = study.read(study.DATA / 'protocol.json')
    rows = study.plan(selected, protocol['profiles'], protocol['seeds']); records = []
    requests = 0
    for index, row in enumerate(rows):
        case = lookup[row['case']]
        def chat(body, *, timeout_seconds, row=row, case=case):
            nonlocal requests
            requests += 1
            assert timeout_seconds == 180
            return response(row['profile'], case['expected_report'])
        record = study.trial(case, row, tmp_path / 'run' / str(index), chat)
        study.replay(case, row, record, tmp_path / 'replay' / str(index))
        records.append(record)
    assert requests == 240
    summary = summarize(records, selected)
    assert summary['groups']['none']['correct'] == 12
    for profile in PROFILES:
        group = summary['groups'][profile]
        assert group['correct'] == 60 and group['wrong'] == 0 and group['nonreportable'] == 30
        assert group['false_host_rejects'] == 0 and group['utility_admission'] == 'GO'
    # A correct report with a forged source identity is independently scored wrong.
    forged = copy.deepcopy(records[1]); envelope = json.loads(forged['report_file'])
    envelope['source']['source_id'] = 'forged'; forged['report_file'] = json.dumps(envelope)
    scored = summarize([forged], selected)['groups']['none']
    assert scored['wrong'] == 1 and scored['wrong_scope_commits'] == 1
    if sys.version_info[:2] == (3, 12):
        _check_mock_archive(tmp_path / 'mock-archive', records)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux effect adapter')
@pytest.mark.parametrize('fault,reason', [('wrong', 'report_mismatch'), ('duplicate_json', 'invalid_json'),
    ('native_tool', 'json_tools_forbidden'), ('timeout', 'rpc_timeout'), ('late', 'time_budget'),
    ('too_many_tokens', 'generated_budget'), ('invalid_usage', 'invalid_usage'),
    ('infinite_proposal', 'invalid_report_fields')])
def test_negative_control_preserved_and_replayed(tmp_path, fault, reason):
    case = next(c for c in cases() if c['id'] == 'r14_c_pair')
    row = {'profile': 'qwen25', 'seed': 17, 'case': case['id'], 'variant': 'form_json_guard'}
    def chat(body, *, timeout_seconds):
        if fault == 'timeout': raise TimeoutError('synthetic')
        result = response('qwen25', case['expected_report'])
        if fault == 'wrong': result['message']['content'] = '{"attribute_names":["Invented"]}'
        elif fault == 'duplicate_json': result['message']['content'] = '{"attribute_names":[],"attribute_names":[]}'
        elif fault == 'native_tool': result['message']['tool_calls'] = [{'function': {'name': 'raw_save_report'}}]
        elif fault == 'too_many_tokens': result['eval_count'] = 385
        elif fault == 'invalid_usage': result['prompt_eval_count'] = float('nan')
        elif fault == 'infinite_proposal': result['message']['content'] = '{"x":1e999}'
        return result
    ticks = iter([0, 181] if fault == 'late' else [0, .1, .2])
    record = study.trial(case, row, tmp_path / 'run', chat, clock=lambda: next(ticks))
    assert record['report_file'] is None and record['result']['stop_reason'] == reason
    # Strict outer JSON remains serializable despite invalid provider metadata/proposal.
    serialized = json.dumps(record, ensure_ascii=True, allow_nan=False)
    record = json.loads(serialized)
    study.replay(case, row, record, tmp_path / 'replay')
    assert summarize([record], cases())['groups']['qwen25']['wrong'] == 0


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux effect adapter')
def test_stop_resume_audits_prefix_without_repeating_a_generation(tmp_path):
    selected = cases(); protocol = study.read(study.DATA / 'protocol.json')
    rows = study.plan(selected, protocol['profiles'], protocol['seeds'])
    replies = {}
    for row in rows:
        if row['variant'] == 'deterministic': continue
        case = next(c for c in selected if c['id'] == row['case'])
        evidence = analyze_form(study.case_source(case))
        if evidence.status != 'complete': continue
        key = json.dumps(study.request_for(row, evidence), sort_keys=True)
        replies[key] = response(row['profile'], case['expected_report'])
    output = tmp_path / 'scripted-run'; called = []
    class FakeModel:
        def verify(self, profile):
            return {'ollama': {'version': '0.16.1'}, 'profile': profile, 'model': PROFILES[profile]}
        def chat(self, body, *, timeout_seconds):
            key = json.dumps(body, sort_keys=True)
            assert key not in called
            called.append(key)
            if len(called) == 1: (output / 'PAUSE').touch()
            return copy.deepcopy(replies[key])
    # This tests loop/checkpoint semantics, not a fake claim about real source hashes.
    # Real sealed-byte verification is independently tested by the pinned archive.
    with patch.object(study, 'TimedLocalOllama', return_value=FakeModel()), \
            patch.object(study, 'verify', return_value={'plan': rows}):
        study.run(output, 'unused-scripted-endpoint')
        prefix = study.records_at(output / 'records.jsonl')
        assert len(prefix) == 19 and len(called) == 1
        assert not (output / 'runtime-end.json').exists()
        (output / 'PAUSE').unlink()
        study.run(output, 'unused-scripted-endpoint', resume=True)
        final = study.audit(output)
        assert len(final) == 378 and len(called) == 240
        assert final[:19] == prefix
        assert study.read(output / 'runtime.json') == study.read(output / 'runtime-end.json')
