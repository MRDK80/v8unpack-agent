import pytest

from v8unpack_agent.bsl_facts import _tokens
from v8unpack_agent.dynamic_forms import ResolutionStatus as R
from v8unpack_agent.dynamic_forms_detector import _Snapshot as Snapshot


@pytest.mark.parametrize(('text','expected'), [('"Demo"','Demo'), ('True', True), ('Ложь',False), ('Undefined',None), ('Null',None), ('-8',-8), ('1.5',1.5), ('"Demo\n |Value"','Demo\nValue')])
def test_scalar_literal(text, expected):
    snapshot = Snapshot(text, 'CommonForm/Demo/Module.bsl')
    value = snapshot.value(tuple(_tokens(text)))
    assert value.resolution == R.EXACT
    assert value.value == expected


@pytest.mark.parametrize('text', ['0.1', 'Prefix + "Demo"', 'Demo.Name', 'Type("FormField")'])
def test_unknown_expression_is_not_evaluated(text):
    snapshot = Snapshot(text, 'CommonForm/Demo/Module.bsl')
    value = snapshot.value(tuple(_tokens(text)))
    assert value.resolution == R.PARTIAL
    assert value.expression == text


def test_type_requires_proven_builtin():
    text = 'Type("FormField")'
    value = Snapshot(text, 'CommonForm/Demo/Module.bsl').value(tuple(_tokens(text)), type_builtin=True)
    assert value.resolution == R.EXACT
    assert value.value == 'FormField'


def test_mixed_newlines_and_unicode_positions():
    snapshot = Snapshot('Demo\r\nДемо\rThird\nEnd', 'CommonForm/Demo/Module.bsl')
    source = snapshot.source(6, 10)
    assert (source.start_line, source.end_line) == (2, 2)
    assert snapshot.text[source.start_offset:source.end_offset] == 'Демо'


def test_large_integer_has_same_bounded_policy_on_supported_python_versions():
    text = '9' * 5000
    value = Snapshot(text, 'CommonForm/Demo/Module.bsl').value(tuple(_tokens(text)))
    assert value.resolution == R.PARTIAL
    assert value.expression == text
