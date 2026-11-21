import json
from pathlib import Path

import pytest

from v8unpack_agent.common_form_creation import CommonFormIndex


def put_module(root: Path, name: str, text: str) -> None:
    module = root / 'CommonModule' / name
    module.mkdir(parents=True)
    (module / 'CommonModule.json').write_text(json.dumps({'name': name}), encoding='utf-8')
    (module / 'CommonModule.obj.bsl').write_text(text, encoding='utf-8')


def test_depth_dedup_cycles_and_module_transitions(tmp_path: Path) -> None:
    put_module(tmp_path, 'Entry', '''
Procedure Start(F) Export
    F.Items.Add("Direct", Type("FormField"));
    Holder.Child = Local(F.Items);
    Deep1(F);
EndProcedure
Procedure Local(Elements)
    Elements.Add("Two", Type("FormField"));
    Entry.Start(ThisObject);
EndProcedure
Procedure Deep1(F)
    Deep2(F);
EndProcedure
Procedure Deep2(F)
    Deep1(F);
    Other.Enter(F);
EndProcedure
''')
    put_module(tmp_path, 'Other', '''
Procedure Enter(F) Export
    Entry.Start(F);
    Forward1(F);
EndProcedure
Procedure Forward1(F)
    Forward2(F);
EndProcedure
Procedure Forward2(F)
    Forward3(F);
EndProcedure
Procedure Forward3(F)
    Forward4(F.Items);
EndProcedure
Procedure Forward4(E)
    E.Insert("Eight", Type("FormField"), Parent);
EndProcedure
''')
    form = 'Procedure Build()\nEntry.Start(ThisForm);\nEntry.Start(ThisObject);\nEndProcedure'
    index = CommonFormIndex(tmp_path)
    result = index.analyze(form, 'Form.bsl')
    assert sorted(len(w.calls) for w in result.witnesses) == [1, 2, 8]
    assert {len(w.calls): w.module_hops for w in result.witnesses} == {1: 1, 2: 1, 8: 2}
    assert len(index.analyze(form, 'Form.bsl', max_depth=1).witnesses) == 1
    limited = index.analyze(form, 'Form.bsl', max_depth=7)
    assert len(limited.witnesses) == 2
    assert dict(limited.diagnostics)['depth_limit_reached'] > 0
    assert len(index.analyze(form, 'Form.bsl', max_depth=8).witnesses) == 3
    assert dict(index.analyze(form, 'Form.bsl', max_depth=0).diagnostics)['depth_limit_reached'] == 1
    with pytest.raises(ValueError):
        index.analyze(form, 'Form.bsl', max_depth=-1)


def test_reassignment_shadowing_and_preprocessor_are_explicit(tmp_path: Path) -> None:
    put_module(tmp_path, 'Factory', '''
Procedure Make(F) Export
    DateValue = '2026.11.21 00:00.00';
    DateOnly = '2026-11-21';
    Holder.Procedure = "Example";
#If Server Then
    F.Items.Add("Guarded", Type("FormField"));
#EndIf
    Other(F);
EndProcedure
Procedure Other(F)
    F = New Structure;
    F.Items.Add("Wrong", Type("FormField"));
EndProcedure
''')
    index = CommonFormIndex(tmp_path)
    result = index.analyze('Procedure Build()\nFactory.Make(ThisForm);\nEndProcedure', 'Form.bsl')
    assert len(result.witnesses) == 1
    assert result.witnesses[0].compiler_conditional
    assert dict(result.diagnostics)['unresolved_forwarding_sites'] == 1
    shadowed = '''Procedure Build(Factory, ThisForm)
Factory.Make(ThisForm);
EndProcedure'''
    assert not index.analyze(shadowed, 'Form.bsl').witnesses


def test_runner_cli_projects_source_and_reports_only_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.form_fixture_issue500 import write_fixture
    from v8unpack_agent import cli, runner
    from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
    from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

    meta, elem = write_fixture(tmp_path, ['DataValue'])
    source = meta.with_suffix('.obj.bsl')
    source.write_text('Procedure Build()\nFactory.Make(ThisForm);\nEndProcedure', encoding='utf-8')
    put_module(tmp_path, 'Factory', '''
Procedure Make(F) Export
    Forward(F);
EndProcedure
Procedure Forward(F)
    F.Items.Add("Field", Type("FormField"));
EndProcedure''')
    entry = FormEntry('CommonForm', '', '', 'Synthetic', meta.parent, source, meta,
                      elem_json_path=elem.relative_to(tmp_path))
    monkeypatch.setattr(runner, 'scan_forms', lambda *args, **kwargs: FormScanIndex(forms=[entry]))
    context = build_form_context(entry, tmp_path, common_form_index=CommonFormIndex(tmp_path))
    assert context.common_form_creation is not None
    assert len(context.common_form_creation.witnesses) == 1
    fragment = to_llm_prompt_fragment(context, representation='framed')
    assert 'DYNAMIC_FORM_OPERATIONS' in fragment
    assert '"depth":2' in fragment
    assert 'kind=text' in fragment
    assert build_form_context(entry, tmp_path).common_form_creation is None
    report = tmp_path / 'report.json'
    args = [str(tmp_path), '--report-path', str(report), '--skip-common-modules', '--skip-skd']
    assert cli.main([*args, '--common-form-max-depth', '1']) == cli.EXIT_BAD_INPUT
    code = cli.main([*args, '--common-form-creation', '--common-form-max-depth', '1'])
    assert code in (cli.EXIT_OK, cli.EXIT_DEGRADED)
    limited = json.loads(report.read_text(encoding='utf-8'))['summary']['common_form_creation']
    assert limited == {'forms': 1, 'forms_with_operations': 0, 'operations': 0,
                       'forms_at_depth_limit': 1, 'max_observed_depth': 0}
    code = cli.main([*args, '--common-form-creation'])
    assert code in (cli.EXIT_OK, cli.EXIT_DEGRADED)
    measured = json.loads(report.read_text(encoding='utf-8'))['summary']['common_form_creation']
    assert measured == {'forms': 1, 'forms_with_operations': 1, 'operations': 1,
                        'forms_at_depth_limit': 0, 'max_observed_depth': 2}


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_export_whitespace_and_continuation_keep_source_positions(tmp_path: Path, newline: str) -> None:
    text = newline.join((
        'Procedure Make(F) Export',
        '    Message = "first',
        '',
        '    // Fake quote " and F.Items.Add("Ignore", Type("FormField"));',
        '    |second";',
        '    F.Items.Add("Real", Type("FormField"));',
        'EndProcedure',
    ))
    put_module(tmp_path, 'Factory', text)
    form = newline.join(('Procedure Build()', '\u00a0Factory.Make(ThisForm);', 'EndProcedure'))
    result = CommonFormIndex(tmp_path).analyze(form, 'Form.bsl')
    witness, = result.witnesses
    # Compare against the bytes as read by the index, including source newlines.
    raw = (tmp_path / witness.site.module).read_bytes().decode('utf-8')
    assert witness.site.line == 6 and witness.site.offset == raw.index('Add("Real"')
    assert witness.calls[0].line == 2 and witness.calls[0].offset == form.index('Make')


def test_arbitrary_code_cannot_continue_a_string(tmp_path: Path) -> None:
    form = 'Procedure Build()\nMessage = "first\nFactory.Make(ThisForm);\nEndProcedure'
    result = CommonFormIndex(tmp_path).analyze(form, 'Form.bsl')
    assert not result.witnesses
    assert dict(result.diagnostics)['form_bsl_unsupported'] == 1
