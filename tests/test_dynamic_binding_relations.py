"""Potential source relations retain identity, uncertainty, and diagnostics."""
from v8unpack_agent.dynamic_forms import OperationKind, TargetKind
from v8unpack_agent.dynamic_forms import ResolutionStatus as R
from v8unpack_agent.dynamic_forms_binding import (
    BindingReason,
    RelationKind,
    bind_form_operations,
)


def run(body, **kwargs):
    return bind_form_operations('Procedure Build()\n'+body+'\nEndProcedure','CommonForm/Demo/Module.bsl',**kwargs)


def test_closed_attribute_element_data_path_chain_keeps_separate_reference_and_chain_status():
    result=run('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs); X=Items.Add("Field",Type("FormField")); X.DataPath="Demo";')
    attr=next(c for c in result.candidates if c.reference.target_kind == TargetKind.ATTRIBUTE)
    element=next(c for c in result.candidates if c.reference.target_kind == TargetKind.ELEMENT)
    relation=next(r for r in element.relations if r.kind == RelationKind.DATA_PATH)
    assert attr.reference.resolution == relation.target.resolution == R.EXACT
    assert relation.target_candidate_id == attr.candidate_id
    assert relation.resolution == R.EXACT
    # The batch array expression remains partial even though root membership is proven.
    assert attr.resolution == element.resolution == R.PARTIAL
    assert attr.evidence_sources


def test_computed_name_does_not_weaken_exact_reference_but_weakens_whole_chain():
    result=run('Group=Items.Add(Prefix,Type("FormGroup")); X=Items.Add("Field",Type("FormField"),Group);')
    child=next(c for c in result.candidates if c.reference.name.value == 'Field')
    parent=next(r for r in child.relations if r.kind == RelationKind.PARENT)
    assert parent.target.resolution == parent.resolution == R.EXACT
    assert child.reference.resolution == R.EXACT
    assert child.resolution == R.PARTIAL


def test_declared_data_path_reference_is_not_a_dynamic_creation():
    result=run('X=Items.Add("Field",Type("FormField")); X.DataPath="Demo";',declared_attributes=('Demo',))
    attr=next(c for c in result.candidates if c.reference.target_kind == TargetKind.ATTRIBUTE)
    assert not attr.operation_ids
    assert attr.effect.value == 'reference'
    assert attr.reference.creation_operation_id is None


def test_command_assignment_relates_to_proven_local_command():
    result=run('Commands.Add("Demo"); X=Items.Add("Field",Type("FormButton")); X.CommandName="Demo";')
    child=next(c for c in result.candidates if c.reference.target_kind == TargetKind.ELEMENT)
    relation=next(r for r in child.relations if r.kind == RelationKind.COMMAND)
    assert relation.resolution == R.EXACT
    assert relation.target_candidate_id


def test_source_conflicts_preserve_every_operation_and_do_not_choose_latest_value():
    result=run('X=Items.Add("Demo",Type("FormField")); X.Width=8; X.Width=9; Items.Delete(X); Items.Delete(X);')
    assert len(result.bound.operations) == 5
    reasons={d.reason for c in result.candidates for d in c.diagnostics}
    assert BindingReason.MULTIPLE_VALUES in reasons
    assert BindingReason.REPEATED_DELETE in reasons


def test_parent_cycle_is_diagnostic_and_never_an_exact_chain():
    result=run('X=Items.Add("X",Type("FormGroup")); Y=Items.Add("Y",Type("FormGroup")); X.Parent=Y; Y.Parent=X;')
    assert any(BindingReason.CYCLIC_RELATION == d.reason for c in result.candidates for d in c.diagnostics)
    assert all(c.resolution == R.UNRESOLVED for c in result.candidates)


def test_loop_carried_attribute_namespace_does_not_give_exact_data_path_relation():
    result=run('Attrs=New Array; Attrs.Add(New FormAttribute("Demo",New TypeDescription("String"))); ChangeAttributes(Attrs); For I=1 To 3 Do X=Items.Add("Field",Type("FormField")); X.DataPath="Demo"; ChangeAttributes(UnknownAttrs); EndDo;')
    relation=next(r for c in result.candidates for r in c.relations if r.kind == RelationKind.DATA_PATH)
    assert relation.resolution == R.UNRESOLVED


