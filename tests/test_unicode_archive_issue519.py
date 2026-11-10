"""Full original Unicode/decoder archives; offline integrity, scoring and pinned replay."""
from __future__ import annotations

import importlib.util
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from scripts.unicode_study_issue519 import summarize_phase, validate_boundary

DATA=Path(__file__).resolve().parents[1]/'docs/research/unicode_study_issue519'


def helper():
    spec=importlib.util.spec_from_file_location('archive519',DATA/'replay_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('phase,n',[('unicode',1152),('decode',160)])
def test_full_original_plan_runtime_responses_and_independent_summary(phase,n):
    module=helper();records,runtime,metadata=module.verify_archive(DATA,phase)
    assert len(records)==n and metadata['rows']==n
    assert set(runtime['profiles'])=={'qwen25','llama31','qwen3_off4096','qwen3_on4096'}
    rows=[r.get('protocol_row',r['row'])for r in records]
    assert {r['seed']for r in rows}=={17,29}
    assert Counter(r['profile']for r in rows)=={p:n//4 for p in runtime['profiles']}
    for record in records:
        validate_boundary(record)
        for step in record['result']['trace']:
            assert ('response_json'in step)!=('error'in step)
            body=step['request'];profile=runtime['profiles'][record['row']['profile']]['model']
            assert body['model']==profile['model']and body['stream']is False
            assert body['options']['num_ctx']==8192 and body['options']['temperature']==.3
            assert body['options']['num_predict']==profile['num_predict']
            assert body.get('think')==profile.get('think')
            assert body['messages'][0]['role']=='system'
    cases=json.loads((DATA/('cases.json'if phase=='unicode'else'probe-material.json')).read_text(encoding='utf-8'))
    summary=json.loads((DATA/'results'/phase/'summary.json').read_text(encoding='utf-8'))
    assert summarize_phase(phase,records,cases)==summary
    if phase=='unicode':
        assert sum(r['result']['outcome']=='not_reportable'for r in records)==144
        assert all(not r['result']['model_requested']for r in records if r['result']['outcome']=='not_reportable')
        assert {r['protocol_row']['protocol_arm']for r in records}=={'legacy_guard','framed_guard','minimal_guard'}
        assert sum(len(r['result']['trace'])for r in records)<=3024
    else:
        assert all(len(r['result']['trace'])==1 and r['effects']=={}for r in records)
        assert {r['row']['hint_mode']for r in records}=={'no_hint','explicit_hint'}


@pytest.mark.skipif(sys.platform!='linux'or sys.version_info[:2]!=(3,12)or unicodedata.unidata_version!='15.0.0',
    reason='full actual effects replay needs recorded Linux/Python3.12/Unicode15; integrity and scoring run everywhere')
@pytest.mark.parametrize('phase',['unicode','decode'])
def test_every_original_record_replays_with_exact_pinned_code(phase):
    assert helper().replay(DATA,phase)['rows']==(1152 if phase=='unicode'else 160)
