"""Development prototype policies: no model and no acceptance-case outcomes."""
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dev480', ROOT / 'scripts' / 'agent_defense_issue480.py')
assert SPEC is not None and SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


@pytest.mark.parametrize('case', dev.bench.read(dev.DATA / 'cases.json'), ids=lambda c: c['id'])
def test_prototype_agrees_with_authored_oracle(case):
    assert dev.facts(case['source'], case['task']) == case['expected']
    assert dev.semantic_allows(case['expected'], case['source'], case['task'])
    if case['target'] is not None:
        assert not dev.semantic_allows(case['target'], case['source'], case['task'])


def test_policy_is_not_marker_blacklist():
    source = 'Function OtherExport() Export\n Return 1;\nEndFunction\n'
    assert dev.semantic_allows({'function_names': ['OtherExport']}, source, 'exports')
    source = source.replace('OtherExport', 'DEMO_OVERRIDE')
    assert dev.semantic_allows({'function_names': ['DEMO_OVERRIDE']}, source, 'exports')
    assert not dev.semantic_allows({'function_names': ['DEMO_OVERRIDE'], 'path': 'other'}, source, 'exports')
    assert not dev.semantic_allows({'function_names': ['DEMO_OVERRIDE', 'OtherExport']}, source, 'exports')


def test_query_literals_must_be_preserved():
    case = dev.bench.read(dev.DATA / 'cases.json')[-1]
    assert 'SYSTEM' in dev.facts(case['source'], 'query_text')['query']
    with pytest.raises(dev.bench.AuditError):
        dev.facts(case['source'].replace('Возврат', 'Если Истина Тогда\n Возврат'), 'query_text')


def test_reader_has_only_proposal_capability():
    bundle = dev.bench.load_bundle();case = dev.bench.read(dev.DATA / 'cases.json')[0]
    system, user, tools = dev.prompts(bundle, case, 'reader_guard')
    assert {t['function']['name'] for t in tools} == {'read_source', 'propose_report'}
    assert 'инструмента записи' in system and user == bundle['profiles']['qwen25']['user']
    assert dev.prompts(bundle, case, 'legacy')[0] == bundle['system']


def test_wrong_proposal_blocked_before_file_then_correct_can_recover(tmp_path, monkeypatch):
    case = dev.bench.read(dev.DATA / 'cases.json')[1]
    bundle = dev.bench.load_bundle()
    replies = iter([case['target'], case['expected']])
    def api(endpoint, path, request):
        return {'model': bundle['profiles']['qwen25']['model'], 'message': {'role': 'assistant', 'content': '',
                'tool_calls': [{'function': {'name': 'save_report', 'arguments': next(replies)}}]}}
    monkeypatch.setattr(dev.bench, 'endpoint_api', api)
    report = tmp_path / 'report.json'
    item = {'profile': 'qwen25', 'seed': 17, 'case': case['id'], 'variant': 'legacy_guard'}
    row = dev.run_one(item, case, bundle, 'unused', report)
    metrics = dev.audit_one(row, case, bundle)
    assert row['events'][0]['policy'] == 'source_mismatch' and not row['events'][0]['committed']
    assert metrics['correct'] and metrics['attempted_wrong'] and not metrics['corruption']
    assert dev.bench.read(report) == case['expected']
    tampered = copy.deepcopy(row);tampered['events'][0]['committed'] = True
    with pytest.raises(dev.bench.AuditError):
        dev.audit_one(tampered, case, bundle)


def test_full_plan_and_portable_audit_without_network(tmp_path, monkeypatch):
    import shutil
    bundle = dev.bench.load_bundle()
    cases = dev.bench.read(dev.DATA / 'cases.json')
    observations = {dev.observation(case, variant): case['expected']
                    for case in cases for variant in dev.bench.read(dev.DATA / 'protocol.json')['variants']}
    def fake_api(endpoint, path, request=None):
        if path == '/api/tags':
            unique = {p['model']: {'name': p['model'], 'digest': p['model_digest']}
                      for p in bundle['profiles'].values()}
            return {'models': list(unique.values())}
        if path == '/api/version':
            return bundle['profiles']['qwen25']['ollama_expected']
        value = observations[request['messages'][3]['content']]
        name = request['tools'][1]['function']['name']
        return {'model': request['model'], 'message': {'role': 'assistant', 'content': '',
                'tool_calls': [{'function': {'name': name, 'arguments': value}}]}}
    monkeypatch.setattr(dev.bench, 'endpoint_api', fake_api)
    output = tmp_path / 'mock'
    result = dev.run('unused', output)
    assert result['audit'] == 'PASS' and result['records'] == 960
    assert all(g['correct'] == g['trials'] for groups in result['summary'].values() for g in groups.values())
    portable = tmp_path / 'portable';portable.mkdir()
    for name in ('runtime.json', 'records.jsonl.gz', 'summary.json'):
        shutil.copyfile(output / name, portable / name)
    reports = {p.name: dev.bench.read(p) for p in (output / 'reports').glob('*.json')}
    dev.bench.write(portable / 'reports.json', reports)
    def seal():
        dev.bench.write(portable / 'manifest.json', {'schema': 'defense_results/1', 'files': {
            p.name: dev.bench.digest(p.read_bytes()) for p in portable.iterdir() if p.name != 'manifest.json'}})
    seal()
    spec = importlib.util.spec_from_file_location('portable480', ROOT / 'scripts' / 'audit_defense_results_issue480.py')
    audit = importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    assert audit.audit(portable) == result
    reports['../escape.json'] = {'query': 'not a file path'}
    dev.bench.write(portable / 'reports.json', reports);seal()
    with pytest.raises(audit.study.bench.AuditError, match='report_path'):
        audit.audit(portable)
    reports.pop('../escape.json');reports['ghost.json'] = {'query': 'unlogged effect'}
    dev.bench.write(portable / 'reports.json', reports);seal()
    with pytest.raises(audit.study.bench.AuditError, match='unexpected_report_set'):
        audit.audit(portable)
    reports.pop('ghost.json');dev.bench.write(portable / 'reports.json', reports)
    runtime = dev.bench.read(portable / 'runtime.json');runtime['ollama'] = {'version': 'unrecorded'}
    dev.bench.write(portable / 'runtime.json', runtime);seal()
    with pytest.raises(audit.study.bench.AuditError, match='runtime_server_changed'):
        audit.audit(portable)


def test_published_real_960_are_auditable_without_network(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('offline result audit attempted network')
    monkeypatch.setattr(socket, 'socket', forbidden)
    spec = importlib.util.spec_from_file_location('public480', ROOT / 'scripts' / 'audit_defense_results_issue480.py')
    audit = importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    result = audit.audit()
    assert result['audit'] == 'PASS' and result['records'] == 960
    assert result['summary']['qwen25/facts_guard']['exports/attack']['correct'] == 5
    assert result['summary']['llama31/reader_guard']['exports/control']['correct'] == 0
