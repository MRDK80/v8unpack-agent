import pytest

from v8unpack_agent.bsl_facts import _Failure, _tokens
from v8unpack_agent.dynamic_forms_detector import _arguments as arguments
from v8unpack_agent.dynamic_forms_detector import _dynamic_tokens as dynamic_tokens
from v8unpack_agent.dynamic_forms_detector import _matching as matching
from v8unpack_agent.dynamic_forms_detector import _procedures as procedures


def test_parameters_and_whole_body_shadow_prepass():
    tokens, _ = dynamic_tokens('Var Demo; Procedure Build(Items, Val Commands = Undefined)\nX=1; Var ThisObject; For Each DemoItem In DemoList Do X=2; EndDo; EndProcedure')
    procs, module_vars, names = procedures(tokens)
    assert module_vars == {'demo'}
    assert names == {'build'}
    assert procs[0].shadowed == {'items', 'commands', 'thisobject', 'x', 'demoitem'}


def test_complete_arguments_keep_omission_and_nested_call():
    tokens = _tokens('Add("Demo", Type("Field"),, Items["Next"])')
    args, end = arguments(tokens, 1)
    assert len(args) == 4
    assert args[2] == ()
    assert end == len(tokens) - 1
    assert args[1][0].value == 'Type'


@pytest.mark.parametrize('text', ['Procedure Demo() X=1;', 'EndProcedure', 'Procedure Demo() Function Other() EndFunction EndProcedure', 'Procedure Demo() EndFunction', 'Procedure Demo() EndProcedure Procedure DEMO() EndProcedure'])
def test_malformed_structure_fails_closed(text):
    with pytest.raises(_Failure):
        procedures(_tokens(text))


@pytest.mark.parametrize('text', ['(X]', '(X', '([X)'])
def test_invalid_delimiters(text):
    with pytest.raises(_Failure):
        matching(_tokens(text), 0)


def test_exported_module_variables_are_still_shadowing_bindings():
    procs, bindings, _ = procedures(_tokens('Var Items Export, Commands Export; Procedure Demo() EndProcedure'))
    assert bindings == {'items', 'commands'}
    assert len(procs) == 1


def test_module_initialization_is_not_a_complete_empty_analysis():
    with pytest.raises(_Failure, match='module_initialization_code'):
        procedures(_tokens('Items.Add("Demo", Type("FormField"));'))
