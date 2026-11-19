import pytest

from v8unpack_agent.bsl_facts import _Failure
from v8unpack_agent.dynamic_forms import BranchKind, ConditionKind
from v8unpack_agent.dynamic_forms_detector import _branch_contexts as branch_contexts
from v8unpack_agent.dynamic_forms_detector import _dynamic_tokens as dynamic_tokens
from v8unpack_agent.dynamic_forms_detector import _Snapshot as Snapshot


def parse(text):
    tokens, directives = dynamic_tokens(text)
    contexts, conditions = branch_contexts(tokens, directives, Snapshot(text, 'CommonForm/Demo/Module.bsl'))
    return tokens, contexts, conditions


def test_elseif_retains_negative_guards_without_guessing_truth():
    text = 'If DemoFlag Then A=1; ElsIf OtherFlag Then B=2; Else C=3; EndIf; D=4;'
    tokens, contexts, conditions = parse(text)
    by_name = {t.value: contexts[t.start][0] for t in tokens if t.value in {'A','B','C','D'}}
    assert len(conditions) == 2
    assert [c.expression for c in conditions] == ['DemoFlag', 'OtherFlag']
    assert [r.branch for r in by_name['A']] == [BranchKind.THEN]
    assert [r.branch for r in by_name['B']] == [BranchKind.ELSE, BranchKind.THEN]
    assert [r.branch for r in by_name['C']] == [BranchKind.ELSE, BranchKind.ELSE]
    assert by_name['D'] == ()
    assert all(c.runtime_truth == 'unknown' for c in conditions)


def test_preprocessor_loop_and_runtime_guards_combine():
    text = '#If Server Then\nFor Each Demo In DemoList Do\nIf DemoFlag Then X=1; EndIf; EndDo;\n#EndIf'
    tokens, contexts, conditions = parse(text)
    target = next(t for t in tokens if t.value == 'X')
    assert [c.kind for c in conditions] == [ConditionKind.PREPROCESSOR, ConditionKind.LOOP, ConditionKind.IF]
    assert [r.branch for r in contexts[target.start][0]] == [BranchKind.THEN, BranchKind.BODY, BranchKind.THEN]


@pytest.mark.parametrize('text', ['If Demo Then X=1;', 'Else X=1;', 'EndDo;', 'If Demo X=1;', 'For Each X In Demo Do If Flag Then EndDo; EndIf;'])
def test_malformed_guards_fail_closed(text):
    with pytest.raises(_Failure):
        parse(text)


def test_unsupported_control_marks_context_incomplete():
    tokens, contexts, _ = parse('Try X=1; Except X=2; EndTry;')
    assert all(not contexts[t.start][1] for t in tokens if t.value == 'X')
