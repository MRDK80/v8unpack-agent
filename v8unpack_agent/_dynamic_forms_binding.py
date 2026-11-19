"""Bounded forward-local identity proofs; no interprocedural evaluator."""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, replace

from v8unpack_agent.bsl_facts import _Failure, _Token
from v8unpack_agent.dynamic_forms import (
    ConditionReference,
    DynamicFormOperation,
    DynamicOperationIndex,
    DynamicReason,
    EntityReference,
    OperationKind,
    PropertyKind,
    ResolvedValue,
    TargetKind,
    TargetOrigin,
    operation_id,
)
from v8unpack_agent.dynamic_forms import (
    ResolutionStatus as R,
)
from v8unpack_agent.dynamic_forms_binding import DynamicBindingIndex
from v8unpack_agent.dynamic_forms_detector import (
    _BASE_FORM_PROPERTIES,
    _COLLECTIONS,
    _METHODS,
    _PROPERTIES,
    _SELF,
    _STATEMENT_END,
    _arguments,
    _detect,
    _matching,
    _prepare_dynamic_module,
    _PreparedDynamicModule,
    _Procedure,
    _symbol,
    _word,
)

Context = tuple[ConditionReference, ...]
Proofs = dict[tuple[int,int], EntityReference | TargetKind]
RelationTargets = dict[tuple[str,str], EntityReference]
Diagnostics = list[tuple[str,int]]

_MAX_PENDING = 10_000
_MAX_SYMBOLS = 10_000
_MAX_PROOFS = 100_000
_MAX_BOUND_OPERATIONS = 10_000


@dataclass(frozen=True)
class Binding:
    value: EntityReference | TargetKind
    conditions: Context
    alive: bool = True


def receiver_proofs(text: str, path: str, raw: DynamicOperationIndex, *,
    declared_elements: tuple[str,...] = (), declared_commands: tuple[str,...] = (),
    declared_attributes: tuple[str,...] = (), prepared: _PreparedDynamicModule | None = None,
    extra_operations: dict[str,DynamicFormOperation] | None = None,
    native_calls: frozenset[int] = frozenset(), opaque_calls: set[int] | None = None,
    relation_targets: RelationTargets | None = None,
) -> tuple[Proofs,tuple[tuple[str,int],...]]:
    if prepared is None:
        prepared = _prepare_dynamic_module(text, path)
    snapshot = prepared.snapshot
    if extra_operations is None:
        extra_operations = {}
    if opaque_calls is None:
        opaque_calls = set()
    if relation_targets is None:
        relation_targets = {}
    for op in raw.operations:
        if (op.source.relative_path, op.source.snapshot_sha256) != (path, snapshot.sha):
            raise ValueError('raw source snapshot mismatch')
    for condition in raw.conditions:
        if (condition.source.relative_path, condition.source.snapshot_sha256) != (path, snapshot.sha):
            raise ValueError('raw condition snapshot mismatch')
    procs = prepared.procedures
    proofs: Proofs = {}
    diagnostics: Diagnostics = []
    creators = {op.source.start_offset: op for op in raw.operations if op.operation_kind in {
        OperationKind.ELEMENT_ADD, OperationKind.ELEMENT_INSERT, OperationKind.COMMAND_ADD}}
    declared = {TargetKind.ELEMENT: frozenset(n.casefold() for n in declared_elements),
                TargetKind.COMMAND: frozenset(n.casefold() for n in declared_commands)}

    form_attributes = frozenset(n.casefold() for n in declared_attributes)
    creation_offsets = {op.operation_id: op.source.start_offset for op in creators.values()}
    for proc in procs:
        _procedure_proofs(proc, prepared, creators, creation_offsets, declared, proofs, diagnostics, extra_operations, native_calls, opaque_calls, relation_targets, form_attributes)
    return proofs, tuple(diagnostics)


