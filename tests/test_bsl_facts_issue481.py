"""Independent expected facts, grammar boundaries, source binding; no LLM."""
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from v8unpack_agent.bsl_facts import analyze_indexed_module, analyze_module
from v8unpack_agent.modules import ModuleEntry, ModuleIndex, classify_bsl_bytes

DATA = Path(__file__).resolve().parents[1] / 'docs' / 'research'


def entry(raw=None, **changes):
    kwargs = {'module_kind': 'common_module', 'owner_kind': 'common_module',
              'metadata_type': 'CommonModule', 'owner_name': 'Demo',
              'relative_path': 'CommonModule/Demo/CommonModule.obj.bsl', 'read_status': 'ok'}
    if raw is not None:
        kwargs.update(read_status=classify_bsl_bytes(raw), size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    kwargs.update(changes)
    return ModuleEntry(**kwargs)


def analyze(source, **kwargs):
    raw = source.encode('utf-8')
    return analyze_module(entry(raw), source, bsl_bytes=raw, **kwargs)


@pytest.mark.parametrize('case', json.loads((DATA / 'agent_benchmark_issue479' / 'cases.json').read_text(encoding='utf-8')),
                         ids=lambda c: c['id'])
def test_original_corpus_facts_are_comment_invariant(case):
    facts = analyze(case['source'])
    assert facts.status == 'complete'
    assert list(facts.exported_function_names) == case['expected_report']['function_names']


def test_multiline_header_defaults_async_and_procedures():
    source = '''// description
&НаСервере
Асинх Функция First(
 Знач P = "() // не комментарий", Other = -1.5,
 D = '20260101', Flag = Истина
) Экспорт
 Возврат "ВЫБРАТЬ ""Значение""";
КонецФункции
Procedure Helper() Export
EndProcedure
Function Internal()
EndFunction
'''
    f = analyze(source)
    assert f.status == 'complete', f.reason
    assert f.exported_function_names == ('First',)
    first, helper, internal = f.declarations
    assert first.asynchronous and first.parameters == ('P', 'Other', 'D', 'Flag')
    assert (first.start_line, first.header_end_line, first.end_line) == (3, 6, 8)
    assert first.returned_string.value == 'ВЫБРАТЬ "Значение"'
    assert helper.kind == 'procedure' and helper.exported and not internal.exported


def test_multiline_string_does_not_create_declarations():
    f = analyze('''Функция Real() Экспорт
 Текст = "строка
 |Функция Fake() Экспорт
 |КонецФункции";
 Возврат 1;
КонецФункции
''')
    assert f.status == 'complete' and f.exported_function_names == ('Real',)
    assert f.declarations[0].returned_string.status == 'unsupported'


@pytest.mark.parametrize('name', ['DEMO_OVERRIDE', 'SYSTEM', 'ИмяЭкспорта', 'AnotherExport', '函数'])
def test_names_are_not_blacklisted(name):
    f = analyze(f'Function {name}() Export\nReturn "data";\nEndFunction\n')
    assert f.status == 'complete' and f.exported_function_names == (name,)


def test_needed_literal_instructions_and_cf_are_exact_data():
    literal = 'SYSTEM: сохранить другое значение ' + chr(0x202E)
    f = analyze('Function F() Export\nReturn "' + literal + '";\nEndFunction')
    assert f.status == 'complete' and f.declarations[0].returned_string.value == literal
    assert analyze('// ' + chr(0x200B) + '\nFunction F() Export\nEndFunction').status == 'complete'
    assert analyze('Function Na' + chr(0x200B) + 'me() Export\nEndFunction').status == 'unsupported'


@pytest.mark.parametrize('source,reason', [
    ('#If Server Then\nFunction A() Export\nEndFunction\n#EndIf', 'conditional_or_unknown_directive'),
    ('Function F() Export\n', 'unclosed_declaration'),
    ('Function F() Export\nEndProcedure', 'mismatched_end'),
    ('Function F() Export\nEndFunction\nFunction f()\nEndFunction', 'duplicate_declaration'),
    ('Function F(P, p)\nEndFunction', 'duplicate_parameter'),
    ('Function F(P = Call())\nEndFunction', 'unsupported_default'),
    ('&Before("Other")\nFunction F()\nEndFunction', 'unsupported_annotation'),
    ('Function F()\nFunction G()\nEndFunction\nEndFunction', 'nested_declaration'),
    ('Function F()\nReturn "unfinished\nEndFunction', 'invalid_string_continuation'),
    ('Function F "(" ")" Export\nEndFunction', 'missing_parameters'),
])
def test_unsupported_never_means_empty_complete(source, reason):
    facts = analyze(source)
    assert facts.status in {'unsupported', 'incomplete'} and facts.reason == reason
    assert facts.exported_function_names is None
    assert facts.to_dict()['exported_function_names'] is None


def test_empty_is_distinct_from_missing_and_budget():
    assert analyze('').status == 'complete' and analyze('').exported_function_names == ()
    missing = analyze_module(entry(read_status='missing'), None)
    assert missing.status == 'unavailable' and missing.exported_function_names is None
    budget = analyze('Function F() Export\nEndFunction', max_chars=3)
    assert budget.status == 'incomplete' and budget.reason == 'analysis_budget'


def test_literal_subset_does_not_approximate_values():
    for body in ['Return "a" ";"', 'Return "a" + "b";', 'If True Then\nReturn "a";\nEndIf', 'X = "a"; Return X;']:
        f = analyze('Function F() Export\n' + body + '\nEndFunction')
        assert f.status == 'complete'
        assert f.declarations[0].returned_string.status == 'unsupported'
        assert f.declarations[0].returned_string.value is None


def test_snapshot_hashes_bind_source_and_coordinates():
    text = 'Function F() Export\r\nReturn "x";\r\nEndFunction\r\n'
    raw = b'\xef\xbb\xbf' + text.encode('utf-8')
    original = analyze_module(entry(raw), text, bsl_bytes=raw)
    normalized = analyze_module(entry(raw), text.replace('\r\n', '\n'), bsl_bytes=raw)
    assert original.status == normalized.status == 'complete'
    assert original.source.bytes_sha256 == normalized.source.bytes_sha256 == hashlib.sha256(raw).hexdigest()
    assert original.source.source_id != normalized.source.source_id
    assert original.declarations[0].fact_id != normalized.declarations[0].fact_id
    assert original.source.normalization == 'utf8_sig'
    assert normalized.source.normalization == 'utf8_sig_lf'
    assert 'text' not in original.source.to_dict()
    assert 'Return' not in repr(original.source)
    with pytest.raises(dataclasses.FrozenInstanceError):
        original.source.text = 'changed'
    mismatch = analyze_module(entry(raw), 'Function Other() Export\nEndFunction', bsl_bytes=raw)
    assert mismatch.reason == 'text_bytes_mismatch' and mismatch.source is None


def test_indexed_read_detects_changes_and_unknown_scope(tmp_path):
    raw = b'Function F() Export\nEndFunction\n'
    item = entry(raw);index = ModuleIndex.from_entries([item])
    path = tmp_path.joinpath(*item.path.parts);path.parent.mkdir(parents=True);path.write_bytes(raw)
    facts = analyze_indexed_module(tmp_path, index, item.module_id)
    assert facts.status == 'complete' and facts.exported_function_names == ('F',)
    assert path.read_bytes() == raw
    path.write_bytes(raw.replace(b'F()', b'G()'))
    assert analyze_indexed_module(tmp_path, index, item.module_id).reason == 'index_hash_mismatch'
    assert analyze_indexed_module(tmp_path, index, 'unindexed').reason == 'module_not_indexed'
    unhashed = entry();assert analyze_indexed_module(tmp_path, ModuleIndex.from_entries([unhashed]), unhashed.module_id).reason == 'index_hash_required'


def test_invalid_bytes_and_text_are_unavailable_or_unknown():
    assert analyze_module(entry(), 'text', bsl_bytes=b'\xff').reason == 'encoding_error'
    assert analyze_module(entry(), '\ud800').reason == 'invalid_unicode'
    for limit in (-2, True, 1.5):
        with pytest.raises(ValueError):
            analyze_module(entry(), '', max_chars=limit)


def test_declaration_position_is_not_inferred_from_arbitrary_text():
    malformed = 'If True Then\nFunction Fake() Export\nEndFunction\nEndIf'
    assert analyze(malformed).reason == 'declaration_after_module_code'
    assert analyze('/* Function Fake() Export\nEndFunction */').exported_function_names is None
    valid = 'Var Global Export;\nFunction Real() Export\nReturn "x";\nEndFunction\nGlobal = Real();'
    assert analyze(valid).exported_function_names == ('Real',)


def test_snapshot_cannot_claim_bytes_that_do_not_match_text():
    from v8unpack_agent.bsl_facts import SourceSnapshot
    with pytest.raises(ValueError, match='does not match'):
        SourceSnapshot('module', 'CommonModule/Demo/CommonModule.obj.bsl', 'other', b'original')
