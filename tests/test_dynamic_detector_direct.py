import pytest

from v8unpack_agent.dynamic_forms import OperationKind
from v8unpack_agent.dynamic_forms import ResolutionStatus as R
from v8unpack_agent.dynamic_forms_detector import detect_form_operations as detect


def analyze(body, **kwargs):
    return detect('Procedure Build()\n' + body + '\nEndProcedure', 'CommonForm/Demo/Module.bsl', **kwargs)


def test_creation_identity_exact_even_with_partial_name():
    index = analyze('Demo = Items.Add(Prefix + "Demo", Type("FormField"));')
    op, = index.operations
    assert op.operation_kind == OperationKind.ELEMENT_ADD
    assert op.operation_resolution == R.PARTIAL
    assert op.target.resolution == R.EXACT
    assert op.target.creation_operation_id == op.operation_id
    assert op.target.name.resolution == R.PARTIAL


def test_literal_creation_with_omitted_parent_is_exact():
    op, = analyze('Items.Add("Demo", Type("FormField"));').operations
    assert op.operation_resolution == R.EXACT
    assert op.arguments[2].value.omitted


@pytest.mark.parametrize('body', ['// Items.Add("Ignore", Type("FormField"));', 'X = "Items.Add(""Ignore"", Type(""FormField""));";', 'Arbitrary.Items.Add("Demo", Type("FormField"));', 'If Items.Demo.Visible = True Then X=1; EndIf;'])
def test_comments_strings_suffixes_and_comparisons_not_confirmed(body):
    assert not analyze(body).operations


def test_declared_name_requires_supplied_inventory():
    unresolved, = analyze('Items.Demo.Visible = True;').operations
    exact, = analyze('Items.Demo.Visible = True;', declared_elements=('Demo',)).operations
    assert unresolved.operation_resolution == R.UNRESOLVED
    assert exact.operation_resolution == R.EXACT


def test_shadowed_collection_never_exact():
    op, = analyze('Var Items; Items.Add("Demo", Type("FormField"));').operations
    assert op.operation_resolution == R.UNRESOLVED


def test_conditional_creation_retains_exact_reference_and_unknown_truth():
    result = analyze('If DemoFlag Then Items.Add(Prefix + "Demo", Type("FormField")); EndIf;')
    op, = result.operations
    condition, = result.conditions
    assert op.target.resolution == R.EXACT
    assert op.target.valid_in == op.conditions
    assert condition.expression == 'DemoFlag'
    assert condition.runtime_truth == 'unknown'


def test_every_kind_has_a_positive_direct_construction():
    result = analyze('''Items.Add("Demo", Type("FormField"));
Items.Insert("Other", Type("FormField"));
Items.Delete(Items.Demo);
Items.Move(Items.Demo, Items.Group);
ChangeAttributes(DemoAttributes);
Commands.Add("DemoCommand");
Commands.Delete(Commands.DemoCommand);
ThisObject.Title = "Demo";''', declared_elements=('Demo','Group'), declared_commands=('DemoCommand',))
    assert {op.operation_kind for op in result.operations} == set(OperationKind)
    assert all(op.operation_resolution != R.UNRESOLVED for op in result.operations)


def test_local_change_attributes_procedure_shadows_builtin():
    text = 'Procedure ChangeAttributes(Value) EndProcedure Procedure Demo() ChangeAttributes(DemoList); EndProcedure'
    result = detect(text, 'CommonForm/Demo/Module.bsl')
    assert result.operations[0].operation_resolution == R.UNRESOLVED


def test_multiline_russian_mixed_case_and_repeatability():
    text = 'Процедура Демо()\nЭлЕмЕнТы.ДоБаВиТь(\n"Демо",\nТип("ПолеФормы")\n);\nКонецПроцедуры'
    first = detect(text, 'CommonForm/Demo/Module.bsl')
    assert first.to_json() == detect(text, 'CommonForm/Demo/Module.bsl').to_json()
    assert first.operations[0].source.start_line == 2
    assert first.operations[0].source.end_line == 5
    assert first.operations[0].operation_resolution == R.EXACT


def test_semicolon_string_is_value_not_a_statement_delimiter():
    result = analyze('ThisObject.Title = ";";')
    op, = result.operations
    assert op.operation_resolution == R.EXACT
    assert op.value.value == ';'


@pytest.mark.parametrize('property_name', ['Visible','Enabled','Title','ReadOnly','DataPath','CommandName','Type','Parent','CurrentPage','CurrentRow','ToolTip','Width','Height'])
def test_all_closed_properties(property_name):
    op, = analyze(f'Items.Demo.{property_name} = 8;', declared_elements=('Demo',)).operations
    assert op.operation_resolution == R.EXACT


