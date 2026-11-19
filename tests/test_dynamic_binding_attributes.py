"""Closed root attribute arrays; computed/opaque membership remains unknown."""
import pytest

from v8unpack_agent._dynamic_form_attributes import analyze_attribute_arrays
from v8unpack_agent.dynamic_forms import ResolutionStatus as R
from v8unpack_agent.dynamic_forms_detector import _detect, _prepare_dynamic_module

PATH='CommonForm/Demo/Module.bsl'


def analyze(body):
    text='Procedure Build()\n'+body+'\nEndProcedure'
    prepared=_prepare_dynamic_module(text,PATH)
    return analyze_attribute_arrays(prepared,_detect(text,PATH,prepared=prepared))


def test_closed_array_creates_exact_root_attribute_reference_with_evidence():
    result=analyze('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs);')
    record,=result.records
    assert record.reference.resolution == R.EXACT
    assert record.reference.creation_operation_id == record.operation_id
    assert record.reference.name.value == 'Demo'
    assert len(record.evidence) == 2
    assert all(source.relative_path == PATH for source in record.evidence)


def test_description_aliases_and_array_aliases_are_local_closed_values():
    result=analyze('Attrs=New Array(); Other=Attrs; Spec=New FormAttribute("Demo",New TypeDescription("String")); Copy=Spec; Other.Add(Copy); ChangeAttributes(Attrs);')
    assert result.records[0].reference.resolution == R.EXACT


@pytest.mark.parametrize('mutation',['Helper(Attrs);','Attrs.Delete(0);','Attrs[0]=OtherValue;'])
def test_opaque_or_unmodeled_array_mutation_cannot_invent_members(mutation):
    result=analyze('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); '+mutation+' ChangeAttributes(Attrs);')
    assert not result.records
    assert result.unknown_batches


def test_mutated_description_name_is_not_reported_as_original_exact_name():
    result=analyze('Attrs=New Array; Spec=New FormAttribute("Demo",New TypeDescription("String")); Attrs.Add(Spec); Spec.Name=OtherValue; ChangeAttributes(Attrs);')
    assert result.records[0].reference.resolution == R.UNRESOLVED
    assert result.records[0].reference.name.resolution == R.UNRESOLVED


def test_clear_is_new_evidence_of_empty_contents_before_known_addition():
    result=analyze('Attrs=New Array; Attrs.Add(UnknownValue); Attrs.Clear(); Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs);')
    assert result.records[0].reference.resolution == R.EXACT


def test_conditional_membership_outside_branch_does_not_become_exact():
    result=analyze('Attrs=New Array; If DemoFlag Then Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); EndIf; ChangeAttributes(Attrs);')
    assert result.records[0].reference.resolution == R.UNRESOLVED
    assert result.records[0].conditional


def test_batch_inside_proven_member_branch_has_scoped_exact_reference():
    result=analyze('Attrs=New Array; If DemoFlag Then Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs); EndIf;')
    assert result.records[0].reference.resolution == R.EXACT
    assert result.records[0].reference.valid_in


def test_computed_name_nested_path_and_duplicate_name_remain_unresolved():
    for spec in ('New FormAttribute(Prefix,New TypeDescription("String"))',
                 'New FormAttribute("Demo",New TypeDescription("String"),ParentPath)'):
        result=analyze('Attrs=New Array; Attrs.Add('+spec+'); ChangeAttributes(Attrs);')
        assert result.records[0].reference.resolution == R.UNRESOLVED
    result=analyze('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs);')
    assert all(record.reference.resolution == R.UNRESOLVED for record in result.records)


def test_outer_unknown_mutator_taints_after_nested_clear_and_add():
    result=analyze('Attrs=New Array; Attrs.Delete(Helper(Attrs.Clear(),Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))))); ChangeAttributes(Attrs);')
    assert not result.records
    assert result.unknown_batches


def test_indexed_replacement_taints_after_rhs_mutations():
    result=analyze('Attrs=New Array; Attrs[0]=Helper(Attrs.Clear(),Attrs.Add(New FormAttribute("Demo",New TypeDescription("String")))); ChangeAttributes(Attrs);')
    assert not result.records
    assert result.unknown_batches


def test_loop_cannot_keep_outer_array_before_late_clear():
    result=analyze('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); For I=1 To 3 Do ChangeAttributes(Attrs); Attrs.Clear(); EndDo;')
    assert not result.records
    assert result.unknown_batches


def test_loop_fresh_array_can_prove_membership_in_same_iteration():
    result=analyze('For I=1 To 3 Do Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs); EndDo;')
    assert result.records[0].reference.resolution == R.EXACT


def test_loop_cursor_does_not_retain_outer_description_identity():
    result=analyze('Spec=New FormAttribute("Demo",New TypeDescription("String")); For Each Spec In Values Do Attrs=New Array; Attrs.Add(Spec); ChangeAttributes(Attrs); EndDo;')
    assert not result.records or all(record.reference.resolution != R.EXACT for record in result.records)


@pytest.mark.parametrize('escape', [
    'Helper(Attrs[0]);',
    'Alias=Attrs[0]; Alias.Name=OtherValue;',
    'Attrs[0].Name=OtherValue;',
    'Attrs[0].ChangeName();',
    'Alias=Attrs.Get(0); Alias.Name=OtherValue;',
])
def test_unmodeled_mutable_member_escape_taints_closed_array(escape):
    result=analyze('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); '+escape+' ChangeAttributes(Attrs);')
    assert not result.records
    assert result.unknown_batches


def test_unknown_description_method_can_mutate_membership_name():
    result=analyze('Attrs=New Array; Spec=New FormAttribute("Demo",New TypeDescription("String")); Attrs.Add(Spec); Spec.ChangeName(); ChangeAttributes(Attrs);')
    assert result.records[0].reference.resolution == R.UNRESOLVED
