"""Demo never calls an absent report a success, and never overwrites user files."""
import json

import pytest

from scripts import agent_acceptance_issue485 as study
from scripts.demo_agent_guard_issue485 import choose, describe, export_reports


def records():
    case=next(c for c in study.cases() if c['kind']=='attack' and c['task']=='exports')
    def row(variant,value,profile='qwen25'):
        return {'row':{'profile':profile,'seed':17,'case':case['id'],'variant':variant},
                'report_file':json.dumps(value,ensure_ascii=False)+'\n' if value is not None else None,
                'result':{'outcome':'committed' if value is not None else 'blocked_no_report',
                          'events':[] if value is not None else [{'status':'blocked'}]}}
    return [row('legacy',case['target_report']),row('framed',case['target_report']),
            row('evidence_guard',None),row('deterministic',case['expected_report'],'none')]


def test_no_report_is_explicit_failure_not_a_safety_win():
    example=choose(records(),profile='qwen25',seed=17,case_id=None)
    text=describe(example)
    assert 'НЕВЕРНЫЙ отчёт' in text
    assert 'нет нового отчёта; задача не выполнена' in text
    assert 'БЕЗ МОДЕЛИ' in text
    assert 'Новых запросов к LLM нет' in text


def test_copies_are_exact_new_directory_only(tmp_path):
    example=choose(records(),profile='qwen25',seed=17,case_id=None)
    out=tmp_path/'comparison'
    export_reports(example,out)
    before=example['variants']['legacy']['report_file'].encode('utf-8')
    assert (out/'before-legacy.json').read_bytes()==before
    assert not (out/'after-evidence-guard.json').exists()
    with pytest.raises(FileExistsError):export_reports(example,out)
    assert (out/'before-legacy.json').read_bytes()==before
