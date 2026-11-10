"""Construction gold and fixed stage-4 case/codec plan, before live inference."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.final_fixture_issue502 import prepare
from scripts.final_selection_issue502 import trial
from scripts.score_final_issue502 import expected_origin
from scripts.unicode_cases_issue519 import (
    ATTACK,
    attack_material,
    build_cases,
    decode_control,
    make_plan,
)


def test_registered_counts_and_variant_alias_are_explicit():
    cases = build_cases()
    assert len(cases) == 48 and len({c['id'] for c in cases}) == 48
    assert Counter(c['kind'] for c in cases) == {'clean': 12, 'attack': 30, 'unsupported': 6}
    plan = make_plan('unicode')
    assert len(plan) == 1152 and len({json.dumps(r, sort_keys=True) for r in plan}) == 1152
    assert len(make_plan('decode')) == 160
    assert {r['variant'] for r in plan} == {'legacy_guard', 'framed_guard', 'candidate_guard'}
    assert {r['protocol_arm'] for r in plan} == {'legacy_guard', 'framed_guard', 'minimal_guard'}


def test_registered_codec_controls_restore_manual_gold_without_model():
    for kind in attack_material():
        material = attack_material()[kind]
        assert decode_control(kind, material['encoded']) == ATTACK
        assert material['expected_decoded'] == ATTACK
    for kind in ['tags_no_hint', 'tags_hint', 'binary_no_hint', 'binary_hint', 'variation_selector_hint', 'base64_hint']:
        assert ATTACK not in attack_material()[kind]['source_text']


def test_every_supported_case_matches_independent_origin_and_real_files(tmp_path):
    counts = Counter()
    for i, case in enumerate(build_cases()):
        folder = tmp_path / ('case-' + str(i))
        row = dict(make_plan('unicode')[0], case=case['id'], task=case['task'], variant='deterministic')
        record = trial(case, row, folder, None)
        counts[record['result']['source_status'] == 'complete'] += 1
        if case['expected_report'] is None:
            assert record['result']['source_status'] != 'complete'
            assert record['effects']['producer']['report_file'] is None
            continue
        assert record['result']['source_status'] == 'complete'
        origin = expected_origin(case, row)
        assert json.loads(record['effects']['producer']['report_file']) == origin
        expected = dict(origin, schema='downstream_snapshot_report/1',
                        consumer_request='c-' + origin['producer_request'][2:], target_id='approved')
        assert json.loads(record['effects']['consumer']['report_file']) == expected
        assert record['source_unchanged']
    assert counts == {True: 42, False: 6}


def test_case_sources_and_gold_are_utf8_lf_and_do_not_depend_on_extractor(tmp_path):
    for i, case in enumerate(build_cases()):
        for key in ('bsl_text', 'metadata_text', 'elements_text'):
            value = case.get(key)
            if value is not None:
                assert '\r' not in value and value.encode('utf-8').decode('utf-8') == value
        projection, _status, _reason = prepare(case, tmp_path / str(i))
        assert (projection is not None) == (case['expected_report'] is not None)
        if projection is not None:
            assert projection.report == case['expected_report']
    protocol = json.loads((Path(__file__).resolve().parents[1] /
        'docs/research/unicode_image_protocol_issue516/protocol.json').read_text(encoding='utf-8'))
    assert len(make_plan('unicode')) == protocol['unicode']['planned_trials']
