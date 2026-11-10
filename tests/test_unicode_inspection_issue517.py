"""Opt-in scalar inspection preserves legacy Cf and exact source/transport."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import FrozenInstanceError

import pytest

from v8unpack_agent.unicode_inspection import (
    MAX_OCCURRENCES,
    MAX_SCAN_CHARS,
    InspectionReason,
    inspect_unicode,
)
from v8unpack_agent.untrusted_diagnostics import detect_untrusted_chars
from v8unpack_agent.untrusted_transport import decode_block, encode_block


def test_assigned_tags_and_reserved_gaps_are_distinct():
    assigned = [0xE0001, *range(0xE0020, 0xE0080)]
    text = ''.join(map(chr, assigned))
    result = inspect_unicode(text)
    assert result.status == 'complete'
    assert result.counts['tag'] == 97
    assert all(o.category == 'Cf' and o.group == 'tag' for o in result.occurrences)
    assert [o.column for o in result.occurrences] == list(range(1, 98))
    assert detect_untrusted_chars(text).counts['other_cf'] == 97
    assert not inspect_unicode(chr(0xE0000) + chr(0xE0002) + chr(0xE001F)).occurrences


@pytest.mark.parametrize('start,end', [(0xFE00, 0xFE0F), (0xE0100, 0xE01EF)])
def test_both_standard_variation_selector_ranges_are_not_cf(start, end):
    text = ''.join(chr(c) for c in range(start, end + 1))
    result = inspect_unicode(text)
    assert result.counts['variation_selector'] == end - start + 1
    assert all(o.category == 'Mn' for o in result.occurrences)
    assert detect_untrusted_chars(text).total == 0
    assert inspect_unicode(chr(start - 1) + chr(end + 1)).counts['variation_selector'] == 0


def test_mongolian_selectors_cgj_and_uncovered_invisibles_are_explicit():
    text = '\u180b\u180c\u180d\u180f\u034f\u180e\u115f\u1160'
    result = inspect_unicode(text)
    assert result.counts['variation_selector'] == 4
    assert result.counts['combining_grapheme_joiner'] == 1
    assert result.counts['other_cf'] == 1
    assert len(result.occurrences) == 6
    assert all(o.codepoint not in {'U+115F', 'U+1160'} for o in result.occurrences)


def test_bidi_marks_isolates_and_zero_width_match_legacy_counts():
    values = [0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
    text = ''.join(map(chr, values)) + '\u200b\u200c\u200d\u2060\ufeff\u00ad\u2061'
    old = detect_untrusted_chars(text).to_dict()
    result = inspect_unicode(text)
    assert result.counts['bidi'] == len(values)
    assert {k: result.counts[k] for k in old['counts']} == old['counts']
    assert detect_untrusted_chars(text).to_dict() == old


def test_exact_scalar_columns_crlf_and_supplementary_characters():
    result = inspect_unicode('😀\u200b\r\n\U000E0061\ufe0f\n\u034f')
    assert [(o.codepoint, o.line, o.column) for o in result.occurrences] == [
        ('U+200B', 1, 2), ('U+E0061', 2, 1), ('U+FE0F', 2, 2), ('U+034F', 3, 1)]


@pytest.mark.parametrize('raw,expected', [(None, None), (b'abc', False), (b'\xef\xbb\xbfabc', True)])
def test_file_bom_and_inner_feff_are_separate(raw, expected):
    text = '\ufeffx\ufeff'
    result = inspect_unicode(text, raw=raw)
    assert result.file_bom is expected
    assert result.counts['zero_width'] == (1 if expected else 2)
    assert detect_untrusted_chars(text, raw=raw).total == result.counts['zero_width']
    assert inspect_unicode('abc', raw=raw).file_bom is expected


def test_bounded_prefix_is_not_a_complete_safe_result():
    text = 'a\u200b\ufe0f\u202e'
    result = inspect_unicode(text, max_chars=2)
    assert result.status == 'truncated'
    assert result.reasons == (InspectionReason.CHAR_BUDGET,)
    assert result.scanned_chars == 2 and result.unscanned_chars == 2
    assert result.counts['zero_width'] == 1
    assert result.counts['variation_selector'] == result.counts['bidi'] == 0
    assert result.to_dict()['counts_scope'] == 'scanned_prefix_only'


def test_occurrence_retention_does_not_hide_prefix_counts():
    result = inspect_unicode('\u200b\ufe0f\u202e', max_occurrences=1)
    assert result.status == 'truncated'
    assert result.reasons == (InspectionReason.OCCURRENCE_BUDGET,)
    assert len(result.occurrences) == 1
    assert result.observed_count == 3 and result.omitted_occurrences == 2
    assert sum(result.counts.values()) == 3
    assert result.unscanned_chars == 0
    both = inspect_unicode('\ufe0f\u202e', max_chars=1, max_occurrences=0)
    assert both.reasons == (InspectionReason.CHAR_BUDGET, InspectionReason.OCCURRENCE_BUDGET)


def test_zero_budgets_empty_and_plain_are_not_conflated():
    assert inspect_unicode('', max_chars=0, max_occurrences=0).status == 'complete'
    assert inspect_unicode('x', max_chars=0).status == 'truncated'
    assert inspect_unicode('abc', max_occurrences=0).status == 'complete'
    assert inspect_unicode('\ufe0f', max_occurrences=0).status == 'truncated'


def test_invalid_scalar_report_is_bounded_and_never_silently_complete():
    result = inspect_unicode('\ud800\u200b', max_occurrences=0)
    assert result.status == 'invalid'
    assert result.reasons == (InspectionReason.INVALID_SCALAR, InspectionReason.OCCURRENCE_BUDGET)
    assert result.counts['invalid_scalar'] == 1
    assert result.observed_count == 2
    assert json.loads(json.dumps(result.to_dict()))['status'] == 'invalid'
    assert inspect_unicode('a\ud800', max_chars=1).status == 'truncated'


@pytest.mark.parametrize('key,value', [('max_chars', -1), ('max_chars', True),
    ('max_chars', MAX_SCAN_CHARS + 1), ('max_occurrences', -1),
    ('max_occurrences', False), ('max_occurrences', MAX_OCCURRENCES + 1)])
def test_budget_parameters_cannot_expand_the_hard_limits(key, value):
    with pytest.raises(ValueError):
        inspect_unicode('x', **{key: value})


@pytest.mark.parametrize('text,raw', [(b'x', None), ('x', 'x')])
def test_input_types_do_not_trigger_implicit_decoding(text, raw):
    with pytest.raises(TypeError):
        inspect_unicode(text, raw=raw)


def test_legitimate_unicode_snapshot_and_framed_bytes_are_unchanged():
    text = '❤️ 漢\U000E0100 العربية\u200f e\u0301 аa\u034f\n'
    raw = text.encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    before_cf = detect_untrusted_chars(text, raw=raw).to_dict()
    before_block = encode_block(text, n=1, kind='bsl')
    result = inspect_unicode(text, raw=raw)
    assert result.status == 'complete'
    assert result.unidata_version == unicodedata.unidata_version
    assert hashlib.sha256(text.encode('utf-8')).hexdigest() == digest
    assert encode_block(text, n=1, kind='bsl') == before_block
    assert decode_block(before_block).text.encode('utf-8') == raw
    partial = encode_block(text, n=2, kind='text', first_line=4, total_lines=8)
    assert decode_block(partial).truncated and decode_block(partial).text == text
    assert detect_untrusted_chars(text, raw=raw).to_dict() == before_cf
    assert '\ufe0f' in before_block  # Old framed does not newly escape Mn.
    mutated = result.to_dict()
    mutated['counts']['variation_selector'] = 999
    assert result.counts['variation_selector'] == 2
    with pytest.raises(FrozenInstanceError):
        result.status = 'complete'


def test_default_hard_budgets_bound_the_actual_scan_and_retention():
    text = 'a' * MAX_SCAN_CHARS + '\u200b'
    result = inspect_unicode(text)
    assert result.status == 'truncated' and result.unscanned_chars == 1
    assert result.scanned_chars == MAX_SCAN_CHARS and result.observed_count == 0
    repeated = inspect_unicode('\u200b' * (MAX_OCCURRENCES + 1))
    assert repeated.observed_count == MAX_OCCURRENCES + 1
    assert len(repeated.occurrences) == MAX_OCCURRENCES and repeated.omitted_occurrences == 1


def test_bytearray_bom_marker_is_not_modified_or_authenticated():
    raw = bytearray(b'\xef\xbb\xbfabc')
    before = bytes(raw)
    result = inspect_unicode('\ufeff\ufe0f', raw=raw)
    assert result.file_bom is True and result.counts['variation_selector'] == 1
    assert bytes(raw) == before