def test_late_assignment_and_formal_parameter_invalidate_collection_proof():
    result = analyze('Items.Add("Demo", Type("FormField")); Items = DemoValue;')
    assert result.operations[0].operation_resolution == R.UNRESOLVED
    text = 'Procedure Demo(Items) Items.Add("Demo", Type("FormField")); EndProcedure'
    result = detect(text, 'CommonForm/Demo/Module.bsl')
    assert result.operations[0].operation_resolution == R.UNRESOLVED


def test_member_assignment_does_not_shadow_the_collection_itself():
    result = analyze('Items.Demo.Visible = True; Items.Add("Other", Type("FormField"));', declared_elements=('Demo',))
    assert [op.operation_resolution for op in result.operations] == [R.EXACT, R.EXACT]


def test_type_builtin_shadowing_keeps_type_expression_partial():
    text = 'Function Type(Name) Return DemoValue; EndFunction Procedure Demo() Items.Add("Demo", Type("FormField")); EndProcedure'
    result = detect(text, 'CommonForm/Demo/Module.bsl')
    assert result.operations[0].operation_resolution == R.PARTIAL
    assert result.operations[0].arguments[1].value.resolution == R.PARTIAL


def test_parent_declared_reference_is_exact_without_dynamic_linking():
    op, = analyze('Items.Add("Demo", Type("FormField"), Items.Group);', declared_elements=('Group',)).operations
    assert op.operation_resolution == R.EXACT
    assert op.arguments[2].value.resolution == R.EXACT


def test_aliases_are_not_bound_in_stage214():
    result = analyze('Demo = Items.Add("Demo", Type("FormField")); Demo.Width = 8;')
    assert [op.operation_resolution for op in result.operations] == [R.EXACT, R.UNRESOLVED]


def test_parenthesis_string_after_return_is_not_a_call():
    assert not analyze('Return "(";').operations


def test_creation_evaluated_in_if_header_is_not_guarded_by_its_result():
    result = analyze('If Check(Items.Add("Demo", Type("FormField"))) Then X=1; EndIf;')
    op, = result.operations
    assert op.conditions == ()
    assert op.target.valid_in == ()


def test_optional_final_semicolon_does_not_swallow_branch_keywords():
    result = analyze('If DemoFlag Then ThisObject.Title = "Then" Else ThisObject.Title = "Else" EndIf;')
    assert [op.value.value for op in result.operations] == ['Then', 'Else']
    assert all(op.operation_resolution == R.EXACT for op in result.operations)
    final, = analyze('ThisObject.Title = "Demo"').operations
    assert final.value.value == 'Demo'


def test_unsupported_arity_does_not_erase_unrelated_proven_operations():
    result = analyze('Items.Add(); Items.Add("Demo", Type("FormField"));')
    assert [op.operation_resolution for op in result.operations] == [R.UNRESOLVED, R.EXACT]
    assert result.operations[0].provenance == ('unsupported_call_arity',)


def test_incompatible_delete_target_never_exact_or_model_failure():
    op, = analyze('Items.Delete(ThisObject);').operations
    assert op.operation_resolution == R.UNRESOLVED


def test_unsupported_attribute_arity_preserves_neighboring_creation():
    result = analyze('ChangeAttributes(); Items.Add("Demo", Type("FormField"));')
    assert [op.operation_resolution for op in result.operations] == [R.UNRESOLVED, R.EXACT]


@pytest.mark.parametrize('body', [
    'DemoResult = Items.Demo.Visible = True;',
    'Check(Items.Demo.Visible = True);',
    'Return Items.Demo.Visible = True;',
    'While Items.Demo.Visible = True Do X=1; EndDo;',
])
def test_comparison_in_expression_never_becomes_setter(body):
    assert not analyze(body, declared_elements=('Demo',)).operations


def test_explicit_form_collection_ignores_unrelated_local_collection_name():
    result = analyze('Var Items; ThisObject.Items.Add("Demo", Type("FormField"));')
    assert result.operations[0].operation_resolution == R.EXACT


@pytest.mark.parametrize('prefix', ['Try Items = DemoValue; Except EndTry;', 'Try X=1; Except Items = DemoValue; EndTry;', '~DemoLabel: Items = DemoValue;'])
def test_assignments_after_nonstandard_boundaries_cannot_prove_platform_collection(prefix):
    result = analyze(prefix + ' Items.Add("Demo", Type("FormField"));')
    assert result.operations[0].operation_resolution == R.UNRESOLVED
