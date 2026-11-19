"""Closed local arrays of root FormAttribute descriptions; no arbitrary evaluator."""
from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from v8unpack_agent.bsl_facts import _Failure, _Token
from v8unpack_agent.dynamic_forms import (
    ConditionReference,
    DynamicFormOperation,
    DynamicOperationIndex,
    DynamicReason,
    EntityReference,
    OperationKind,
    OperationSource,
    ResolvedValue,
    TargetKind,
    TargetOrigin,
)
from v8unpack_agent.dynamic_forms import (
    ResolutionStatus as R,
)
from v8unpack_agent.dynamic_forms_detector import (
    _STATEMENT_END,
    _arguments,
    _matching,
    _PreparedDynamicModule,
    _Procedure,
    _symbol,
    _word,
)

_MAX_ATTRIBUTE_EVENTS = 10_000
_MAX_ATTRIBUTE_MEMBERS = 10_000
_MAX_ATTRIBUTE_LOCALS = 10_000
_MAX_ATTRIBUTE_RECORDS = 10_000


@dataclass
class _Description:
    name: ResolvedValue
    root: bool
    conditions: tuple[ConditionReference, ...]
    source: OperationSource
    tainted: bool = False


@dataclass(frozen=True)
class _Member:
    description: _Description
    conditions: tuple[ConditionReference, ...]
    source: OperationSource


@dataclass
class _Array:
    members: list[_Member] = field(default_factory=list)
    tainted: bool = False


@dataclass(frozen=True)
class _Local:
    value: _Array | _Description
    conditions: tuple[ConditionReference, ...]


@dataclass(frozen=True)
class _AttributeRecord:
    reference: EntityReference
    operation_id: str
    evidence: tuple[OperationSource, ...]
    evidence_key: str
    conditional: bool
    context_complete: bool


@dataclass(frozen=True)
class _AttributeAnalysis:
    records: tuple[_AttributeRecord, ...]
    native_calls: frozenset[int]
    unknown_batches: tuple[str, ...]


