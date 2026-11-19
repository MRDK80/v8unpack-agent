"""Private synthetic checks for the proposed internal lexical adapter."""
import pytest

from v8unpack_agent.bsl_facts import _Failure, _tokens
from v8unpack_agent.dynamic_forms_detector import _dynamic_tokens as dynamic_tokens


def test_offsets_comments_and_string_literals():
    text = '#If Demo Then\nProcedure Build()\n// Items.Add("Ignore");\nX = "Items.Add(""Ignore"")";\nItems.Add("Demo", Type("FormField"));\nEndProcedure\n#EndIf'
    tokens, directives = dynamic_tokens(text)
    assert [d.word for d in directives] == ['if', 'endif']
    assert [t.value for t in tokens].count('Items') == 1
    for token in tokens:
        if token.kind == 'word':
            assert text[token.start:token.end] == token.value
    with pytest.raises(_Failure):
        _tokens(text)


@pytest.mark.parametrize('text', ['#If Demo Then\n', '#Else\n', '#Region Demo\n', '#Unknown Demo\n', '#If Demo Then\n#Else\n#Else\n#EndIf'])
def test_incomplete_and_unknown_directives_fail_closed(text):
    with pytest.raises(_Failure):
        dynamic_tokens(text)


def test_multiline_literal_keeps_hash_as_data():
    tokens, directives = dynamic_tokens('X = "Demo\n |#If Demo Then\n |Items.Add(""Demo"")";')
    assert not directives
    assert sum(t.kind == 'string' for t in tokens) == 1


def test_guard_expression_preserves_double_slash_in_string():
    _, directives = dynamic_tokens('#If Demo = "x//y" Then // trailing comment\n#EndIf')
    assert directives[0].expression == 'Demo = "x//y"'


def test_missing_then_is_not_a_proven_guard():
    with pytest.raises(_Failure):
        dynamic_tokens('#If Demo\n#EndIf')
