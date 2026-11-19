"""Synthetic public boundary and fail-closed budget checks."""
import pytest

from v8unpack_agent.dynamic_forms_detector import (
    DynamicAnalysisError,
    detect_form_operations,
)

PATH = 'CommonForm/Demo/Module.bsl'


def test_empty_success_differs_from_unsupported_or_incomplete():
    assert detect_form_operations('', PATH).operations == ()
    for text in ('#Unknown Demo', 'Procedure Demo()', 'If Demo Then'):
        with pytest.raises(DynamicAnalysisError) as failure:
            detect_form_operations(text, PATH)
        assert failure.value.status in {'unsupported', 'incomplete'}
        assert text not in str(failure.value)


def test_character_budget_and_invalid_unicode_have_source_free_reasons():
    with pytest.raises(DynamicAnalysisError, match='character_budget'):
        detect_form_operations('Demo', PATH, max_chars=1)
    with pytest.raises(DynamicAnalysisError, match='invalid_unicode'):
        detect_form_operations(chr(0xD800), PATH)


def test_nesting_budget_is_explicit_not_a_complete_empty_index():
    text = 'Procedure Demo() Items.Add("Demo", ' + '(' * 65 + 'DemoType' + ')' * 65 + '); EndProcedure'
    with pytest.raises(DynamicAnalysisError, match='nesting_budget'):
        detect_form_operations(text, PATH)


@pytest.mark.parametrize('names', [['Demo'], ('',), (8,)])
def test_inventory_requires_explicit_immutable_names(names):
    with pytest.raises(TypeError):
        detect_form_operations('', PATH, declared_elements=names)


def test_cross_platform_path_validation_precedes_empty_analysis():
    with pytest.raises(ValueError):
        detect_form_operations('', '../Demo.bsl')


def test_operation_and_condition_budgets_raise_without_partial_index(monkeypatch):
    import v8unpack_agent.dynamic_forms_detector as detector

    monkeypatch.setattr(detector, '_MAX_RECORDS', 1)
    one = 'Procedure Demo() Items.Add("Demo", Type("FormField")); EndProcedure'
    assert len(detector.detect_form_operations(one, PATH).operations) == 1
    two = 'Procedure Demo() Items.Add("First", Type("FormField")); Items.Add("Second", Type("FormField")); EndProcedure'
    with pytest.raises(DynamicAnalysisError, match='operation_budget'):
        detector.detect_form_operations(two, PATH)
    with pytest.raises(DynamicAnalysisError, match='condition_budget'):
        detector.detect_form_operations('Procedure Demo() If First Then EndIf; If Second Then EndIf; EndProcedure', PATH)
