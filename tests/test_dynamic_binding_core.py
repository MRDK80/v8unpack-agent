"""Synthetic forward-local identity contracts for #215."""
import pytest

from v8unpack_agent.dynamic_forms import OperationKind, ResolutionStatus
from v8unpack_agent.dynamic_forms_binding import bind_form_operations

R=ResolutionStatus
PATH='CommonForm/Demo/Module.bsl'


def run(body, **kwargs):
    return bind_form_operations('Procedure Build()\n'+body+'\nEndProcedure',PATH,**kwargs)


def test_computed_name_keeps_exact_reference_but_partial_chain():
    result=run('X=Items.Add(Prefix,Type("FormField")); X.Width=8;')
    assert [op.operation_resolution for op in result.raw.operations] == [R.PARTIAL,R.UNRESOLVED]
    assert [op.operation_resolution for op in result.bound.operations] == [R.PARTIAL,R.EXACT]
    assert result.bound.operations[1].target.name.resolution == R.PARTIAL
    assert result.bound.operations[1].target.creation_operation_id == result.bound.operations[0].operation_id
    assert result.candidates[0].resolution == R.PARTIAL


def test_collection_alias_creation_and_result_alias_are_proven_in_separate_passes():
    result=run('Collection=Items; X=Collection.Add(Prefix,Type("FormField")); X.Width=8;')
    assert len(result.raw.operations) == 1
    assert [op.operation_kind for op in result.bound.operations] == [OperationKind.ELEMENT_ADD,OperationKind.PROPERTY_SET]
    assert result.bound.operations[1].operation_resolution == R.EXACT


def test_known_parent_identity_is_exact_even_with_computed_parent_name():
    result=run('Group=Items.Add(Prefix,Type("FormGroup")); X=Items.Add("Demo",Type("FormField"),Group);')
    parent=result.bound.operations[1].arguments[2].value
    assert parent.resolution == R.EXACT
    assert parent.creation_operation_id == result.bound.operations[0].operation_id


@pytest.mark.parametrize('lookup',['Items.Find("Demo")','Items["Demo"]'])
def test_delete_can_use_proven_literal_lookup(lookup):
    result=run('Items.Add("Demo",Type("FormField")); Items.Delete('+lookup+');')
    assert result.bound.operations[1].operation_resolution == R.EXACT


def test_branch_reference_does_not_escape_its_creation_context():
    result=run('If DemoFlag Then X=Items.Add(Prefix,Type("FormField")); X.Width=8; EndIf; X.Height=8;')
    assert result.bound.operations[1].operation_resolution == R.EXACT
    assert result.bound.operations[1].target.valid_in == result.bound.operations[1].conditions
    assert result.bound.operations[2].operation_resolution == R.UNRESOLVED


def test_opaque_call_and_delete_do_not_keep_later_alias_exact():
    for body in ('X=Items.Add("Demo",Type("FormField")); Helper(X); X.Width=8;',
                 'X=Items.Add("Demo",Type("FormField")); Y=X; Items.Delete(X); Y.Width=8;'):
        assert run(body).bound.operations[-1].operation_resolution == R.UNRESOLVED


def test_parameters_and_module_globals_are_not_closed_local_variables():
    text='Var Global; Procedure Build(X,Y) X=Items.Add("Demo",Type("FormField")); Y=OtherValue; X.Width=8; Global=Items.Add("Other",Type("FormField")); Global.Width=8; EndProcedure'
    result=bind_form_operations(text,PATH)
    assert [op.operation_resolution for op in result.bound.operations if op.operation_kind == OperationKind.PROPERTY_SET] == [R.UNRESOLVED,R.UNRESOLVED]


def test_nested_evaluation_does_not_create_forbidden_forward_reference():
    result=run('Items.Add("Demo",Type("FormField"),Items.Add("Group",Type("FormGroup")));')
    assert len(result.bound.operations) == 2
    assert result.bound.operations[0].arguments[2].value.resolution != R.EXACT


@pytest.mark.parametrize('lookup',['Items.Find("Demo")','Items["Demo"]'])
def test_complex_setter_uses_known_name_without_changing_raw_index(lookup):
    result=run('Items.Add("Demo",Type("FormField")); '+lookup+'.Width=8; '+lookup+'.Height=9;')
    assert len(result.raw.operations) == 1
    assert len(result.bound.operations) == 3
    assert all(op.operation_resolution == R.EXACT for op in result.bound.operations)
    assert len(result.candidates) == 1


def test_chained_creation_setter_has_same_site_reference():
    result=run('Items.Add(Prefix,Type("FormField")).Width=8;')
    create,setter=result.bound.operations
    assert setter.target.creation_operation_id == create.operation_id
    assert setter.target.name.resolution == R.PARTIAL
    assert setter.operation_resolution == R.EXACT


@pytest.mark.parametrize('body',[
    'If Items.Find("Demo").Visible = True Then X=1; EndIf;',
    'Return Items["Demo"].Visible = True;',
    'Check(Items.Find("Demo").Visible = True);',
])
def test_complex_comparisons_are_not_setters(body):
    assert not run(body,declared_elements=('Demo',)).bound.operations


