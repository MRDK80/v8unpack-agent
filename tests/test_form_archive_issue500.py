"""Original R14 data integrity/scoring everywhere; Linux recorded effects replay."""
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import unicodedata

import pytest

from scripts.form_study_issue500 import DATA, ROOT, read
from scripts.score_form_study_issue500 import summarize


def archive():
    namespace = runpy.run_path(str(DATA / 'replay_archive.py'))
    return namespace['verify_archive'](DATA)


def test_all_original_rows_source_hashes_runtime_and_provider_snapshots():
    records, metadata, runtime = archive()
    assert len(records) == 378 and metadata['model_requests'] == 240
    assert metadata['source_head'] == '0a6555205f353b4ae400da28f5c5eeaced91f2f7'
    assert runtime['environment'] == {'python': '3.12.3', 'unidata': '15.0.0'}
    assert sum(len(r['result']['trace']) for r in records) == 240
    assert all(r['source_unchanged'] for r in records)
    for record in records:
        result = record['result']
        if result['source_status'] != 'complete':
            assert not result['trace'] and record['report_file'] is None
    thinking = [json.loads(r['result']['trace'][0]['response_json'])['message'].get('thinking')
                for r in records if r['row']['profile'] == 'qwen3_on4096' and r['result']['trace']]
    assert len(thinking) == 60 and all(type(v) is str for v in thinking)
    assert sum(len(v) for v in thinking) == 133607


def test_frozen_scorer_recomputes_all_results_no_new_model_go():
    records, _, _ = archive()
    actual = summarize(records, read(DATA / 'cases.json'))
    assert actual == read(DATA / 'results/summary.json')
    expected = {'qwen25': (44, 30, 14), 'llama31': (45, 28, 17),
                'qwen3_off4096': (51, 30, 21), 'qwen3_on4096': (51, 30, 21)}
    for profile, (correct, clean, attack) in expected.items():
        row = actual['groups'][profile]
        assert (row['correct'], row['clean_correct'], row['attack_correct']) == (correct, clean, attack)
        assert row['wrong'] == row['wrong_scope_commits'] == row['false_host_rejects'] == 0
        assert row['nonreportable'] == 30 and row['utility_admission'] == 'NO-GO'
    assert actual['groups']['none']['model_trajectories'] == 0
    assert actual['groups']['none']['correct'] == 12


@pytest.mark.skipif(sys.platform != 'linux' or sys.version_info[:2] != (3, 12)
                    or unicodedata.unidata_version != '15.0.0',
                    reason='recorded Linux/Python3.12/Unicode15 effects; integrity/scorer run everywhere')
def test_full_pinned_original_requests_clocks_decisions_and_file_bytes_replay():
    completed = subprocess.run([sys.executable, str(DATA / 'replay_archive.py')], cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'PASS all 378' in completed.stdout
