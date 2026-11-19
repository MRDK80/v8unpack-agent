"""Assemble source-effect candidates, relations, and non-destructive diagnostics."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from v8unpack_agent._dynamic_form_attributes import _AttributeAnalysis
from v8unpack_agent.bsl_facts import _Failure
from v8unpack_agent.dynamic_forms import (
    ConditionKind,
    ConditionReference,
    DynamicFormOperation,
    DynamicOperationIndex,
    DynamicReason,
    EntityReference,
    OperationKind,
    OperationSource,
    PropertyKind,
    ResolvedValue,
    TargetKind,
    TargetOrigin,
)
from v8unpack_agent.dynamic_forms import (
    ResolutionStatus as R,
)
from v8unpack_agent.dynamic_forms_binding import (
    BindingDiagnostic,
    BindingReason,
    CandidateEffect,
    DynamicBindingIndex,
    DynamicCandidate,
    DynamicRelation,
    RelationKind,
    _least_resolution,
    candidate_id,
)
from v8unpack_agent.dynamic_forms_detector import _PreparedDynamicModule

_MAX_CANDIDATES = 20_000


def compatible(left: tuple[ConditionReference, ...], right: tuple[ConditionReference, ...]) -> bool:
    branches = {ref.condition_id:ref.branch for ref in left}
    return all(ref.condition_id not in branches or branches[ref.condition_id] == ref.branch for ref in right)


@dataclass
class _Draft:
    reference: EntityReference
    procedure: str
    operations: set[str] = field(default_factory=set)
    effects: set[CandidateEffect] = field(default_factory=set)
    relations: list[DynamicRelation] = field(default_factory=list)
    diagnostics: set[BindingDiagnostic] = field(default_factory=set)
    evidence: set[OperationSource] = field(default_factory=set)
    conditional: bool = False
    complete: bool = True
    resolution: R = R.EXACT


class _Assembler:
    def __init__(self, prepared: _PreparedDynamicModule, raw: DynamicOperationIndex,
                 bound: DynamicOperationIndex, attributes: _AttributeAnalysis,
                 opaque_calls: set[int], relation_targets: dict[tuple[str,str], EntityReference],
                 declared_elements: tuple[str,...], declared_commands: tuple[str,...],
                 declared_attributes: tuple[str,...]):
        self.prepared, self.raw, self.bound, self.attributes = prepared, raw, bound, attributes
        self.opaque_calls, self.relation_targets = opaque_calls, relation_targets
        self.operations = {op.operation_id:op for op in bound.operations}
        self.drafts: dict[str,_Draft] = {}
        self.owner: dict[str,str] = {}
        self.declared = {TargetKind.ELEMENT:frozenset(n.casefold() for n in declared_elements),
                         TargetKind.COMMAND:frozenset(n.casefold() for n in declared_commands),
                         TargetKind.ATTRIBUTE:frozenset(n.casefold() for n in declared_attributes)}
        self.global_diagnostics: set[BindingDiagnostic] = set()

    def draft(self, reference: EntityReference, procedure: str, evidence_key: str) -> tuple[str,_Draft]:
        creator = self.operations.get(reference.creation_operation_id or "")
        if creator is not None and creator.target.target_kind == reference.target_kind:
            reference = creator.target
        ident = candidate_id(self.prepared.snapshot.relative_path,self.prepared.snapshot.sha,procedure,
                             reference,unknown_operation_id=evidence_key)
        if ident not in self.drafts:
            if len(self.drafts) >= _MAX_CANDIDATES:
                raise _Failure('unsupported','candidate_budget')
            self.drafts[ident] = _Draft(reference,procedure,conditional=bool(reference.valid_in))
        return ident,self.drafts[ident]

    def attributes_for_path(self, op: DynamicFormOperation, value: ResolvedValue) -> EntityReference | ResolvedValue:
        if value.resolution != R.EXACT or not isinstance(value.value,str) or not value.value:
            return value
        name = value.value
        prior = []
        for record in self.attributes.records:
            creator = self.operations[record.operation_id]
            ref = record.reference
            if (creator.procedure.casefold() == op.procedure.casefold() and creator.source.end_offset <= op.source.start_offset
                    and ref.name is not None and ref.name.resolution == R.EXACT and isinstance(ref.name.value,str)
                    and ref.name.value.casefold() == name.casefold() and compatible(ref.valid_in,op.conditions)):
                prior.append(record)
        if len(prior) > 1:
            return EntityReference(TargetKind.ATTRIBUTE,TargetOrigin.UNKNOWN,R.UNRESOLVED,
                                   name=ResolvedValue(R.EXACT,name),reason=DynamicReason.CONFLICTING_BINDINGS)
        if prior:
            ref = prior[0].reference
            creator = self.operations[prior[0].operation_id]
            opaque = any(creator.source.end_offset <= offset < op.source.start_offset for offset in self.opaque_calls)
            unknown_later = any(self.operations[ident].procedure.casefold() == op.procedure.casefold()
                                and creator.source.end_offset <= self.operations[ident].source.start_offset < op.source.start_offset
                                for ident in self.attributes.unknown_batches)
            condition_kinds = {condition.condition_id:condition.kind for condition in self.bound.conditions}
            loop_carried = any(condition_kinds[c.condition_id] == ConditionKind.LOOP and c not in ref.valid_in
                               for c in op.conditions)
            if opaque or unknown_later or loop_carried:
                return EntityReference(ref.target_kind,ref.origin,R.UNRESOLVED,ref.creation_operation_id,
                                       ref.name,reason=DynamicReason.ALIAS_INVALIDATED)
            if any(c not in op.conditions for c in ref.valid_in):
                return EntityReference(ref.target_kind,ref.origin,R.UNRESOLVED,ref.creation_operation_id,
                                       ref.name,reason=DynamicReason.BRANCH_SCOPE)
            return ref
        proc = next(p for p in self.prepared.procedures if p.name.casefold() == op.procedure.casefold())
        condition_kinds = {condition.condition_id:condition.kind for condition in self.bound.conditions}
        uncertain = any(condition_kinds[c.condition_id] == ConditionKind.LOOP for c in op.conditions)
        uncertain |= any(proc.start <= offset < op.source.start_offset for offset in self.opaque_calls)
        uncertain = uncertain or any(self.operations[ident].procedure.casefold() == op.procedure.casefold()
                                    and self.operations[ident].source.start_offset < op.source.start_offset
                                    for ident in self.attributes.unknown_batches)
        if name.casefold() in self.declared[TargetKind.ATTRIBUTE]:
            return EntityReference(TargetKind.ATTRIBUTE,TargetOrigin.DECLARED,R.UNRESOLVED if uncertain else R.EXACT,
                                   name=ResolvedValue(R.EXACT,name),reason=DynamicReason.ALIAS_INVALIDATED if uncertain else None)
        return EntityReference(TargetKind.ATTRIBUTE,TargetOrigin.UNKNOWN,R.UNRESOLVED,
                               name=ResolvedValue(R.EXACT,name),reason=DynamicReason.RECEIVER_UNPROVEN)

    def relation(self, source: DynamicFormOperation, kind: RelationKind,
                 target: EntityReference | ResolvedValue) -> None:
        draft = self.drafts[self.owner[source.operation_id]]
        target_id = None
        if isinstance(target,EntityReference):
            resolution = target.resolution
            if target.creation_operation_id or target.origin in {TargetOrigin.DECLARED,TargetOrigin.FORM}:
                target_id,other = self.draft(target,source.procedure,source.operation_id+'/reference')
                if not other.operations:
                    other.effects.add(CandidateEffect.REFERENCE)
        elif kind == RelationKind.TYPE:
            resolution = target.resolution
        else:
            resolution = R.UNRESOLVED
        expected = {RelationKind.PARENT:TargetKind.ELEMENT,RelationKind.BEFORE:TargetKind.ELEMENT,
                    RelationKind.DATA_PATH:TargetKind.ATTRIBUTE,RelationKind.COMMAND:TargetKind.COMMAND}
        if isinstance(target,EntityReference) and kind in expected and target.target_kind != expected[kind]:
            resolution = R.UNRESOLVED
            draft.diagnostics.add(BindingDiagnostic(BindingReason.UNSUPPORTED_CONSTRUCT,(source.operation_id,)))
        if source.target.resolution != R.EXACT or 'unproven_form_property' in source.provenance:
            resolution = R.UNRESOLVED
        draft.relations.append(DynamicRelation(kind,source.operation_id,target,resolution,target_id))

    def seed(self) -> None:
        creations = {OperationKind.ELEMENT_ADD,OperationKind.ELEMENT_INSERT,OperationKind.COMMAND_ADD}
        deletes = {OperationKind.ELEMENT_DELETE,OperationKind.COMMAND_DELETE}
        for op in self.bound.operations:
            ident,draft = self.draft(op.target,op.procedure,op.operation_id)
            self.owner[op.operation_id] = ident
            draft.operations.add(op.operation_id)
            draft.effects.add(CandidateEffect.CREATE if op.operation_kind in creations else
                              CandidateEffect.DELETE if op.operation_kind in deletes else CandidateEffect.MODIFY)
            draft.conditional |= bool(op.conditions) or not op.condition_context_complete
            draft.complete &= op.condition_context_complete
        for record in self.attributes.records:
            op = self.operations[record.operation_id]
            _,draft = self.draft(record.reference,op.procedure,record.operation_id+'/attribute/'+record.evidence_key)
            draft.operations.add(record.operation_id)
            draft.effects.add(CandidateEffect.CREATE)
            draft.evidence.update(record.evidence)
            draft.conditional |= record.conditional
            draft.complete &= record.context_complete
            if record.reference.resolution != R.EXACT:
                draft.diagnostics.add(BindingDiagnostic(BindingReason.ATTRIBUTE_MEMBERSHIP_UNKNOWN,(record.operation_id,)))
        for ident in self.attributes.unknown_batches:
            self.drafts[self.owner[ident]].diagnostics.add(BindingDiagnostic(BindingReason.ATTRIBUTE_MEMBERSHIP_UNKNOWN,(ident,)))

    def relations(self) -> None:
        for op in self.bound.operations:
            for arg in op.arguments:
                kind = {'parent':RelationKind.PARENT,'before':RelationKind.BEFORE,'type':RelationKind.TYPE}.get(arg.name)
                if kind is None:
                    continue
                if isinstance(arg.value,ResolvedValue) and (arg.value.omitted or arg.value.resolution == R.EXACT and arg.value.value is None):
                    continue
                self.relation(op,kind,arg.value)
            if op.operation_kind != OperationKind.PROPERTY_SET or op.value is None:
                continue
            if op.property_kind == PropertyKind.DATA_PATH:
                if not (op.value.resolution == R.EXACT and op.value.value in (None,'')):
                    self.relation(op,RelationKind.DATA_PATH,self.attributes_for_path(op,op.value))
            elif op.property_kind == PropertyKind.COMMAND_NAME:
                if not (op.value.resolution == R.EXACT and op.value.value in (None,'')):
                    target = self.relation_targets.get((op.operation_id,'command'),op.value)
                    self.relation(op,RelationKind.COMMAND,target)
            elif op.property_kind == PropertyKind.PARENT:
                if not (op.value.resolution == R.EXACT and op.value.value is None):
                    target = self.relation_targets.get((op.operation_id,'parent'),op.value)
                    self.relation(op,RelationKind.PARENT,target)
            elif op.property_kind == PropertyKind.TYPE:
                self.relation(op,RelationKind.TYPE,op.value)

    def diagnostics(self) -> None:
        named: dict[tuple[str,TargetKind,str],list[tuple[str,DynamicFormOperation]]] = {}
        creates = {OperationKind.ELEMENT_ADD,OperationKind.ELEMENT_INSERT,OperationKind.COMMAND_ADD}
        deletes = {OperationKind.ELEMENT_DELETE,OperationKind.COMMAND_DELETE}
        for op in self.bound.operations:
            draft = self.drafts[self.owner[op.operation_id]]
            name = op.target.name
            if op.operation_kind in creates and name is not None and name.resolution == R.EXACT and isinstance(name.value,str):
                key = (op.procedure.casefold(),op.target.target_kind,name.value.casefold())
                named.setdefault(key,[]).append((self.owner[op.operation_id],op))
                if name.value.casefold() in self.declared.get(op.target.target_kind,frozenset()):
                    draft.diagnostics.add(BindingDiagnostic(BindingReason.NAME_COLLISION,(op.operation_id,)))
            if op.target.reason in {DynamicReason.ALIAS_INVALIDATED,DynamicReason.BRANCH_SCOPE,DynamicReason.CONFLICTING_BINDINGS}:
                code = {DynamicReason.ALIAS_INVALIDATED:BindingReason.ALIAS_INVALIDATED,
                        DynamicReason.BRANCH_SCOPE:BindingReason.BRANCH_SCOPE,
                        DynamicReason.CONFLICTING_BINDINGS:BindingReason.NAME_COLLISION}[op.target.reason]
                related = tuple(sorted({op.operation_id} | ({op.target.creation_operation_id} if op.target.creation_operation_id else set())))
                draft.diagnostics.add(BindingDiagnostic(code,related))
            if 'unsupported_call_arity' in op.provenance or 'unproven_form_property' in op.provenance:
                draft.diagnostics.add(BindingDiagnostic(BindingReason.UNSUPPORTED_CONSTRUCT,(op.operation_id,)))
        comparisons = 0
        for members in named.values():
            for i,(left_id,left) in enumerate(members):
                for j in range(len(members)):
                    if i == j:
                        continue
                    comparisons += 1
                    if comparisons > 100_000:
                        raise _Failure('unsupported','conflict_comparison_budget')
                    right_id,right = members[j]
                    if compatible(left.conditions,right.conditions):
                        diagnostic = BindingDiagnostic(BindingReason.NAME_COLLISION,tuple(sorted((left.operation_id,right.operation_id))))
                        self.drafts[left_id].diagnostics.add(diagnostic)
                        self.drafts[right_id].diagnostics.add(diagnostic)
                        break
        for draft in self.drafts.values():
            values: dict[PropertyKind,dict[tuple, list[str]]] = {}
            ordered = sorted((self.operations[ident] for ident in draft.operations),key=lambda op:op.source.start_offset)
            prior_deletes: list[DynamicFormOperation] = []
            for op in ordered:
                if prior_deletes:
                    for deleted in reversed(prior_deletes):
                        comparisons += 1
                        if comparisons > 100_000:
                            raise _Failure('unsupported','conflict_comparison_budget')
                        if compatible(deleted.conditions,op.conditions):
                            code = BindingReason.REPEATED_DELETE if op.operation_kind in deletes else BindingReason.DELETE_THEN_USE
                            draft.diagnostics.add(BindingDiagnostic(code,tuple(sorted((deleted.operation_id,op.operation_id)))))
                            break
                if op.operation_kind in deletes:
                    prior_deletes.append(op)
                if op.property_kind is not None and op.value is not None:
                    value = op.value
                    tag = ('number' if type(value.value) in (int,float) else type(value.value).__name__)
                    value_key = (value.resolution.value,tag,value.value) if value.resolution == R.EXACT else (value.resolution.value,value.expression)
                    values.setdefault(op.property_kind,{}).setdefault(value_key,[]).append(op.operation_id)
            for alternatives in values.values():
                if len(alternatives) > 1:
                    ids = tuple(sorted(ident for group in alternatives.values() for ident in group))
                    draft.diagnostics.add(BindingDiagnostic(BindingReason.MULTIPLE_VALUES,ids))


    def finish(self) -> DynamicBindingIndex:
        self.seed()
        self.relations()
        self.diagnostics()
        reverse: dict[str,set[str]] = {ident:set() for ident in self.drafts}
        parents: dict[str,set[str]] = {ident:set() for ident in self.drafts}
        for ident,draft in self.drafts.items():
            draft.resolution = _least_resolution([draft.reference.resolution]
                + [self.operations[op].operation_resolution for op in draft.operations]
                + [relation.resolution for relation in draft.relations])
            for relation in draft.relations:
                if relation.target_candidate_id is not None:
                    reverse[relation.target_candidate_id].add(ident)
                    if relation.kind == RelationKind.PARENT:
                        parents[ident].add(relation.target_candidate_id)
        # Iterative DFS: diagnose potential parent backedges without recursion.
        colors: dict[str,int] = {}
        cycle_work = 0
        for root, root_targets in parents.items():
            if root in colors:
                continue
            colors[root] = 1
            stack = [(root,iter(sorted(root_targets)))]
            while stack:
                ident,targets = stack[-1]
                target = next(targets,None)
                if target is None:
                    colors[ident] = 2
                    stack.pop()
                elif colors.get(target) == 1:
                    cycle_work += len(stack)
                    if cycle_work > 100_000:
                        raise _Failure('unsupported','cycle_comparison_budget')
                    start = next(i for i,(node,_) in enumerate(stack) if node == target)
                    for node,_ in stack[start:]:
                        draft = self.drafts[node]
                        draft.resolution = R.UNRESOLVED
                        draft.diagnostics.add(BindingDiagnostic(BindingReason.CYCLIC_RELATION,tuple(sorted(draft.operations))))
                elif target not in colors:
                    colors[target] = 1
                    stack.append((target,iter(sorted(parents[target]))))
        # Resolution can worsen at most twice per node. Propagate the least
        # certain constituent across the whole chain, without merging identity.
        pending = deque(self.drafts)
        queued = set(self.drafts)
        while pending:
            target = pending.popleft()
            queued.remove(target)
            for source in reverse[target]:
                draft = self.drafts[source]
                resolution = _least_resolution((draft.resolution,self.drafts[target].resolution))
                if resolution != draft.resolution:
                    draft.resolution = resolution
                    if source not in queued:
                        queued.add(source)
                        pending.append(source)
        candidates = []
        for ident,draft in self.drafts.items():
            effects = draft.effects - {CandidateEffect.REFERENCE}
            effect = (CandidateEffect.CREATE if CandidateEffect.CREATE in effects and CandidateEffect.DELETE not in effects
                      else next(iter(effects)) if len(effects) == 1
                      else CandidateEffect.MIXED if effects else CandidateEffect.REFERENCE)
            candidates.append(DynamicCandidate(ident,draft.procedure,draft.reference,effect,
                tuple(sorted(draft.operations)),draft.resolution,draft.conditional,draft.complete,
                tuple(sorted(draft.relations,key=lambda r:(r.source_operation_id,r.kind.value))),
                tuple(sorted(draft.diagnostics,key=lambda d:(d.reason.value,d.operation_ids))),
                tuple(sorted(draft.evidence,key=lambda e:(e.relative_path,e.start_offset,e.end_offset)))))
        return DynamicBindingIndex(self.raw,self.bound,tuple(candidates))