@pytest.mark.parametrize('lookup',['Items.Find(DemoName)','Items[DemoName]'])
def test_computed_lookup_name_is_preserved_without_guessing(lookup):
    result=run(lookup+'.Width=8;')
    op,=result.bound.operations
    assert op.operation_resolution == R.UNRESOLVED
    assert op.target.name.resolution == R.PARTIAL
    assert op.target.name.expression == 'DemoName'


def test_declared_lookup_after_delete_is_unresolved_and_raw_stays_exact():
    result=run('Items.Delete(Items.Demo); Items.Demo.Width=8;',declared_elements=('Demo',))
    assert result.raw.operations[-1].operation_resolution == R.EXACT
    assert result.bound.operations[-1].operation_resolution == R.UNRESOLVED
    assert len(result.candidates) == 1


def test_dynamic_name_collision_with_declared_name_never_chooses_new_site():
    result=run('Items.Add("Demo",Type("FormField")); Items.Demo.Width=8;',declared_elements=('Demo',))
    assert result.bound.operations[-1].operation_resolution == R.UNRESOLVED


def test_root_form_properties_require_base_contract_not_only_a_whitelisted_word():
    result=run('ThisObject.Title="Demo"; ThisObject.ReadOnly=True; ThisObject.CurrentRow=8;')
    assert [op.operation_resolution for op in result.bound.operations] == [R.EXACT,R.EXACT,R.UNRESOLVED]


def test_form_attributes_can_disqualify_native_context_name_assumptions():
    result=run('Items.Add("Demo",Type("FormField")); ThisObject.Items.Add("Other",Type("FormField")); ThisObject.Title="Demo";',declared_attributes=('Items','Title'))
    assert all(op.operation_resolution == R.UNRESOLVED for op in result.bound.operations)


@pytest.mark.parametrize('loop', [
    'For I=1 To 3 Do X.Width=8; X=Items.Add("Second",Type("FormField")); EndDo;',
    'For Each X In Values Do X.Width=8; EndDo;',
])
def test_loop_backedge_and_cursor_do_not_retain_outer_exact_binding(loop):
    result=run('X=Items.Add("First",Type("FormField")); '+loop)
    setter=next(op for op in result.bound.operations if op.operation_kind == OperationKind.PROPERTY_SET)
    assert setter.operation_resolution == R.UNRESOLVED


def test_fresh_creation_inside_loop_restores_exact_reference():
    result=run('For I=1 To 3 Do X=Items.Add(Prefix,Type("FormField")); X.Width=8; EndDo;')
    assert result.bound.operations[-1].operation_resolution == R.EXACT
    assert result.bound.operations[-1].target.valid_in == result.bound.operations[-1].conditions


def test_loop_early_lookup_cannot_ignore_late_delete_and_recreate():
    result=run('Items.Add("Demo",Type("FormField")); For I=1 To 3 Do Items["Demo"].Width=8; Items.Delete(Items["Demo"]); Items.Add("Demo",Type("FormField")); EndDo;')
    setter=next(op for op in result.bound.operations if op.operation_kind == OperationKind.PROPERTY_SET)
    assert setter.operation_resolution == R.UNRESOLVED


@pytest.mark.parametrize('control', ['Execute Text;', 'Goto ~Again;'])
def test_unsupported_dynamic_control_rejects_whole_module(control):
    from v8unpack_agent.dynamic_forms_detector import DynamicAnalysisError
    with pytest.raises(DynamicAnalysisError, match='dynamic_control_flow'):
        run('X=Items.Add("Demo",Type("FormField")); X.Width=8; '+control)


def test_long_elseif_chain_has_bounded_condition_context():
    from v8unpack_agent.dynamic_forms_detector import DynamicAnalysisError
    text='If Flag Then '+''.join('ElsIf Flag Then ' for _ in range(65))+'X=Items.Add("Demo",Type("FormField")); EndIf;'
    with pytest.raises(DynamicAnalysisError, match='condition_context_budget'):
        run(text)


def test_opaque_helper_cannot_preserve_unpassed_alias_or_declared_name_liveness():
    result=run('X=Items.Add("Demo",Type("FormField")); Y=X; Helper(); Y.Width=8; Items.Known.Height=9;',declared_elements=('Known',))
    setters=[op for op in result.bound.operations if op.operation_kind == OperationKind.PROPERTY_SET]
    assert len(setters) == 2
    assert all(op.operation_resolution == R.UNRESOLVED for op in setters)


def test_mutually_exclusive_literal_creations_do_not_collide_inside_their_branch():
    result=run('If Flag Then Items.Add("Demo",Type("FormField")); Items["Demo"].Width=8; Else Items.Add("Demo",Type("FormField")); Items["Demo"].Height=9; EndIf;')
    setters=[op for op in result.bound.operations if op.operation_kind == OperationKind.PROPERTY_SET]
    assert len(setters) == 2
    assert all(op.operation_resolution == R.EXACT for op in setters)
    assert setters[0].target.creation_operation_id != setters[1].target.creation_operation_id


def test_preprocessor_elseif_only_chain_is_bounded_even_without_body_tokens():
    from v8unpack_agent.dynamic_forms_detector import DynamicAnalysisError
    text='#If Flag Then\n'+''.join('#ElsIf Flag Then\n' for _ in range(65))+'#EndIf\n'
    with pytest.raises(DynamicAnalysisError, match='condition_context_budget'):
        bind_form_operations(text,PATH)