def test_unknown_receiver_retains_operation_but_never_an_exact_relation():
    result=run('X.DataPath="Demo";',declared_attributes=('Demo',))
    assert result.bound.operations[0].operation_kind == OperationKind.PROPERTY_SET
    relation=next(r for c in result.candidates for r in c.relations)
    assert relation.resolution == R.UNRESOLVED


def test_serialization_is_deterministic_and_expressions_are_opt_in():
    first=run('X=Items.Add(Prefix,Type("FormField")); X.Width=WidthValue;')
    second=run('X=Items.Add(Prefix,Type("FormField")); X.Width=WidthValue;')
    assert first.to_json() == second.to_json()
    assert 'WidthValue' not in first.to_json()
    assert 'WidthValue' in first.to_json(include_expressions=True)


def test_index_rejects_dangling_diagnostic_and_upgraded_conditional_summary():
    from dataclasses import replace

    import pytest

    from v8unpack_agent.dynamic_forms_binding import BindingDiagnostic
    result=run('If Flag Then X=Items.Add("Demo",Type("FormField")); EndIf;')
    candidate=result.candidates[0]
    with pytest.raises(ValueError,match='conditional'):
        replace(result,candidates=(replace(candidate,conditional=False),))
    with pytest.raises(ValueError,match='dangling diagnostic'):
        replace(result,diagnostics=(BindingDiagnostic(BindingReason.OPAQUE_CALL,('missing',)),))


def test_index_rejects_dangling_and_cross_kind_relation_creation():
    from dataclasses import replace

    import pytest

    result=run('Group=Items.Add("Group",Type("FormGroup")); X=Items.Add("Demo",Type("FormField"),Group);')
    child=next(c for c in result.candidates if c.reference.name.value == 'Demo')
    relation=next(r for r in child.relations if r.kind == RelationKind.PARENT)
    others=tuple(c for c in result.candidates if c.candidate_id != child.candidate_id)
    with pytest.raises(ValueError,match='compatible target kind'):
        bad=replace(relation,target=replace(relation.target,target_kind=TargetKind.COMMAND))
        replace(result,candidates=others+(replace(child,relations=(bad,)),))
    with pytest.raises(ValueError,match='same target kind'):
        bad=replace(relation,target=replace(relation.target,creation_operation_id='missing'))
        replace(result,candidates=others+(replace(child,relations=(bad,)),))


def test_wrong_entity_kind_in_parent_argument_is_unknown_relation_not_a_crash():
    result=run('X=Items.Add("Demo",Type("FormField"),ThisObject);')
    relation=next(r for c in result.candidates for r in c.relations if r.kind == RelationKind.PARENT)
    assert relation.resolution == R.UNRESOLVED
    assert any(d.reason == BindingReason.UNSUPPORTED_CONSTRUCT for c in result.candidates for d in c.diagnostics)


def test_candidate_relation_order_is_canonical_and_duplicate_role_is_rejected():
    from dataclasses import replace

    import pytest

    result=run('Group=Items.Add("Group",Type("FormGroup")); X=Items.Add("Demo",Type("FormField"),Group);')
    candidate=next(c for c in result.candidates if c.reference.name.value == 'Demo')
    assert replace(candidate,relations=tuple(reversed(candidate.relations))) == candidate
    with pytest.raises(ValueError,match='duplicate relation role'):
        replace(candidate,relations=candidate.relations+(candidate.relations[0],))


def test_binding_index_cannot_change_raw_procedure_context():
    from dataclasses import replace

    import pytest

    result=run('Items.Add("Demo",Type("FormField"));')
    changed=replace(result.bound,operations=(replace(result.bound.operations[0],procedure='Other'),))
    with pytest.raises(ValueError,match='preserve raw source context'):
        replace(result,bound=changed)


def test_invalidated_alias_is_not_merged_into_proven_creation_candidate():
    result=run('X=Items.Add("Demo",Type("FormField")); Helper(X); X.Width=8;')
    creation, setter=result.bound.operations
    assert setter.target.resolution == R.UNRESOLVED
    assert setter.target.creation_operation_id == creation.operation_id
    owners=[c for c in result.candidates if setter.operation_id in c.operation_ids]
    assert len(owners) == 1
    assert owners[0].reference.resolution == R.UNRESOLVED
    assert creation.operation_id not in owners[0].operation_ids
    assert any(d.reason == BindingReason.ALIAS_INVALIDATED for d in owners[0].diagnostics)