def _procedure_proofs(proc: _Procedure, prepared: _PreparedDynamicModule,
    creators: dict[int,DynamicFormOperation], creation_offsets: dict[str,int],
    declared: dict[TargetKind,frozenset[str]], proofs: Proofs, diagnostics: Diagnostics,
    extra_operations: dict[str,DynamicFormOperation], native_calls: frozenset[int],
    opaque_calls: set[int], relation_targets: RelationTargets, form_attributes: frozenset[str],
) -> None:
    module_vars, procedure_names = prepared.module_bindings, prepared.procedure_names
    contexts = prepared.contexts
    snapshot = prepared.snapshot
    symbols: dict[str,Binding] = {}
    excluded_symbols = module_vars | frozenset(name.casefold() for name in proc.parameters)
    shadowed = module_vars | proc.shadowed | form_attributes
    pending: list[tuple[int,int,str,str | None,Binding | EntityReference | None]] = []
    serial = itertools.count()

    def enqueue(position: int, action: str, key: str | None, value: Binding | EntityReference | None) -> None:
        if len(pending) >= _MAX_PENDING:
            raise _Failure('unsupported', 'binding_event_budget')
        heapq.heappush(pending, (position, next(serial), action, key, value))

    def record_proof(start: int, end: int, value: EntityReference | TargetKind) -> None:
        if (start,end) not in proofs and len(proofs) >= _MAX_PROOFS:
            raise _Failure('unsupported', 'binding_proof_budget')
        proofs[start,end] = value
    completed: set[str] = set()
    dynamic_names: dict[tuple[TargetKind,str],list[Binding]] = {}
    invalid_names: set[tuple[TargetKind,str]] = set()

    def usable(binding: Binding, context: Context, complete: bool) -> bool:
        return binding.alive and complete and all(ref in context for ref in binding.conditions)

    def value_for_symbol(name: str, context: Context, complete: bool) -> EntityReference | TargetKind | None:
        if name.casefold() in excluded_symbols:
            return None
        binding = symbols.get(name.casefold())
        if binding is None:
            return None
        if usable(binding, context, complete):
            if isinstance(binding.value, EntityReference):
                conditions = tuple(dict.fromkeys(binding.value.valid_in + binding.conditions))
                return replace(binding.value, valid_in=conditions)
            return binding.value
        if isinstance(binding.value, EntityReference):
            return replace(binding.value, resolution=R.UNRESOLVED, valid_in=(),
                           reason=DynamicReason.ALIAS_INVALIDATED if not binding.alive else DynamicReason.BRANCH_SCOPE)
        return None

    def collection(expr: tuple[_Token,...], context: Context, complete: bool) -> TargetKind | None:
        if len(expr) == 1:
            symbol = value_for_symbol(expr[0].value, context, complete)
            if isinstance(symbol, TargetKind):
                return symbol
            name = _word(expr[0])
            if name in _COLLECTIONS and name not in shadowed:
                return _COLLECTIONS[name]
        if len(expr) == 3 and _symbol(expr[1], '.'):
            form = entity(expr[:1], context, complete)
            if form is not None and form.target_kind == TargetKind.FORM and form.resolution == R.EXACT:
                return _COLLECTIONS.get(_word(expr[2])) if _word(expr[2]) not in form_attributes else None
        return None

    def named(kind: TargetKind, name: str, context: Context, complete: bool) -> EntityReference:
        literal = ResolvedValue(R.EXACT, name)
        key = (kind, name.casefold())
        branches = {ref.condition_id:ref.branch for ref in context}
        candidates = [binding for binding in dynamic_names.get(key, [])
                      if all(ref.condition_id not in branches or branches[ref.condition_id] == ref.branch
                             for ref in binding.conditions)]
        if len(candidates) > 1 or candidates and name.casefold() in declared[kind]:
            return EntityReference(kind, TargetOrigin.UNKNOWN, R.UNRESOLVED, name=literal,
                                   reason=DynamicReason.CONFLICTING_BINDINGS)
        if key in invalid_names:
            if len(candidates) == 1:
                assert isinstance(candidates[0].value,EntityReference)
                return replace(candidates[0].value,resolution=R.UNRESOLVED,valid_in=(),reason=DynamicReason.ALIAS_INVALIDATED)
            if name.casefold() in declared[kind]:
                return EntityReference(kind,TargetOrigin.DECLARED,R.UNRESOLVED,name=literal,reason=DynamicReason.ALIAS_INVALIDATED)
            return EntityReference(kind,TargetOrigin.UNKNOWN,R.UNRESOLVED,name=literal,reason=DynamicReason.ALIAS_INVALIDATED)
        if candidates:
            binding = candidates[0]
            assert isinstance(binding.value,EntityReference)
            if usable(binding, context, complete):
                return binding.value
            return replace(binding.value, resolution=R.UNRESOLVED, valid_in=(), reason=DynamicReason.BRANCH_SCOPE)
        if name.casefold() in declared[kind]:
            return EntityReference(kind, TargetOrigin.DECLARED, R.EXACT, name=literal)
        return EntityReference(kind, TargetOrigin.UNKNOWN, R.UNRESOLVED, name=literal,
                               reason=DynamicReason.RECEIVER_UNPROVEN)

    def entity(expr: tuple[_Token,...], context: Context, complete: bool) -> EntityReference | None:
        if not expr:
            return None
        if len(expr) == 1 and expr[0].kind == 'word':
            symbol = value_for_symbol(expr[0].value, context, complete)
            if isinstance(symbol, EntityReference):
                return symbol
            name = _word(expr[0])
            if name in _SELF and name not in shadowed:
                return EntityReference(TargetKind.FORM, TargetOrigin.FORM, R.EXACT)
        if len(expr) >= 3 and _symbol(expr[-2], '.') and expr[-1].kind == 'word':
            kind = collection(expr[:-2], context, complete)
            if kind is not None:
                return named(kind, expr[-1].value, context, complete)
        if len(expr) >= 4 and _symbol(expr[-1], ']'):
            for at, token in enumerate(expr):
                if _symbol(token, '[') and at and _matching(expr,at) == len(expr)-1:
                    kind = collection(expr[:at], context, complete)
                    if kind is not None:
                        index = expr[at+1:-1]
                        if len(index) == 1 and index[0].kind == 'string':
                            return named(kind, index[0].value, context, complete)
                        if index:
                            return EntityReference(kind,TargetOrigin.UNKNOWN,R.UNRESOLVED,
                                                   name=snapshot.value(index),reason=DynamicReason.COMPUTED_VALUE)
        for at, token in enumerate(expr):
            if at >= 2 and _word(token) in {'найти','find'} and _symbol(expr[at-1], '.') and at+1 < len(expr) and _symbol(expr[at+1], '('):
                args, end = _arguments(expr, at+1)
                if end == len(expr)-1 and len(args) == 1 and args[0]:
                    kind = collection(expr[:at-1], context, complete)
                    if kind is not None:
                        if len(args[0]) == 1 and args[0][0].kind == 'string':
                            return named(kind, args[0][0].value, context, complete)
                        return EntityReference(kind,TargetOrigin.UNKNOWN,R.UNRESOLVED,
                                               name=snapshot.value(args[0]),reason=DynamicReason.COMPUTED_VALUE)
        if expr[0].start in creators:
            op = creators[expr[0].start]
            if expr[-1].end == op.source.end_offset and op.target.resolution == R.EXACT:
                return op.target
        return None

    def same_identity(left: EntityReference, right: EntityReference) -> bool:
        if left.target_kind != right.target_kind:
            return False
        if left.creation_operation_id or right.creation_operation_id:
            return bool(left.creation_operation_id and left.creation_operation_id == right.creation_operation_id)
        return (left.origin == right.origin == TargetOrigin.DECLARED and left.name is not None and right.name is not None
                and left.name.resolution == right.name.resolution == R.EXACT and isinstance(left.name.value,str) and isinstance(right.name.value,str)
                and left.name.value.casefold() == right.name.value.casefold())

    def apply_pending(position: int) -> None:
        while pending and pending[0][0] <= position:
            _, _, action, key, binding = heapq.heappop(pending)
            if action == 'assign':
                assert key is not None and isinstance(binding,Binding)
                if key not in symbols and len(symbols) >= _MAX_SYMBOLS:
                    raise _Failure('unsupported', 'binding_symbol_budget')
                symbols[key] = binding
            elif action == 'create':
                assert isinstance(binding,Binding) and isinstance(binding.value,EntityReference)
                ref = binding.value
                if ref.name is not None and ref.name.resolution == R.EXACT and isinstance(ref.name.value, str):
                    name_key = (ref.target_kind, ref.name.value.casefold())
                    dynamic_names.setdefault(name_key, []).append(binding)
            elif action == 'delete':
                assert isinstance(binding,EntityReference)
                deleted = binding
                for symbol_name, previous in tuple(symbols.items()):
                    value = previous.value
                    if isinstance(value, EntityReference) and same_identity(value, deleted):
                        symbols[symbol_name] = replace(previous, alive=False)
                if deleted.name is not None and deleted.name.resolution == R.EXACT and isinstance(deleted.name.value,str):
                    invalid_names.add((deleted.target_kind, deleted.name.value.casefold()))
            elif action == 'opaque_names':
                invalid_names.update(dynamic_names)
                for declared_kind, names in declared.items():
                    invalid_names.update((declared_kind,name) for name in names)
                for symbol_name, previous in tuple(symbols.items()):
                    if isinstance(previous.value,EntityReference) and previous.value.target_kind != TargetKind.FORM:
                        symbols[symbol_name] = replace(previous,alive=False)
            elif action == 'namespace':
                assert key is not None
                deleted_kind = TargetKind(key)
                invalid_names.update(name_key for name_key in dynamic_names if name_key[0] == deleted_kind)
                invalid_names.update((deleted_kind,name) for name in declared[deleted_kind])
                for symbol_name, previous in tuple(symbols.items()):
                    if isinstance(previous.value,EntityReference) and previous.value.target_kind == deleted_kind:
                        symbols[symbol_name] = replace(previous,alive=False)
            elif action == 'invalidate':
                assert key is not None
                old = symbols.get(key)
                if old is not None:
                    symbols[key] = replace(old, alive=False)

    body = proc.body
    setter_sites = _setter_sites(body)
    for i, token in enumerate(body):
        apply_pending(token.start)
        context, complete = contexts[token.start]
        if _word(token) in {'для', 'for', 'пока', 'while'}:
            # No fixed-point proof of loop-carried locals in the MVP. Fresh
            # definitions inside the body may establish identity again.
            for name, binding in tuple(symbols.items()):
                symbols[name] = replace(binding, alive=False)
            invalid_names.update(dynamic_names)
            for declared_kind, names in declared.items():
                invalid_names.update((declared_kind, name) for name in names)
        if token.kind == 'word':
            symbol = value_for_symbol(token.value, context, complete)
            if symbol is not None:
                record_proof(token.start,token.end,symbol)
        site = setter_sites.get(token.start)
        if site is not None:
            receiver, value_tokens, property_kind = site
            ref = entity(receiver,context,complete)
            if ref is not None:
                definition = creation_offsets.get(ref.creation_operation_id or "")
                if definition is None or definition <= token.start:
                    record_proof(receiver[0].start,receiver[-1].end,ref)
                    source = snapshot.source(token.start,value_tokens[-1].end)
                    value = snapshot.value(value_tokens)
                    resolution = value.resolution if ref.resolution == R.EXACT else R.UNRESOLVED
                    if ref.target_kind == TargetKind.FORM and (property_kind not in _BASE_FORM_PROPERTIES or _word(body[i+len(receiver)+1]) in form_attributes):
                        resolution = R.UNRESOLVED
                    extra = DynamicFormOperation(operation_id(source,OperationKind.PROPERTY_SET),OperationKind.PROPERTY_SET,
                                                 source,proc.name,ref,resolution,property_kind=property_kind,value=value,
                                                 conditions=context,condition_context_complete=complete,
                                                 provenance=('local_binding_analysis','complex_receiver'))
                    if extra.operation_id not in extra_operations and len(extra_operations) >= _MAX_BOUND_OPERATIONS:
                        raise _Failure('unsupported','binding_operation_budget')
                    extra_operations[extra.operation_id] = extra
                    if property_kind == PropertyKind.COMMAND_NAME and value.resolution == R.EXACT and isinstance(value.value,str) and value.value:
                        relation_targets[extra.operation_id,'command'] = named(TargetKind.COMMAND,value.value,context,complete)
                    elif property_kind == PropertyKind.PARENT:
                        parent = entity(value_tokens,context,complete)
                        if parent is not None:
                            parent_definition = creation_offsets.get(parent.creation_operation_id or "")
                            if parent_definition is None or parent_definition <= token.start:
                                relation_targets[extra.operation_id,'parent'] = parent

        if token.kind == 'word' and i+1 < len(body) and _symbol(body[i+1], '('):
            args, call_end = _arguments(body,i+1)
            chain_start = i
            while chain_start >= 2 and _symbol(body[chain_start-1], '.') and body[chain_start-2].kind == 'word':
                chain_start -= 2
            receiver = body[chain_start:i-1] if chain_start < i else ()
            kind = collection(receiver,context,complete) if receiver else None
            method = _METHODS.get(_word(token))
            consumer_start = body[chain_start].start
            for arg in args:
                ref = entity(arg,context,complete)
                if ref is not None:
                    definition = creation_offsets.get(ref.creation_operation_id or "")
                    if definition is None or definition <= consumer_start:
                        record_proof(arg[0].start,arg[-1].end,ref)
            known_collection_call = kind is not None and method in {'add','insert','move','delete'}
            known_lookup_call = kind is not None and _word(token) in {'найти','find'}
            known_native_call = token.start in native_calls
            element_receiver = entity(receiver,context,complete) if receiver else None
            known_registration = (element_receiver is not None and element_receiver.target_kind == TargetKind.ELEMENT
                                  and element_receiver.resolution == R.EXACT and _word(token) in {'установитьдействие','setaction'})
            known_constructor = (i > 0 and _word(body[i-1]) in {'новый','new'}
                                 and _word(token) in {'массив','array','реквизитформы','formattribute','описаниетипов','typedescription'})
            known_type_call = chain_start == i and _word(token) in {'тип','type'} and _word(token) not in shadowed | procedure_names
            known_attribute_call = _word(token) in {'изменитьреквизиты','changeattributes'} and (
                not receiver and _word(token) not in shadowed | procedure_names or
                receiver and (form := entity(receiver,context,complete)) is not None and form.target_kind == TargetKind.FORM and form.resolution == R.EXACT)
            if known_collection_call and method == 'delete' and len(args) == 1:
                deleted = entity(args[0],context,complete)
                if deleted is not None and deleted.resolution == R.EXACT and deleted.target_kind == kind:
                    enqueue(body[call_end].end,'delete',None,deleted)
                else:
                    # Unknown delete target may alias any item in this collection.
                    assert kind is not None
                    enqueue(body[call_end].end,'namespace',kind.value,None)
            elif not (known_collection_call or known_lookup_call or known_type_call or known_attribute_call or known_native_call or known_registration or known_constructor):
                opaque_calls.add(token.start)
                for arg in args:
                    if len(arg) == 1 and arg[0].kind == 'word' and _word(arg[0]) in symbols:
                        enqueue(body[call_end].end,'invalidate',_word(arg[0]),None)
                        diagnostics.append(('opaque_call',token.start))
                enqueue(body[call_end].end,'opaque_names',None,None)
        creator = creators.get(token.start)
        if creator is not None and creator.operation_id not in completed and creator.target.resolution == R.EXACT:
            completed.add(creator.operation_id)
            enqueue(creator.source.end_offset,'create',None,Binding(creator.target,creator.conditions))
        if (token.kind == 'word' and _word(token) not in excluded_symbols and i+1 < len(body) and _symbol(body[i+1], '=') and
            (i == 0 or _symbol(body[i-1], ';') or _symbol(body[i-1], ':') or _word(body[i-1]) in {'тогда','then','иначе','else','цикл','do','попытка','try','исключение','except'})):
            j = i+2
            while j < len(body) and not _symbol(body[j], ';') and _word(body[j]) not in _STATEMENT_END:
                if body[j].kind == 'symbol' and body[j].value in {'(', '['}:
                    j = _matching(body,j)+1
                else:
                    j += 1
            rhs = body[i+2:j]
            resolved: EntityReference | TargetKind | None = entity(rhs,context,complete)
            if resolved is None:
                resolved = collection(rhs,context,complete)
            if resolved is not None:
                enqueue(rhs[-1].end,'assign',_word(token),Binding(resolved,context))
            elif _word(token) in symbols:
                enqueue(rhs[-1].end if rhs else token.end,'invalidate',_word(token),None)


