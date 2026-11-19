"""Synthetic contract tests for immutable dynamic-operation records (#213)."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from v8unpack_agent.dynamic_forms import (
    BranchKind,
    ConditionKind,
    ConditionReference,
    DynamicCondition,
    DynamicFormOperation,
    DynamicOperationIndex,
    EntityReference,
    OperationArgument,
    OperationKind,
    OperationSource,
    PropertyKind,
    ResolvedValue,
    TargetKind,
    TargetOrigin,
    operation_id,
)
from v8unpack_agent.dynamic_forms import (
    ResolutionStatus as R,
)

SHA = hashlib.sha256(b'synthetic snapshot').hexdigest()


def source(offset=10, path='CommonForm/Demo/Module.bsl'):
    return OperationSource(path, SHA, offset + 1, offset + 1, offset, offset + 1)


def form():
    return EntityReference(TargetKind.FORM, TargetOrigin.FORM, R.EXACT)


def creation(conditions=()):
    src = source()
    ident = operation_id(src, OperationKind.ELEMENT_ADD)
    target = EntityReference(TargetKind.ELEMENT, TargetOrigin.DYNAMIC, R.EXACT,
                             ident, ResolvedValue(R.PARTIAL, expression='Prefix + "Demo"'), conditions)
    return DynamicFormOperation(ident, OperationKind.ELEMENT_ADD, src, 'BuildDemo', target,
        R.PARTIAL, (OperationArgument('name', ResolvedValue(R.PARTIAL, expression='Prefix + "Demo"')),
                    OperationArgument('type', ResolvedValue(R.EXACT, 'ПолеФормы')),
                    OperationArgument('parent', ResolvedValue(R.EXACT, omitted=True))),
        conditions=conditions, provenance=('proven_form_collection',))


def setter(created, value=None, conditions=None):
    src = source(20)
    return DynamicFormOperation(operation_id(src, OperationKind.PROPERTY_SET),
        OperationKind.PROPERTY_SET, src, 'BuildDemo', created.target,
        R.EXACT if value is None else value.resolution,
        property_kind=PropertyKind.WIDTH, value=value or ResolvedValue(R.EXACT, 10),
        conditions=created.conditions if conditions is None else conditions)


def test_computed_name_does_not_downgrade_reference_or_literal_setter():
    create = creation()
    set_width = setter(create)
    index = DynamicOperationIndex((set_width, create))
    payload = index.to_dict(include_expressions=True)
    assert payload['operations'][0]['operation_resolution'] == 'partial'
    assert payload['operations'][0]['target']['name']['resolution'] == 'partial'
    assert payload['operations'][1]['target']['resolution'] == 'exact'
    assert payload['operations'][1]['operation_resolution'] == 'exact'
    assert payload['operations'][1]['value']['value'] == 10
    assert index.select(kind=OperationKind.PROPERTY_SET) == (set_width,)
    assert index.select(procedure='builddemo') == (create, set_width)


def test_computed_setter_value_remains_partial():
    create = creation()
    change = setter(create, ResolvedValue(R.PARTIAL, expression='Prefix + ".Demo"'))
    assert change.target.resolution == R.EXACT
    assert change.operation_resolution == R.PARTIAL


def test_exact_guard_expression_unknown_truth_and_branch_scoped_reference():
    guard = DynamicCondition('demo_guard', ConditionKind.IF, source(1), 'DemoFlag And OtherFlag')
    branch = ConditionReference(guard.condition_id, BranchKind.THEN)
    create = creation((branch,))
    width = setter(create)
    index = DynamicOperationIndex((width, create), (guard,))
    assert index.conditions[0].expression_resolution == R.EXACT
    assert index.conditions[0].runtime_truth == 'unknown'
    assert width.target.valid_in == (branch,)
    assert width.operation_resolution == R.EXACT
    assert not width.condition_context_complete
    with pytest.raises(ValueError, match='outside'):
        setter(create, conditions=())
    with pytest.raises(ValueError, match='runtime truth'):
        replace(guard, runtime_truth='true')


def test_cannot_erase_creation_guard_from_an_exact_reference():
    guard = DynamicCondition('demo_guard', ConditionKind.IF, source(1), 'DemoFlag')
    create = creation((ConditionReference(guard.condition_id, BranchKind.THEN),))
    width = replace(setter(create), target=replace(create.target, valid_in=()), conditions=())
    with pytest.raises(ValueError, match='retain'):
        DynamicOperationIndex((create, width), (guard,))


@pytest.mark.parametrize('path', ['/absolute/Module.bsl', '../Module.bsl', 'C:/Demo/Module.bsl',
    'C:Module.bsl', 'Demo//Module.bsl', 'Demo/./Module.bsl', 'Demo/CON.bsl', 'Demo/Module.bsl '])
def test_cross_platform_invalid_source_paths(path):
    with pytest.raises(ValueError):
        source(path=path)


def test_windows_separator_is_rejected_without_a_platform_specific_fixture():
    with pytest.raises(ValueError):
        source(path='Demo' + chr(92) + 'Module.bsl')


@pytest.mark.parametrize('change', [{'start_line':0}, {'end_line':1}, {'start_offset':-1},
    {'end_offset':10}, {'snapshot_sha256':'bad'}, {'start_offset':True}])
def test_invalid_source_ranges_and_snapshot(change):
    with pytest.raises((ValueError, TypeError)):
        replace(source(), **change)


def test_records_are_deeply_immutable():
    create = creation()
    with pytest.raises(FrozenInstanceError):
        create.procedure = 'Changed'
    with pytest.raises(TypeError):
        replace(create, arguments=list(create.arguments))
    with pytest.raises(TypeError):
        ResolvedValue(R.EXACT, value=['mutable'])
    with pytest.raises(TypeError):
        DynamicOperationIndex([create])
    with pytest.raises(TypeError):
        replace(create.target, valid_in=[])


def test_duplicate_and_invalid_ids_rejected():
    create = creation()
    with pytest.raises(ValueError, match='duplicate operation'):
        DynamicOperationIndex((create, create))
    with pytest.raises(ValueError, match='id must match'):
        replace(create, operation_id='arbitrary')
    with pytest.raises(ValueError, match='duplicate argument'):
        replace(create, arguments=(create.arguments[0], create.arguments[0]))
    with pytest.raises(ValueError, match='arguments must match'):
        replace(create, arguments=())


def test_identity_and_json_are_independent_of_discovery_order():
    create = creation()
    width = setter(create)
    first = DynamicOperationIndex((width, create))
    second = DynamicOperationIndex((create, width))
    assert first.to_json() == second.to_json()
    assert operation_id(source(), OperationKind.ELEMENT_ADD) != operation_id(source(11), OperationKind.ELEMENT_ADD)
    assert operation_id(source(), OperationKind.ELEMENT_ADD) != operation_id(replace(source(), snapshot_sha256='0'*64), OperationKind.ELEMENT_ADD)
    assert json.loads(first.to_json())['declared_elements_included'] is False
    assert 'Prefix' not in first.to_json()
    assert 'Prefix' in first.to_json(include_expressions=True)
    assert first.operations[0].arguments[0].value.expression not in repr(first)


def test_reference_cannot_be_dangling_or_cross_snapshot_or_wrong_kind():
    create = creation()
    with pytest.raises(ValueError, match='creation reference'):
        DynamicOperationIndex((setter(create),))
    wrong = replace(setter(create), target=replace(create.target, target_kind=TargetKind.COMMAND))
    with pytest.raises(ValueError, match='same target kind'):
        DynamicOperationIndex((create, wrong))
    src = replace(source(20), snapshot_sha256='0'*64)
    wrong = replace(setter(create), source=src, operation_id=operation_id(src, OperationKind.PROPERTY_SET))
    with pytest.raises(ValueError, match='crosses source'):
        DynamicOperationIndex((create, wrong))


def test_invalid_resolved_values_cannot_claim_exactness():
    with pytest.raises(ValueError):
        ResolvedValue(R.PARTIAL, value=10, expression='Unknown')
    with pytest.raises(ValueError):
        ResolvedValue(R.PARTIAL)
    with pytest.raises(ValueError):
        ResolvedValue(R.EXACT, float('nan'))
    with pytest.raises(ValueError):
        ResolvedValue(R.EXACT, 10, omitted=True)
    create = creation()
    with pytest.raises(ValueError, match='mandatory values'):
        replace(create, operation_resolution=R.EXACT)
    with pytest.raises(ValueError, match='proven receiver'):
        replace(setter(create), target=EntityReference(TargetKind.UNKNOWN, TargetOrigin.UNKNOWN, R.UNRESOLVED))


def test_unknown_context_is_not_serialized_as_unconditional():
    create = creation()
    data = create.to_dict()
    assert data['conditions'] == []
    assert data['condition_context_complete'] is False
    assert 'unconditional' not in json.dumps(data)


def test_dangling_duplicate_and_cross_source_conditions_rejected():
    branch = ConditionReference('demo_guard', BranchKind.THEN)
    create = creation((branch,))
    guard = DynamicCondition('demo_guard', ConditionKind.IF, source(1), 'DemoFlag')
    with pytest.raises(ValueError, match='dangling condition'):
        DynamicOperationIndex((create,))
    with pytest.raises(ValueError, match='duplicate condition'):
        DynamicOperationIndex((create,), (guard, guard))
    with pytest.raises(ValueError, match='crosses source'):
        DynamicOperationIndex((create,), (replace(guard, source=source(1, 'CommonForm/Other/Module.bsl')),))


def test_reference_does_not_cross_procedure_context():
    create = creation()
    other = replace(setter(create), procedure='AnotherProcedure')
    with pytest.raises(ValueError, match='same procedure'):
        DynamicOperationIndex((create, other))


def test_loop_body_and_if_branch_are_not_interchangeable():
    guard = DynamicCondition('demo_guard', ConditionKind.LOOP, source(1), 'For DemoIndex = 1 To DemoCount')
    branch = ConditionReference(guard.condition_id, BranchKind.THEN)
    create = creation((branch,))
    with pytest.raises(ValueError, match='incompatible'):
        DynamicOperationIndex((create,), (guard,))
    body = ConditionReference(guard.condition_id, BranchKind.BODY)
    create = creation((body,))
    assert DynamicOperationIndex((create, setter(create)), (guard,)).operations[1].operation_resolution == R.EXACT


def test_conflicting_branches_are_rejected_in_one_operation():
    branch = ConditionReference('demo_guard', BranchKind.THEN)
    create = creation((branch,))
    with pytest.raises(ValueError, match='conflicting condition'):
        replace(create, conditions=(branch, ConditionReference('demo_guard', BranchKind.ELSE)))


def test_forward_reference_is_rejected():
    create = creation()
    src = source(5)
    early = replace(setter(create), source=src, operation_id=operation_id(src, OperationKind.PROPERTY_SET))
    with pytest.raises(ValueError, match='precedes'):
        DynamicOperationIndex((early, create))


@pytest.mark.parametrize('kind,args', [
    (OperationKind.COMMAND_ADD, ('name',)),
    (OperationKind.ATTRIBUTES_CHANGE, ('add_list','delete_list')),
    (OperationKind.ELEMENT_INSERT, ('name','type','parent','before')),
    (OperationKind.ELEMENT_MOVE, ('parent','before')),
    (OperationKind.ELEMENT_DELETE, ()),
    (OperationKind.COMMAND_DELETE, ()),
])
def test_closed_argument_contract_for_each_operation_kind(kind,args):
    src = source()
    ident = operation_id(src,kind)
    tk = TargetKind.COMMAND if kind in (OperationKind.COMMAND_ADD,OperationKind.COMMAND_DELETE) else TargetKind.ELEMENT
    if kind == OperationKind.ATTRIBUTES_CHANGE:
        target = form()
    elif kind in (OperationKind.ELEMENT_INSERT,OperationKind.COMMAND_ADD):
        target = EntityReference(tk,TargetOrigin.DYNAMIC,R.EXACT,ident)
    else:
        target = EntityReference(tk,TargetOrigin.DECLARED,R.EXACT,name=ResolvedValue(R.EXACT,'DemoTarget'))
    op = DynamicFormOperation(ident,kind,src,'Demo',target,R.EXACT,
        tuple(OperationArgument(n,ResolvedValue(R.EXACT,'Demo') if n in ('name','type')
              else ResolvedValue(R.EXACT,omitted=True)) for n in args))
    assert op.to_dict()['operation_kind'] == kind.value


def test_form_origin_cannot_assert_an_element_target():
    with pytest.raises(ValueError, match='form origin'):
        EntityReference(TargetKind.ELEMENT,TargetOrigin.FORM,R.EXACT)


def test_batch_attribute_reference_requires_its_name():
    src = source(1)
    ident = operation_id(src,OperationKind.ATTRIBUTES_CHANGE)
    batch = DynamicFormOperation(ident,OperationKind.ATTRIBUTES_CHANGE,src,'Demo',form(),R.PARTIAL,
        (OperationArgument('add_list',ResolvedValue(R.PARTIAL,expression='DemoAttributes')),
         OperationArgument('delete_list',ResolvedValue(R.EXACT,omitted=True))))
    ref = EntityReference(TargetKind.ATTRIBUTE,TargetOrigin.DYNAMIC,R.EXACT,ident)
    src = source(20)
    change = DynamicFormOperation(operation_id(src,OperationKind.PROPERTY_SET),OperationKind.PROPERTY_SET,
        src,'Demo',ref,R.EXACT,property_kind=PropertyKind.TITLE,value=ResolvedValue(R.EXACT,'Demo'))
    with pytest.raises(ValueError,match='exact attribute name'):
        DynamicOperationIndex((batch,change))
    named = replace(change,target=replace(ref,name=ResolvedValue(R.EXACT,'DemoAttribute')))
    assert len(DynamicOperationIndex((batch,named)).operations) == 2
