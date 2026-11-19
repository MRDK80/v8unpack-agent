"""Immutable potential dynamic structure with per-operation provenance (#215).

Candidates are source effects, not a reconstructed runtime or declared tree.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from v8unpack_agent.dynamic_forms import (
    DynamicFormOperation,
    DynamicOperationIndex,
    EntityReference,
    OperationKind,
    OperationSource,
    ResolutionStatus,
    ResolvedValue,
    TargetKind,
    TargetOrigin,
)
from v8unpack_agent.modules import validate_relative_module_path

__all__ = ['BindingDiagnostic', 'BindingReason', 'CandidateEffect', 'DynamicBindingIndex', 'DynamicCandidate', 'DynamicRelation', 'RelationKind', 'bind_form_operations', 'candidate_id']


class RelationKind(str, Enum):
    PARENT = 'parent'
    BEFORE = 'before'
    DATA_PATH = 'data_path'
    COMMAND = 'command'
    TYPE = 'type'


class CandidateEffect(str, Enum):
    CREATE = 'create'
    MODIFY = 'modify'
    DELETE = 'delete'
    MIXED = 'mixed'
    REFERENCE = 'reference'
    UNKNOWN = 'unknown'


class BindingReason(str, Enum):
    NAME_COLLISION = 'name_collision'
    MULTIPLE_VALUES = 'multiple_values'
    DELETE_THEN_USE = 'delete_then_use'
    REPEATED_DELETE = 'repeated_delete'
    BRANCH_SCOPE = 'branch_scope'
    ALIAS_INVALIDATED = 'alias_invalidated'
    OPAQUE_CALL = 'opaque_call'
    CYCLIC_RELATION = 'cyclic_relation'
    ATTRIBUTE_MEMBERSHIP_UNKNOWN = 'attribute_membership_unknown'
    UNSUPPORTED_CONSTRUCT = 'unsupported_construct'


def _tuple(value: Any, cls: type) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, cls) for item in value):
        raise TypeError('expected immutable tuple of supported records')


def _ids(value: Any) -> None:
    _tuple(value, str)
    if any(not item for item in value) or len(set(value)) != len(value):
        raise ValueError('operation IDs must be nonempty and unique')


def _least_resolution(values: Iterable[ResolutionStatus]) -> ResolutionStatus:
    values = tuple(values)
    rank = {ResolutionStatus.EXACT: 0, ResolutionStatus.PARTIAL: 1, ResolutionStatus.UNRESOLVED: 2}
    return max(values, key=rank.__getitem__) if values else ResolutionStatus.UNRESOLVED


@dataclass(frozen=True)
class BindingDiagnostic:
    reason: BindingReason
    operation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reason, BindingReason):
            raise TypeError('reason requires BindingReason')
        _ids(self.operation_ids)
        object.__setattr__(self,'operation_ids',tuple(sorted(self.operation_ids)))

    def to_dict(self) -> dict[str, Any]:
        return {'reason': self.reason.value, 'operation_ids': list(self.operation_ids)}


@dataclass(frozen=True)
class DynamicRelation:
    kind: RelationKind
    source_operation_id: str
    target: EntityReference | ResolvedValue
    resolution: ResolutionStatus
    target_candidate_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RelationKind) or not isinstance(self.resolution, ResolutionStatus):
            raise TypeError('relation requires closed kind and resolution')
        if not isinstance(self.source_operation_id, str) or not self.source_operation_id:
            raise ValueError('relation requires source operation ID')
        if not isinstance(self.target, (EntityReference, ResolvedValue)):
            raise TypeError('relation target requires a reference or scalar expression')
        expected = {RelationKind.PARENT:TargetKind.ELEMENT,RelationKind.BEFORE:TargetKind.ELEMENT,
                    RelationKind.DATA_PATH:TargetKind.ATTRIBUTE,RelationKind.COMMAND:TargetKind.COMMAND}
        if isinstance(self.target,EntityReference) and self.kind in expected and self.target.target_kind != expected[self.kind] and self.resolution != ResolutionStatus.UNRESOLVED:
            raise ValueError('relation requires compatible target kind')
        if isinstance(self.target,ResolvedValue) and self.kind != RelationKind.TYPE and self.resolution != ResolutionStatus.UNRESOLVED:
            raise ValueError('unbound scalar relation must remain unresolved')
        if self.resolution == ResolutionStatus.EXACT and self.target.resolution != ResolutionStatus.EXACT:
            raise ValueError('exact relation requires exact target')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'kind': self.kind.value, 'source_operation_id': self.source_operation_id,
                'target': self.target.to_dict(include_expressions=include_expressions),
                'resolution': self.resolution.value, 'target_candidate_id': self.target_candidate_id}


@dataclass(frozen=True)
class DynamicCandidate:
    candidate_id: str
    procedure: str
    reference: EntityReference
    effect: CandidateEffect
    operation_ids: tuple[str, ...]
    resolution: ResolutionStatus
    conditional: bool
    condition_context_complete: bool
    relations: tuple[DynamicRelation, ...] = ()
    diagnostics: tuple[BindingDiagnostic, ...] = ()
    evidence_sources: tuple[OperationSource, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id,str) or not re.fullmatch(r'dcand_[0-9a-f]{64}',self.candidate_id) or not isinstance(self.procedure,str) or not self.procedure:
            raise ValueError('candidate requires ID and procedure')
        if not isinstance(self.reference, EntityReference) or not isinstance(self.resolution, ResolutionStatus):
            raise TypeError('candidate requires reference and resolution')
        if not isinstance(self.effect, CandidateEffect):
            raise TypeError('candidate requires closed effect')
        _ids(self.operation_ids)
        _tuple(self.relations, DynamicRelation)
        _tuple(self.diagnostics, BindingDiagnostic)
        _tuple(self.evidence_sources, OperationSource)
        object.__setattr__(self, 'operation_ids', tuple(sorted(self.operation_ids)))
        if len({(r.source_operation_id,r.kind) for r in self.relations}) != len(self.relations):
            raise ValueError('duplicate relation role for one operation')
        object.__setattr__(self,'relations',tuple(sorted(self.relations,key=lambda r:(r.source_operation_id,r.kind.value))))
        object.__setattr__(self,'diagnostics',tuple(sorted(self.diagnostics,key=lambda d:(d.reason.value,d.operation_ids))))
        object.__setattr__(self,'evidence_sources',tuple(sorted(self.evidence_sources,key=lambda e:(e.relative_path,e.snapshot_sha256,e.start_offset,e.end_offset))))
        if type(self.conditional) is not bool or type(self.condition_context_complete) is not bool:
            raise TypeError('condition summary requires bool values')
        if not self.operation_ids and (self.effect != CandidateEffect.REFERENCE or self.reference.origin not in {TargetOrigin.DECLARED,TargetOrigin.FORM}):
            raise ValueError('only declared or form reference candidates may have no own operations')
        if self.reference.origin == TargetOrigin.DECLARED and self.effect == CandidateEffect.CREATE:
            raise ValueError('declared candidate is not a dynamic creation')

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'id': self.candidate_id, 'procedure': self.procedure,
                'reference': self.reference.to_dict(include_expressions=include_expressions),
                'effect': self.effect.value, 'operation_ids': list(self.operation_ids),
                'resolution': self.resolution.value, 'conditional': self.conditional,
                'condition_context_complete': self.condition_context_complete,
                'relations': [r.to_dict(include_expressions=include_expressions) for r in self.relations],
                'diagnostics': [d.to_dict() for d in self.diagnostics],
                'evidence_sources': [source.to_dict() for source in self.evidence_sources]}


def candidate_id(relative_path: str, sha: str, procedure: str, reference: EntityReference, *, unknown_operation_id: str | None = None) -> str:
    validate_relative_module_path(relative_path)
    if not re.fullmatch(r'[0-9a-f]{64}',sha) or not procedure:
        raise ValueError('candidate requires snapshot SHA and procedure')
    if not isinstance(reference,EntityReference):
        raise TypeError('candidate requires EntityReference')
    identity: list[Any]
    if reference.resolution != ResolutionStatus.EXACT and unknown_operation_id:
        identity = ['unresolved', reference.target_kind.value, unknown_operation_id]
    elif reference.creation_operation_id:
        identity = ['dynamic', reference.target_kind.value, reference.creation_operation_id,
                    reference.name.value if reference.target_kind.value == 'attribute' and reference.name else None]
    elif reference.origin == TargetOrigin.FORM:
        identity = ['form']
    elif reference.origin == TargetOrigin.DECLARED and reference.name and reference.name.resolution == ResolutionStatus.EXACT:
        if not isinstance(reference.name.value,str) or not reference.name.value:
            raise ValueError('declared identity requires a nonempty string name')
        identity = ['declared', reference.target_kind.value, reference.name.value.casefold()]
    elif unknown_operation_id:
        identity = ['unknown', reference.target_kind.value, unknown_operation_id]
    else:
        raise ValueError('unknown candidate requires a source operation')
    raw = json.dumps([relative_path, sha, procedure.casefold(), identity], sort_keys=True, ensure_ascii=False, separators=(',',':'))
    return 'dcand_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class DynamicBindingIndex:
    raw: DynamicOperationIndex
    bound: DynamicOperationIndex
    candidates: tuple[DynamicCandidate, ...]
    diagnostics: tuple[BindingDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.raw, DynamicOperationIndex) or not isinstance(self.bound, DynamicOperationIndex):
            raise TypeError('binding index requires raw and bound operation indexes')
        _tuple(self.candidates, DynamicCandidate)
        _tuple(self.diagnostics, BindingDiagnostic)
        raw_ids = {op.operation_id for op in self.raw.operations}
        ops = {op.operation_id: op for op in self.bound.operations}
        if not raw_ids <= ops.keys():
            raise ValueError('bound index must retain raw operation IDs')
        for raw_op in self.raw.operations:
            bound_op = ops[raw_op.operation_id]
            if (raw_op.source != bound_op.source or raw_op.procedure.casefold() != bound_op.procedure.casefold()
                    or raw_op.conditions != bound_op.conditions or raw_op.condition_context_complete != bound_op.condition_context_complete):
                raise ValueError('bound operation must preserve raw source context')
        bound_conditions = {c.condition_id:c for c in self.bound.conditions}
        if any(bound_conditions.get(c.condition_id) != c for c in self.raw.conditions):
            raise ValueError('bound index must preserve raw conditions')
        candidates_by_id = {c.candidate_id:c for c in self.candidates}
        ids = set(candidates_by_id)
        if len(ids) != len(self.candidates):
            raise ValueError('duplicate candidate ID')
        creation_kinds = {OperationKind.ELEMENT_ADD:TargetKind.ELEMENT,
                          OperationKind.ELEMENT_INSERT:TargetKind.ELEMENT,
                          OperationKind.COMMAND_ADD:TargetKind.COMMAND,
                          OperationKind.ATTRIBUTES_CHANGE:TargetKind.ATTRIBUTE}

        def check_ref(ref: EntityReference, source: DynamicFormOperation) -> None:
            if any(condition not in source.conditions for condition in ref.valid_in):
                raise ValueError('reference escapes its branch context')
            if ref.creation_operation_id is None:
                return
            creator = ops.get(ref.creation_operation_id)
            if creator is None or creation_kinds.get(creator.operation_kind) != ref.target_kind:
                raise ValueError('reference requires creation of same target kind')
            if creator.procedure.casefold() != source.procedure.casefold():
                raise ValueError('reference crosses procedure contexts')
            if (creator.source.relative_path,creator.source.snapshot_sha256) != (source.source.relative_path,source.source.snapshot_sha256):
                raise ValueError('reference crosses source snapshots')
            if creator.source.start_offset > source.source.start_offset:
                raise ValueError('reference precedes creation')
            if ref.target_kind == TargetKind.ATTRIBUTE and (ref.name is None or ref.name.resolution != ResolutionStatus.EXACT or not isinstance(ref.name.value,str) or not ref.name.value):
                raise ValueError('batch attribute reference requires exact name')
            if ref.resolution == ResolutionStatus.EXACT and (creator.operation_resolution == ResolutionStatus.UNRESOLVED or any(c not in ref.valid_in for c in creator.conditions)):
                raise ValueError('exact reference requires proven branch-scoped creation')

        for diagnostic in self.diagnostics:
            if not set(diagnostic.operation_ids) <= ops.keys():
                raise ValueError('dangling diagnostic operation ID')
        for candidate in self.candidates:
            if not set(candidate.operation_ids) <= ops.keys():
                raise ValueError('dangling candidate operation ID')
            if candidate.reference.creation_operation_id is not None:
                creator = ops.get(candidate.reference.creation_operation_id)
                if creator is None:
                    raise ValueError('dangling candidate creation')
                check_ref(candidate.reference,creator)
            for diagnostic in candidate.diagnostics:
                if not set(diagnostic.operation_ids) <= ops.keys():
                    raise ValueError('dangling diagnostic operation ID')
            for evidence in candidate.evidence_sources:
                if not any((evidence.relative_path,evidence.snapshot_sha256) == (ops[ident].source.relative_path,ops[ident].source.snapshot_sha256) for ident in candidate.operation_ids):
                    raise ValueError('evidence crosses candidate snapshots')
            if any(ops[ident].conditions or not ops[ident].condition_context_complete for ident in candidate.operation_ids) and not candidate.conditional:
                raise ValueError('conditional operations require conditional candidate')
            if any(not ops[ident].condition_context_complete for ident in candidate.operation_ids) and candidate.condition_context_complete:
                raise ValueError('candidate cannot upgrade incomplete condition context')
            constituents = [candidate.reference.resolution]
            for op_id in candidate.operation_ids:
                op = ops[op_id]
                if op.procedure.casefold() != candidate.procedure.casefold():
                    raise ValueError('candidate crosses procedure contexts')
                constituents.append(op.operation_resolution)
            for relation in candidate.relations:
                if relation.source_operation_id not in candidate.operation_ids:
                    raise ValueError('relation must originate in candidate operations')
                if relation.target_candidate_id is not None and relation.target_candidate_id not in ids:
                    raise ValueError('dangling relation candidate ID')
                source = ops[relation.source_operation_id]
                if isinstance(relation.target,EntityReference):
                    check_ref(relation.target,source)
                if relation.target_candidate_id is not None:
                    target = candidates_by_id[relation.target_candidate_id]
                    if target.procedure.casefold() != candidate.procedure.casefold():
                        raise ValueError('relation crosses procedure contexts')
                    if not isinstance(relation.target,EntityReference):
                        raise ValueError('candidate link requires an entity reference')
                    linked = relation.target
                    root = target.reference
                    if linked.creation_operation_id != root.creation_operation_id or linked.target_kind != root.target_kind:
                        raise ValueError('candidate link changes target identity')
                    if linked.creation_operation_id is None and (linked.origin != root.origin or linked.name != root.name):
                        raise ValueError('candidate link changes declared identity')
                    constituents.append(target.resolution)
                constituents.append(relation.resolution)
            if _least_resolution([candidate.resolution, _least_resolution(constituents)]) != candidate.resolution:
                raise ValueError('chain cannot upgrade nonexact constituents')
        object.__setattr__(self, 'candidates', tuple(sorted(self.candidates, key=lambda c: c.candidate_id)))

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {'schema': 'dynamic_form_bindings/1', 'raw': self.raw.to_dict(include_expressions=include_expressions),
                'bound': self.bound.to_dict(include_expressions=include_expressions),
                'candidates': [c.to_dict(include_expressions=include_expressions) for c in self.candidates],
                'diagnostics': [d.to_dict() for d in self.diagnostics],
                'runtime_tree': False}

    def to_json(self, *, include_expressions: bool = False) -> str:
        return json.dumps(self.to_dict(include_expressions=include_expressions), ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False)


def bind_form_operations(
    text: str, relative_path: str, *,
    declared_elements: tuple[str, ...] = (),
    declared_commands: tuple[str, ...] = (),
    declared_attributes: tuple[str, ...] = (),
    max_chars: int = 2_000_000,
) -> DynamicBindingIndex:
    """Bind only proven local identities; preserve raw records and uncertainty."""
    from v8unpack_agent._dynamic_forms_binding import _bind_form_operations
    from v8unpack_agent.bsl_facts import _Failure
    from v8unpack_agent.dynamic_forms_detector import (
        DynamicAnalysisError,
        _validate_dynamic_input,
    )

    _validate_dynamic_input(text, relative_path, declared_elements, declared_commands, max_chars,
                            declared_attributes=declared_attributes)
    try:
        return _bind_form_operations(text, relative_path, declared_elements=declared_elements,
                                     declared_commands=declared_commands, declared_attributes=declared_attributes)
    except _Failure as exc:
        raise DynamicAnalysisError(exc.status, exc.reason) from None
    except UnicodeError:
        raise DynamicAnalysisError('unsupported', 'invalid_unicode') from None