def _bind_form_operations(text: str, relative_path: str, *, declared_elements: tuple[str,...] = (),
                         declared_commands: tuple[str,...] = (), declared_attributes: tuple[str,...] = ()) -> DynamicBindingIndex:
    from v8unpack_agent._dynamic_form_attributes import analyze_attribute_arrays
    from v8unpack_agent._dynamic_form_candidates import _Assembler

    prepared = _prepare_dynamic_module(text, relative_path)
    raw = _detect(text, relative_path, declared_elements=declared_elements,
                  declared_commands=declared_commands, declared_attributes=declared_attributes, prepared=prepared)
    bound = raw
    for _ in range(2):
        extras: dict[str,DynamicFormOperation] = {}
        opaque_calls: set[int] = set()
        relation_targets: RelationTargets = {}
        attributes = analyze_attribute_arrays(prepared,bound)
        proofs, _ = receiver_proofs(text, relative_path, bound, declared_elements=declared_elements,
                                    declared_commands=declared_commands, declared_attributes=declared_attributes, prepared=prepared, extra_operations=extras, native_calls=attributes.native_calls,
                                    opaque_calls=opaque_calls,relation_targets=relation_targets)
        bound = _detect(text, relative_path, declared_elements=declared_elements,
                        declared_commands=declared_commands, declared_attributes=declared_attributes, prepared=prepared, receiver_proofs=proofs)
        merged = {op.operation_id:op for op in bound.operations}
        for ident,extra in extras.items():
            merged.setdefault(ident,extra)
        if len(merged) > _MAX_BOUND_OPERATIONS:
            raise _Failure('unsupported','binding_operation_budget')
        bound = DynamicOperationIndex(tuple(merged.values()),prepared.conditions)
    attributes = analyze_attribute_arrays(prepared,bound)
    return _Assembler(prepared,raw,bound,attributes,opaque_calls,relation_targets,
                      declared_elements,declared_commands,declared_attributes).finish()


