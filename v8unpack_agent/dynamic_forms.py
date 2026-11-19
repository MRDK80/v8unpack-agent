"""Immutable dynamic-form operation model and deterministic index (#213).

Syntactic operations are separate from declared layout and runtime instances.
Expressions and source-derived names are untrusted data, never instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from v8unpack_agent.modules import validate_relative_module_path

__all__ = [
    'SCHEMA_VERSION',
    'BranchKind',
    'ConditionKind',
    'ConditionReference',
    'DynamicCondition',
    'DynamicFormOperation',
    'DynamicOperationIndex',
    'DynamicReason',
    'EntityReference',
    'OperationArgument',
    'OperationKind',
    'OperationSource',
    'PropertyKind',
    'ResolutionStatus',
    'ResolvedValue',
    'TargetKind',
    'TargetOrigin',
    'operation_id',
]

SCHEMA_VERSION = 'dynamic_forms/1'


class OperationKind(str, Enum):
    ELEMENT_ADD = 'element_add'
    ELEMENT_INSERT = 'element_insert'
    ELEMENT_DELETE = 'element_delete'
    ELEMENT_MOVE = 'element_move'
    ATTRIBUTES_CHANGE = 'attributes_change'
    COMMAND_ADD = 'command_add'
    COMMAND_DELETE = 'command_delete'
    PROPERTY_SET = 'property_set'


class TargetKind(str, Enum):
    FORM = 'form'
    ELEMENT = 'element'
    COMMAND = 'command'
    ATTRIBUTE = 'attribute'
    UNKNOWN = 'unknown'


class ResolutionStatus(str, Enum):
    EXACT = 'exact'
    PARTIAL = 'partial'
    UNRESOLVED = 'unresolved'


class ConditionKind(str, Enum):
    UNCONDITIONAL = 'unconditional'
    IF = 'if'
    LOOP = 'loop'
    PREPROCESSOR = 'preprocessor'
    MIXED = 'mixed'
    UNKNOWN = 'unknown'


class BranchKind(str, Enum):
    THEN = 'then'
    ELSE = 'else'
    ELSEIF = 'elseif'
    BODY = 'body'


class TargetOrigin(str, Enum):
    DECLARED = 'declared'
    DYNAMIC = 'dynamic'
    FORM = 'form'
    UNKNOWN = 'unknown'


class DynamicReason(str, Enum):
    UNKNOWN = 'unknown'
    RECEIVER_UNPROVEN = 'receiver_unproven'
    COMPUTED_VALUE = 'computed_value'
    ALIAS_INVALIDATED = 'alias_invalidated'
    CONFLICTING_BINDINGS = 'conflicting_bindings'
    BRANCH_SCOPE = 'branch_scope'
    UNSUPPORTED_SYNTAX = 'unsupported_syntax'
    BUDGET_EXCEEDED = 'budget_exceeded'
    LEXER_FAILURE = 'lexer_failure'


class PropertyKind(str, Enum):
    VISIBLE = 'видимость'
    ENABLED = 'доступность'
    TITLE = 'заголовок'
    READ_ONLY = 'толькопросмотр'
    DATA_PATH = 'путькданным'
    COMMAND_NAME = 'имякоманды'
    TYPE = 'вид'
    PARENT = 'родитель'
    CURRENT_PAGE = 'текущаястраница'
    CURRENT_ROW = 'текущаястрока'
    TOOLTIP = 'подсказка'
    WIDTH = 'ширина'
    HEIGHT = 'высота'


def _enum(value: Any, cls: type[Enum]) -> None:
    if not isinstance(value, cls):
        raise TypeError(f'expected {cls.__name__}')


def _text(value: Any, label: str, *, empty: bool = False) -> None:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f'{label} must be a nonempty string')


def _tuple(value: Any, cls: type) -> None:
    if not isinstance(value, tuple) or any(not isinstance(x, cls) for x in value):
        raise TypeError('expected an immutable tuple of supported values')


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class OperationSource:
    relative_path: str
    snapshot_sha256: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        validate_relative_module_path(self.relative_path)
        if not isinstance(self.snapshot_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', self.snapshot_sha256):
            raise ValueError('snapshot_sha256 must be a lowercase SHA-256')
        for value in (self.start_line, self.end_line, self.start_offset, self.end_offset):
            if type(value) is not int:
                raise TypeError('source positions must be integers')
        if self.start_line < 1 or self.end_line < self.start_line or self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError('invalid source range')

    def to_dict(self) -> dict[str, Any]:
        return {'relative_path': self.relative_path, 'snapshot_sha256': self.snapshot_sha256, 'start_line': self.start_line, 'end_line': self.end_line, 'start_offset': self.start_offset, 'end_offset': self.end_offset}


def operation_id(source: OperationSource, kind: OperationKind) -> str:
    """Identity includes snapshot and half-open character offsets, not discovery order."""
    _enum(kind, OperationKind)
    return 'dop_' + _digest([SCHEMA_VERSION, source.to_dict(), kind.value])


@dataclass(frozen=True)
class ResolvedValue:
    resolution: ResolutionStatus
    value: str | int | float | bool | None = None
    expression: str | None = field(default=None, repr=False)
    reason: DynamicReason | None = None
    omitted: bool = False

    def __post_init__(self) -> None:
        _enum(self.resolution, ResolutionStatus)
        if self.reason is not None:
            _enum(self.reason, DynamicReason)
        if self.reason is None and self.resolution != ResolutionStatus.EXACT:
            object.__setattr__(self, 'reason', DynamicReason.UNKNOWN)
        if self.expression is not None:
            _text(self.expression, 'expression')
        if self.value is not None and type(self.value) not in (str, int, float, bool):
            raise TypeError('value must be an immutable JSON scalar')
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError('value must be finite')
        if type(self.omitted) is not bool:
            raise TypeError('omitted must be bool')
        if self.omitted and (self.value is not None or self.expression is not None):
            raise ValueError('omitted argument cannot have a value or expression')
        if self.resolution != ResolutionStatus.EXACT and self.value is not None:
            raise ValueError('nonexact value cannot assert a resolved scalar')
        if self.resolution == ResolutionStatus.PARTIAL and self.expression is None:
            raise ValueError('partial value requires an expression')
        if self.omitted and self.resolution != ResolutionStatus.EXACT:
            raise ValueError('omission is an exact syntactic fact')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'resolution': self.resolution.value, 'value': self.value, 'expression': self.expression if include_expressions else None, 'expression_present': self.expression is not None, 'expression_sha256': _digest(self.expression) if self.expression is not None else None, 'reason': self.reason.value if self.reason else None, 'omitted': self.omitted}


@dataclass(frozen=True)
class ConditionReference:
    condition_id: str
    branch: BranchKind

    def __post_init__(self) -> None:
        _text(self.condition_id, 'condition_id')
        _enum(self.branch, BranchKind)

    def to_dict(self) -> dict[str, str]:
        return {'reference': self.condition_id, 'branch': self.branch.value}


@dataclass(frozen=True)
class DynamicCondition:
    condition_id: str
    kind: ConditionKind
    source: OperationSource
    expression: str = field(repr=False)
    expression_resolution: ResolutionStatus = ResolutionStatus.EXACT
    runtime_truth: str = 'unknown'

    def __post_init__(self) -> None:
        _text(self.condition_id, 'condition_id')
        _enum(self.kind, ConditionKind)
        _enum(self.expression_resolution, ResolutionStatus)
        _text(self.expression, 'expression')
        if not isinstance(self.source, OperationSource):
            raise TypeError('condition requires OperationSource')
        if self.kind in (ConditionKind.UNCONDITIONAL, ConditionKind.MIXED):
            raise ValueError('individual condition must have a concrete kind or unknown')
        if self.runtime_truth != 'unknown':
            raise ValueError('runtime truth must remain unknown')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'id': self.condition_id, 'kind': self.kind.value, 'source': self.source.to_dict(), 'expression': self.expression if include_expressions else None, 'expression_present': True, 'expression_sha256': _digest(self.expression), 'expression_resolution': self.expression_resolution.value, 'runtime_truth': 'unknown'}


@dataclass(frozen=True)
class EntityReference:
    target_kind: TargetKind
    origin: TargetOrigin
    resolution: ResolutionStatus
    creation_operation_id: str | None = None
    name: ResolvedValue | None = None
    valid_in: tuple[ConditionReference, ...] = ()
    reason: DynamicReason | None = None

    def __post_init__(self) -> None:
        _enum(self.target_kind, TargetKind)
        _enum(self.origin, TargetOrigin)
        _enum(self.resolution, ResolutionStatus)
        _tuple(self.valid_in, ConditionReference)
        if self.reason is not None:
            _enum(self.reason, DynamicReason)
        if self.reason is None and self.resolution != ResolutionStatus.EXACT:
            object.__setattr__(self, 'reason', DynamicReason.UNKNOWN)
        if self.name is not None and not isinstance(self.name, ResolvedValue):
            raise TypeError('name requires ResolvedValue')
        if self.creation_operation_id is not None:
            _text(self.creation_operation_id, 'creation_operation_id')
            if self.origin != TargetOrigin.DYNAMIC:
                raise ValueError('creation reference requires dynamic origin')
        if self.origin == TargetOrigin.FORM and self.target_kind != TargetKind.FORM:
            raise ValueError('form origin requires form target kind')
        if self.resolution == ResolutionStatus.EXACT:
            if self.target_kind == TargetKind.UNKNOWN or self.origin == TargetOrigin.UNKNOWN:
                raise ValueError('exact target requires known kind and origin')
            if self.origin == TargetOrigin.DYNAMIC and not self.creation_operation_id:
                raise ValueError('exact dynamic target requires a creation reference')
            if self.origin == TargetOrigin.DECLARED and (self.name is None or self.name.resolution != ResolutionStatus.EXACT):
                raise ValueError('exact declared target requires an exact name')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'target_kind': self.target_kind.value, 'origin': self.origin.value, 'resolution': self.resolution.value, 'reference': self.creation_operation_id, 'name': self.name.to_dict(include_expressions=include_expressions) if self.name else None, 'valid_in': [c.to_dict() for c in self.valid_in], 'reason': self.reason.value if self.reason else None}


@dataclass(frozen=True)
class OperationArgument:
    name: str
    value: ResolvedValue | EntityReference

    def __post_init__(self) -> None:
        _text(self.name, 'argument name')
        if not isinstance(self.value, (ResolvedValue, EntityReference)):
            raise TypeError('argument requires resolved value or entity reference')


@dataclass(frozen=True)
class DynamicFormOperation:
    operation_id: str
    operation_kind: OperationKind
    source: OperationSource
    procedure: str
    target: EntityReference
    operation_resolution: ResolutionStatus
    arguments: tuple[OperationArgument, ...] = ()
    property_kind: PropertyKind | None = None
    value: ResolvedValue | None = None
    conditions: tuple[ConditionReference, ...] = ()
    condition_context_complete: bool = False
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _enum(self.operation_kind, OperationKind)
        _enum(self.operation_resolution, ResolutionStatus)
        if not isinstance(self.source, OperationSource) or not isinstance(self.target, EntityReference):
            raise TypeError('operation requires source and target')
        if self.operation_id != operation_id(self.source, self.operation_kind):
            raise ValueError('operation id must match source snapshot/range/kind')
        _text(self.procedure, 'procedure')
        _tuple(self.arguments, OperationArgument)
        _tuple(self.conditions, ConditionReference)
        _tuple(self.provenance, str)
        if type(self.condition_context_complete) is not bool:
            raise TypeError('condition_context_complete must be bool')
        if len({c.condition_id for c in self.conditions}) != len(self.conditions):
            raise ValueError('duplicate or conflicting condition branches')
        if len({a.name for a in self.arguments}) != len(self.arguments):
            raise ValueError('duplicate argument name')
        if self.property_kind is not None:
            _enum(self.property_kind, PropertyKind)
        if self.value is not None and not isinstance(self.value, ResolvedValue):
            raise TypeError('value requires ResolvedValue')
        expected_target = (TargetKind.FORM if self.operation_kind == OperationKind.ATTRIBUTES_CHANGE
                           else TargetKind.COMMAND if self.operation_kind in {OperationKind.COMMAND_ADD, OperationKind.COMMAND_DELETE}
                           else None if self.operation_kind == OperationKind.PROPERTY_SET else TargetKind.ELEMENT)
        if expected_target is not None and self.target.target_kind not in {expected_target, TargetKind.UNKNOWN}:
            raise ValueError('target kind is incompatible with operation kind')
        if (self.operation_kind in {OperationKind.ELEMENT_ADD, OperationKind.ELEMENT_INSERT, OperationKind.COMMAND_ADD}
                and self.target.resolution == ResolutionStatus.EXACT
                and self.target.creation_operation_id != self.operation_id):
            raise ValueError('creation target must reference its own operation')
        required_arguments = {
            OperationKind.ELEMENT_ADD: {'name', 'type', 'parent'},
            OperationKind.ELEMENT_INSERT: {'name', 'type', 'parent', 'before'},
            OperationKind.ELEMENT_MOVE: {'parent', 'before'},
            OperationKind.ATTRIBUTES_CHANGE: {'add_list', 'delete_list'},
            OperationKind.COMMAND_ADD: {'name'},
        }.get(self.operation_kind, set())
        if {a.name for a in self.arguments} != required_arguments:
            raise ValueError('arguments must match the operation contract, including explicit omissions')
        for argument in self.arguments:
            if argument.name in {'name', 'type'} and isinstance(argument.value, ResolvedValue) and argument.value.omitted:
                raise ValueError('name and type cannot be omitted')
        if self.operation_kind == OperationKind.PROPERTY_SET:
            if self.property_kind is None or self.value is None:
                raise ValueError('setter requires property and value')
        elif self.property_kind is not None or self.value is not None:
            raise ValueError('property/value only apply to setters')
        if self.operation_resolution != ResolutionStatus.UNRESOLVED and self.target.resolution != ResolutionStatus.EXACT:
            raise ValueError('confirmed operation requires a proven receiver')
        if self.operation_resolution == ResolutionStatus.EXACT:
            values = [a.value.resolution for a in self.arguments]
            if self.value is not None:
                values.append(self.value.resolution)
            if any(x != ResolutionStatus.EXACT for x in values):
                raise ValueError('exact operation requires exact mandatory values')
        for reference in (self.target, *(a.value for a in self.arguments if isinstance(a.value, EntityReference))):
            if any(c not in self.conditions for c in reference.valid_in):
                raise ValueError('reference is outside its proven branch context')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'id': self.operation_id, 'operation_kind': self.operation_kind.value, 'source': self.source.to_dict(), 'procedure': self.procedure, 'target': self.target.to_dict(include_expressions=include_expressions), 'operation_resolution': self.operation_resolution.value, 'arguments': {a.name: a.value.to_dict(include_expressions=include_expressions)
                               for a in sorted(self.arguments, key=lambda a: a.name)}, 'property_kind': self.property_kind.value if self.property_kind else None, 'value': self.value.to_dict(include_expressions=include_expressions) if self.value else None, 'conditions': [c.to_dict() for c in self.conditions], 'condition_context_complete': self.condition_context_complete, 'provenance': list(self.provenance), 'representation': 'dynamic_operation'}


@dataclass(frozen=True)
class DynamicOperationIndex:
    operations: tuple[DynamicFormOperation, ...] = ()
    conditions: tuple[DynamicCondition, ...] = ()

    def __post_init__(self) -> None:
        _tuple(self.operations, DynamicFormOperation)
        _tuple(self.conditions, DynamicCondition)
        if len({o.operation_id for o in self.operations}) != len(self.operations):
            raise ValueError('duplicate operation id')
        if len({c.condition_id for c in self.conditions}) != len(self.conditions):
            raise ValueError('duplicate condition id')
        condition_map = {c.condition_id: c for c in self.conditions}
        operation_map = {o.operation_id: o for o in self.operations}
        for op in self.operations:
            for c in op.conditions:
                if c.condition_id not in condition_map:
                    raise ValueError('dangling condition reference')
                definition = condition_map[c.condition_id]
                allowed = ({BranchKind.BODY} if definition.kind == ConditionKind.LOOP
                           else {BranchKind.THEN, BranchKind.ELSE, BranchKind.ELSEIF})
                if definition.kind != ConditionKind.UNKNOWN and c.branch not in allowed:
                    raise ValueError('branch is incompatible with condition kind')
                source = definition.source
                if (source.relative_path, source.snapshot_sha256) != (op.source.relative_path, op.source.snapshot_sha256):
                    raise ValueError('condition reference crosses source snapshots')
        creation_kinds = {OperationKind.ELEMENT_ADD: TargetKind.ELEMENT,
                          OperationKind.ELEMENT_INSERT: TargetKind.ELEMENT,
                          OperationKind.COMMAND_ADD: TargetKind.COMMAND,
                          OperationKind.ATTRIBUTES_CHANGE: TargetKind.ATTRIBUTE}
        for op in self.operations:
            for ref in (op.target, *(a.value for a in op.arguments if isinstance(a.value, EntityReference))):
                if ref.creation_operation_id is None:
                    continue
                creator = operation_map.get(ref.creation_operation_id)
                if creator is None or creation_kinds.get(creator.operation_kind) != ref.target_kind:
                    raise ValueError('creation reference must identify a creation of the same target kind')
                if (creator.source.relative_path, creator.source.snapshot_sha256) != (op.source.relative_path, op.source.snapshot_sha256):
                    raise ValueError('creation reference crosses source snapshots')
                if ref.target_kind == TargetKind.ATTRIBUTE and (ref.name is None or ref.name.resolution != ResolutionStatus.EXACT or not isinstance(ref.name.value, str) or not ref.name.value):
                    raise ValueError('batch attribute reference requires an exact attribute name')
                if creator.procedure.casefold() != op.procedure.casefold():
                    raise ValueError('creation reference requires the same procedure context')
                if creator.source.start_offset > op.source.start_offset:
                    raise ValueError('creation reference precedes its definition')
                if ref.resolution == ResolutionStatus.EXACT and creator.operation_resolution == ResolutionStatus.UNRESOLVED:
                    raise ValueError('exact reference requires a proven creation operation')
                if ref.resolution == ResolutionStatus.EXACT and any(c not in ref.valid_in for c in creator.conditions):
                    raise ValueError('exact creation reference must retain its creation branch context')
        object.__setattr__(self, 'operations', tuple(sorted(self.operations, key=lambda o:
            (o.source.relative_path, o.source.snapshot_sha256, o.source.start_offset, o.source.end_offset, o.operation_kind.value, o.operation_id))))
        object.__setattr__(self, 'conditions', tuple(sorted(self.conditions, key=lambda c:
            (c.source.relative_path, c.source.snapshot_sha256, c.source.start_offset, c.condition_id))))

    def select(self, *, kind: OperationKind | None = None, procedure: str | None = None) -> tuple[DynamicFormOperation, ...]:
        if kind is not None:
            _enum(kind, OperationKind)
        return tuple(o for o in self.operations if (kind is None or o.operation_kind == kind)
                     and (procedure is None or o.procedure.casefold() == procedure.casefold()))

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'schema': SCHEMA_VERSION, 'declared_elements_included': False, 'operations': [o.to_dict(include_expressions=include_expressions) for o in self.operations], 'conditions': [c.to_dict(include_expressions=include_expressions) for c in self.conditions]}

    def to_json(self, *, include_expressions: bool = False) -> str:
        return json.dumps(self.to_dict(include_expressions=include_expressions), ensure_ascii=False,
                          sort_keys=True, separators=(',', ':'), allow_nan=False)
