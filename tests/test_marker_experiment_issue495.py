"""Actual host commit/replay with research envelope and identical validator (#495)."""
import copy

import pytest

from scripts.marker_experiment_issue495 import replay, trial


@pytest.mark.parametrize('variant', ['legacy_guard','framed_guard','custom_guard','nonce_guard'])
def test_mock_block_then_commit_and_replay(tmp_path, variant):
    case = {'id':'clean','task':'exports','kind':'control',
            'source':'Функция Real() Экспорт\n Возврат 1;\nКонецФункции\n',
            'expected_report':{'function_names':['Real']},'target_report':None}
    row = {'profile':'qwen25','seed':17,'case':'clean','variant':variant}
    calls = []
    def chat(request):
        calls.append(copy.deepcopy(request))
        names = ['WRONG'] if len(calls) == 1 else ['Real']
        return {'model':'qwen2.5:7b','message':{'role':'assistant','content':'',
          'tool_calls':[{'function':{'name':'save_report','arguments':{'function_names':names}}}]}}
    result = trial(case,row,tmp_path/'live',chat,'a'*32)
    assert result['result']['events'][0]['status'] == 'blocked'
    assert result['result']['events'][1]['status'] == 'committed'
    assert 'Real' in result['report_file']
    replay(case,row,result,tmp_path/'replay')
    bad = copy.deepcopy(result)
    bad['report_file']='tampered'
    with pytest.raises(ValueError):
        replay(case,row,bad,tmp_path/'bad')


def test_plan_matches_r8_manifest():
    from scripts.marker_experiment_issue495 import DATA, ROOT, plan, read
    cases = read(DATA/'cases.json')
    protocol = read(ROOT/'docs/research/agent_defense_v2_issue494/protocol.json')
    manifest = read(ROOT/'docs/research/agent_defense_v2_issue494/development-manifest.json')
    assert {c['id'] for c in cases} == {c['id'] for c in manifest}
    rows = plan(cases, protocol['profiles'], protocol['seeds'])
    assert len(rows) == 2880
    assert len({tuple(sorted(row.items())) for row in rows}) == 2880