def _setter_sites(body: tuple[_Token,...]) -> dict[int,tuple[tuple[_Token,...],tuple[_Token,...],PropertyKind]]:
    pairs = {}
    stack = []
    for i,token in enumerate(body):
        if token.kind != 'symbol':
            continue
        if token.value in {'(', '['}:
            stack.append(i)
        elif token.value in {')', ']'}:
            if not stack or (body[stack[-1]].value,token.value) not in {('(',')'),('[',']')}:
                raise _Failure('incomplete','unbalanced_binding_delimiter')
            pairs[i] = stack.pop()
    if stack:
        raise _Failure('incomplete','unclosed_binding_delimiter')
    sites = {}
    for i,token in enumerate(body):
        if not _symbol(token,'=') or i < 3 or not _symbol(body[i-2],'.') or body[i-1].kind != 'word':
            continue
        prop = _PROPERTIES.get(_word(body[i-1]))
        if prop is None:
            continue
        start = i-3
        while start >= 0:
            if body[start].kind == 'symbol' and body[start].value in {')',']'}:
                start = pairs[start]-1
            if start < 0 or body[start].kind != 'word':
                start = -1
                break
            if start >= 2 and _symbol(body[start-1],'.'):
                start -= 2
            else:
                break
        if start < 0:
            continue
        boundary = start == 0 or _symbol(body[start-1],';') or _symbol(body[start-1],':') or _word(body[start-1]) in {'тогда','then','иначе','else','цикл','do','попытка','try','исключение','except'}
        if not boundary:
            continue
        end = i+1
        while end < len(body) and not _symbol(body[end],';') and _word(body[end]) not in _STATEMENT_END:
            if body[end].kind == 'symbol' and body[end].value in {'(', '['}:
                end = _matching(body,end)+1
            else:
                end += 1
        if end == i+1:
            raise _Failure('incomplete','empty_binding_setter_value')
        sites[body[start].start] = (body[start:i-2],body[i+1:end],prop)
    return sites