class _Scope:
    def __init__(self, prepared: _PreparedDynamicModule, proc: _Procedure,
                 operations: dict[int, DynamicFormOperation]):
        self.prepared, self.proc, self.operations = prepared, proc, operations
        self.symbols: dict[str, _Local] = {}
        self.excluded = prepared.module_bindings | frozenset(n.casefold() for n in proc.parameters)
        self.pending: list[tuple[int, int, Callable[[], None]]] = []
        self.serial = itertools.count()
        self.records: list[_AttributeRecord] = []
        self.native_calls: set[int] = set()
        self.unknown_batches: list[str] = []

    def schedule(self, end: int, callback: Callable[[], None]) -> None:
        if len(self.pending) >= _MAX_ATTRIBUTE_EVENTS:
            raise _Failure('unsupported', 'attribute_event_budget')
        heapq.heappush(self.pending, (end, next(self.serial), callback))

    def flush(self, position: int) -> None:
        while self.pending and self.pending[0][0] <= position:
            _, _, callback = heapq.heappop(self.pending)
            callback()

    def value(self, expr: tuple[_Token, ...], conditions: tuple[ConditionReference, ...],
              complete: bool) -> _Array | _Description | None:
        if not expr:
            return None
        if len(expr) == 1 and expr[0].kind == 'word' and _word(expr[0]) not in self.excluded:
            local = self.symbols.get(_word(expr[0]))
            if local is not None and complete and all(c in conditions for c in local.conditions):
                return local.value
        if len(expr) < 2 or _word(expr[0]) not in {'новый', 'new'}:
            return None
        kind = _word(expr[1])
        if kind in {'массив', 'array'}:
            if len(expr) == 2:
                return _Array()
            if len(expr) == 4 and _symbol(expr[2], '(') and _symbol(expr[3], ')'):
                return _Array()
        if kind in {'реквизитформы', 'formattribute'} and len(expr) >= 4 and _symbol(expr[2], '('):
            args, end = _arguments(expr, 2)
            if end == len(expr)-1 and len(args) >= 2 and args[0]:
                source = self.prepared.snapshot.source(expr[0].start, expr[-1].end)
                return _Description(self.prepared.snapshot.value(args[0]), len(args) == 2,
                                    conditions, source)
        return None

    def assign(self, name: str, value: _Array | _Description | None,
               conditions: tuple[ConditionReference, ...]) -> None:
        if value is None:
            self.symbols.pop(name, None)
        else:
            if name not in self.symbols and len(self.symbols) >= _MAX_ATTRIBUTE_LOCALS:
                raise _Failure('unsupported', 'attribute_local_budget')
            self.symbols[name] = _Local(value, conditions)

    def opaque(self, names: tuple[str, ...]) -> None:
        for name in names:
            local = self.symbols.pop(name, None)
            if local is not None:
                local.value.tainted = True

    def add(self, array: _Array, value: _Array | _Description | None,
            conditions: tuple[ConditionReference, ...], source: OperationSource) -> None:
        if not isinstance(value, _Description):
            array.tainted = True
            return
        if len(array.members) >= _MAX_ATTRIBUTE_MEMBERS:
            raise _Failure('unsupported', 'attribute_member_budget')
        array.members.append(_Member(value, conditions, source))

    @staticmethod
    def taint(value: _Array | _Description) -> None:
        value.tainted = True

    @staticmethod
    def clear(array: _Array) -> None:
        array.members.clear()
        array.tainted = False

    def batch(self, op: DynamicFormOperation, array: _Array | _Description | None,
              add_omitted: bool, delete_omitted: bool) -> None:
        if add_omitted:
            return
        if (not isinstance(array, _Array) or array.tainted or not delete_omitted
                or not op.condition_context_complete):
            self.unknown_batches.append(op.operation_id)
            return
        counts: dict[str, int] = {}
        for member in array.members:
            name = member.description.name
            if name.resolution == R.EXACT and isinstance(name.value, str):
                counts[name.value.casefold()] = counts.get(name.value.casefold(), 0)+1
        for member in array.members:
            spec = member.description
            combined = tuple(dict.fromkeys(op.conditions + spec.conditions + member.conditions))
            branches: dict[str, object] = {}
            incompatible = False
            for condition in combined:
                if condition.condition_id in branches and branches[condition.condition_id] != condition.branch:
                    incompatible = True
                branches[condition.condition_id] = condition.branch
            if incompatible:
                continue
            name = spec.name if not spec.tainted else ResolvedValue(R.UNRESOLVED, reason=DynamicReason.ALIAS_INVALIDATED)
            proven = (spec.root and not spec.tainted and name.resolution == R.EXACT
                      and isinstance(name.value, str) and bool(name.value)
                      and counts.get(name.value.casefold()) == 1
                      and all(c in op.conditions for c in combined))
            ref = EntityReference(TargetKind.ATTRIBUTE, TargetOrigin.DYNAMIC if proven else TargetOrigin.UNKNOWN,
                                  R.EXACT if proven else R.UNRESOLVED,
                                  op.operation_id if proven else None, name,
                                  op.conditions if proven else combined,
                                  None if proven else DynamicReason.CONFLICTING_BINDINGS)
            if len(self.records) >= _MAX_ATTRIBUTE_RECORDS:
                raise _Failure('unsupported', 'attribute_record_budget')
            self.records.append(_AttributeRecord(ref, op.operation_id, (spec.source, member.source),
                                str(member.source.start_offset), bool(combined), op.condition_context_complete))

    def run(self) -> None:
        body = self.proc.body
        native_arrays = {'добавить', 'add', 'очистить', 'clear', 'вставить', 'insert', 'удалить',
                         'delete', 'установить', 'set', 'получить', 'get', 'найти', 'find',
                         'количество', 'count', 'вграница', 'ubound'}
        readonly_arrays = {'найти', 'find', 'количество', 'count', 'вграница', 'ubound'}
        constructors = {'массив', 'array', 'реквизитформы', 'formattribute', 'описаниетипов', 'typedescription'}
        for i, token in enumerate(body):
            self.flush(token.start)
            conditions, complete = self.prepared.contexts[token.start]
            if _word(token) in {'для', 'for', 'пока', 'while'}:
                # A later iteration may see any mutation in the body. Preserve
                # neither pointer identity nor contents from an outer iteration.
                for prior_local in self.symbols.values():
                    self.taint(prior_local.value)
                self.symbols.clear()
            boundary = i == 0 or _symbol(body[i-1], ';') or _symbol(body[i-1], ':') or _word(body[i-1]) in {
                'тогда', 'then', 'иначе', 'else', 'цикл', 'do', 'попытка', 'try', 'исключение', 'except'}
            if token.kind == 'word' and _word(token) not in self.excluded and i+1 < len(body):
                if _symbol(body[i+1], '=') and boundary:
                    end = i+2
                    while end < len(body) and not _symbol(body[end], ';') and _word(body[end]) not in _STATEMENT_END:
                        if body[end].kind == 'symbol' and body[end].value in {'(', '['}:
                            end = _matching(body, end)+1
                        else:
                            end += 1
                    rhs = body[i+2:end]
                    value = self.value(rhs, conditions, complete)
                    activation = rhs[-1].end if rhs else token.end
                    self.schedule(activation, partial(self.assign, _word(token), value, conditions))
                elif i+3 < len(body) and _symbol(body[i+1], '.') and _symbol(body[i+3], '='):
                    local = self.value(body[i:i+1], conditions, complete)
                    if isinstance(local, _Description):
                        local.tainted = True
                elif _symbol(body[i+1], '['):
                    end = _matching(body, i+1)
                    # An indexed value may expose a mutable description through
                    # an unmodeled alias, member write, or method receiver.
                    indexed_array = self.value(body[i:i+1],conditions,complete)
                    if isinstance(indexed_array,_Array):
                        self.schedule(body[end].end,partial(self.taint,indexed_array))
                    if end+1 < len(body) and _symbol(body[end+1], '='):
                        local = self.value(body[i:i+1], conditions, complete)
                        if isinstance(local, _Array):
                            value_end = end+2
                            while value_end < len(body) and not _symbol(body[value_end], ';') and _word(body[value_end]) not in _STATEMENT_END:
                                if body[value_end].kind == 'symbol' and body[value_end].value in {'(', '['}:
                                    value_end = _matching(body,value_end)+1
                                else:
                                    value_end += 1
                            activation = body[value_end-1].end
                            self.schedule(activation, partial(self.taint,local))
            if token.kind != 'word' or i+1 >= len(body) or not _symbol(body[i+1], '('):
                continue
            args, end = _arguments(body, i+1)
            start = i
            while start >= 2 and _symbol(body[start-1], '.') and body[start-2].kind == 'word':
                start -= 2
            receiver = body[start:i-1] if start < i else ()
            local_receiver = self.value(receiver, conditions, complete)
            source = self.prepared.snapshot.source(body[start].start, body[end].end)
            method = _word(token)
            batch = self.operations.get(body[start].start)
            if batch is not None and batch.operation_kind == OperationKind.ATTRIBUTES_CHANGE and batch.target.resolution == R.EXACT:
                add = self.value(args[0], conditions, complete) if args and args[0] else None
                add_omitted = not args or not args[0]
                delete_omitted = len(args) < 2 or not args[1]
                self.native_calls.add(token.start)
                self.schedule(body[end].end, partial(self.batch, batch, add, add_omitted, delete_omitted))
            elif isinstance(local_receiver, _Array):
                if method in native_arrays:
                    self.native_calls.add(token.start)
                if method in {'добавить', 'add'} and len(args) == 1:
                    value = self.value(args[0], conditions, complete)
                    self.schedule(body[end].end, partial(self.add, local_receiver, value, conditions, source))
                elif method in {'очистить', 'clear'} and not args:
                    self.schedule(body[end].end, partial(self.clear, local_receiver))
                elif method not in readonly_arrays:
                    self.schedule(body[end].end, partial(self.taint,local_receiver))
            elif i and _word(body[i-1]) in {'новый', 'new'} and method in constructors:
                self.native_calls.add(token.start)
            else:
                if isinstance(local_receiver,_Description):
                    self.schedule(body[end].end,partial(self.taint,local_receiver))
                names = tuple(_word(arg[0]) for arg in args if arg and arg[0].kind == 'word'
                              and _word(arg[0]) in self.symbols)
                if names:
                    self.schedule(body[end].end, partial(self.opaque, names))
        self.flush(self.proc.end)


def analyze_attribute_arrays(prepared: _PreparedDynamicModule,
                             index: DynamicOperationIndex) -> _AttributeAnalysis:
    operations = {op.source.start_offset: op for op in index.operations}
    records: list[_AttributeRecord] = []
    native: set[int] = set()
    unknown: list[str] = []
    for proc in prepared.procedures:
        scope = _Scope(prepared, proc, operations)
        scope.run()
        if len(records)+len(scope.records) > _MAX_ATTRIBUTE_RECORDS:
            raise _Failure('unsupported', 'attribute_record_budget')
        records.extend(scope.records)
        native.update(scope.native_calls)
        unknown.extend(scope.unknown_batches)
    return _AttributeAnalysis(tuple(records), frozenset(native), tuple(sorted(set(unknown))))
