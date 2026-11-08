"""Matched context study: no model for unsupported, exact native effects/replay."""
import pytest

from scripts.minimal_context_issue496 import DATA, plan, read, replay, trial


@pytest.mark.parametrize('variant', ['evidence_guard', 'minimal_guard'])
def test_supported_model_proposal_is_validated_and_replayed(tmp_path, variant):
    case = {'id': 'clean', 'task': 'exports', 'kind': 'clean',
            'source': 'Function Real() Export\nEndFunction\n',
            'expected_report': {'function_names': ['Real']}, 'target_report': None}
    row = {'profile': 'qwen25', 'seed': 17, 'case': 'clean', 'variant': variant}
    def chat(request):
        assert 'task_evidence/1' in request['messages'][0]['content']
        return {'model': 'qwen2.5:7b', 'message': {'role': 'assistant', 'content': '',
                'tool_calls': [{'function': {'name': 'save_report',
                               'arguments': {'function_names': ['Real']}}}]}}
    record = trial(case, row, tmp_path / 'live', chat)
    assert record['result']['outcome'] == 'committed'
    assert record['trial_wall_seconds'] >= 0
    replay(case, row, record, tmp_path / 'replay')


@pytest.mark.parametrize('variant', ['evidence_guard', 'minimal_guard'])
def test_unsupported_has_no_model_request(tmp_path, variant):
    case = {'id': 'unsupported', 'task': 'form_fields', 'kind': 'unsupported',
            'source': {'attributes': []}, 'expected_report': {'attribute_names': []},
            'target_report': None}
    row = {'profile': 'qwen25', 'seed': 17, 'case': 'unsupported', 'variant': variant}
    def forbidden(request):
        pytest.fail('unsupported must not request the model')
    record = trial(case, row, tmp_path / 'live', forbidden)
    assert record['result']['outcome'] == 'unsupported'
    assert not record['result']['model_requested'] and record['report_file'] is None
    replay(case, row, record, tmp_path / 'replay')


def test_full_plan_and_known_development_manifest():
    protocol = read(DATA / 'protocol.json')
    rows = plan(read(DATA / 'cases.json'), protocol['profiles'], protocol['seeds'])
    assert len(rows) == 1440
    assert len({tuple(sorted(row.items())) for row in rows}) == 1440
    assert protocol['full_trial_wall_time']
