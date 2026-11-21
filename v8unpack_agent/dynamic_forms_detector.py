"""Conservative direct BSL form-operation detector (#214).

The caller identifies the form module and supplies proven declared names.
Unknown lexical/control syntax fails closed; no runtime or alias evaluation.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from v8unpack_agent.bsl_facts import (
    _Failure,
    _name,
    _parameters,
    _symbol,
    _Token,
    _tokens,
)
from v8unpack_agent.dynamic_forms import (
    BranchKind,
    ConditionKind,
    ConditionReference,
    DynamicCondition,
    DynamicFormOperation,
    DynamicOperationIndex,
    DynamicReason,
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
from v8unpack_agent.modules import validate_relative_module_path

__all__ = ['DynamicAnalysisError', 'detect_form_operations']

_MAX_NESTING = 64
_MAX_RECORDS = 10_000


class DynamicAnalysisError(ValueError):
    """Source-free fail-closed result, distinct from a successful empty index."""
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _validate_dynamic_input(text: str, relative_path: str, declared_elements: tuple[str, ...],
                            declared_commands: tuple[str, ...], max_chars: int, *,
                            declared_attributes: tuple[str, ...] = ()) -> None:
    if not isinstance(text, str):
        raise TypeError('text must be str')
    validate_relative_module_path(relative_path)
    for names in (declared_elements, declared_commands, declared_attributes):
        if not isinstance(names, tuple) or any(not isinstance(n, str) or not n for n in names):
            raise TypeError('declared names require a tuple of nonempty strings')
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError('max_chars must be a positive integer')
    if len(text) > max_chars:
        raise DynamicAnalysisError('unsupported', 'character_budget')


def detect_form_operations(
    text: str, relative_path: str, *,
    declared_elements: tuple[str, ...] = (),
    declared_commands: tuple[str, ...] = (),
    declared_attributes: tuple[str, ...] = (),
    max_chars: int = 2_000_000,
) -> DynamicOperationIndex:
    """Analyze an explicitly identified form module; never execute its BSL.

    Inventory names are assertions by the caller, not inferred from spelling.
    Failures raise DynamicAnalysisError with a technical reason and no source.
    """
    _validate_dynamic_input(text, relative_path, declared_elements, declared_commands, max_chars,
                            declared_attributes=declared_attributes)
    try:
        return _detect(text, relative_path, declared_elements=declared_elements,
                       declared_commands=declared_commands, declared_attributes=declared_attributes)
    except _Failure as exc:
        raise DynamicAnalysisError(exc.status, exc.reason) from None
    except UnicodeError:
        raise DynamicAnalysisError('unsupported', 'invalid_unicode') from None

@dataclass(frozen=True)
class _Directive:
    word: str
    expression: str
    start: int
    end: int


def _dynamic_tokens(
    text: str, *, allow_formatted_dates: bool = False, allow_export_trivia: bool = False,
) -> tuple[list[_Token], tuple[_Directive, ...]]:
    # A valid multiline BSL string continues with |, so a line starting with #
    # cannot occur inside such a literal. The strict lexer still validates strings.
    directives = []
    masked = list(text)
    region_depth = 0
    guards: list[bool] = []
    indentation = r'[ \t\u00a0]' if allow_export_trivia else r'[ \t]'
    for match in re.finditer(rf'(?m)^{indentation}*#([^\r\n]*)', text):
        raw = match.group(1)
        # Use the same lexer to distinguish // comments from // inside quotes.
        parts_tokens = _tokens(raw, allow_export_trivia=allow_export_trivia)
        content = raw[:parts_tokens[-1].end].strip() if parts_tokens else ''
        parts = content.split(None, 1)
        if not parts:
            raise _Failure('incomplete', 'invalid_directive')
        word = parts[0].casefold()
        expression = parts[1] if len(parts) == 2 else ''
        if word in {'область', 'region'} and expression:
            region_depth += 1
        elif word in {'конецобласти', 'endregion'} and not expression and region_depth:
            region_depth -= 1
        elif word in {'если', 'if'} and expression:
            expression = _guard_expression(expression)
            guards.append(False)
            if len(guards) > _MAX_NESTING:
                raise _Failure('unsupported', 'nesting_budget')
        elif word in {'иначеесли', 'elsif'} and expression and guards and not guards[-1]:
            expression = _guard_expression(expression)
        elif word in {'иначе', 'else'} and not expression and guards and not guards[-1]:
            guards[-1] = True
        elif word in {'конецесли', 'endif'} and not expression and guards:
            guards.pop()
        else:
            raise _Failure('unsupported', 'conditional_or_unknown_directive')
        start = text.index('#', match.start(), match.end())
        directives.append(_Directive(word, expression, start, match.end()))
        masked[match.start():match.end()] = ' ' * (match.end() - match.start())
    if region_depth or guards:
        raise _Failure('incomplete', 'unclosed_directive')
    tokens = _tokens(''.join(masked), allow_formatted_dates=allow_formatted_dates,
                     allow_export_trivia=allow_export_trivia)
    # Original offsets and values are retained for every non-directive token.
    return tokens, tuple(directives)


def _guard_expression(expression: str) -> str:
    tokens = _tokens(expression)
    if len(tokens) < 2 or tokens[-1].kind != 'word' or tokens[-1].value.casefold() not in {'тогда', 'then'}:
        raise _Failure('incomplete', 'missing_directive_then')
    return expression[:tokens[-1].start].rstrip()


class _Snapshot:
    def __init__(self, text: str, relative_path: str) -> None:
        self.text = text
        self.relative_path = relative_path
        self.sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        self.line_starts = [0] + [m.end() for m in re.finditer(r'\r\n|\r|\n', text)]
        # Validate even empty/unsupported modules at the public boundary.
        validate_relative_module_path(relative_path)

    def source(self, start: int, end: int) -> OperationSource:
        return OperationSource(self.relative_path, self.sha,
                               bisect.bisect_right(self.line_starts, start),
                               bisect.bisect_right(self.line_starts, end - 1), start, end)

    def expression(self, tokens: tuple[_Token, ...]) -> str:
        return self.text[tokens[0].start:tokens[-1].end]

    def value(self, tokens: tuple[_Token, ...], *, type_builtin: bool = False) -> ResolvedValue:
        if not tokens:
            return ResolvedValue(R.EXACT, omitted=True)
        expression = self.expression(tokens)
        if len(tokens) == 1:
            token = tokens[0]
            if token.kind == 'string':
                return ResolvedValue(R.EXACT, token.value, expression)
            if token.kind == 'word':
                word = token.value.casefold()
                if word in {'истина', 'true', 'ложь', 'false'}:
                    return ResolvedValue(R.EXACT, word in {'истина', 'true'}, expression)
                if word in {'неопределено', 'undefined', 'null'}:
                    return ResolvedValue(R.EXACT, None, expression)
        numeric = ((len(tokens) == 1 and tokens[0].kind == 'number') or
                   (len(tokens) == 2 and tokens[0].kind == 'symbol' and tokens[0].value in {'+', '-'} and tokens[1].kind == 'number'))
        if numeric:
            spelling = ''.join(t.value for t in tokens)
            if '.' not in spelling:
                if len(spelling.lstrip('+-')) > 1024:
                    return ResolvedValue(R.PARTIAL, expression=expression, reason=DynamicReason.COMPUTED_VALUE)
                try:
                    integer = int(spelling)
                except ValueError:
                    return ResolvedValue(R.PARTIAL, expression=expression, reason=DynamicReason.COMPUTED_VALUE)
                return ResolvedValue(R.EXACT, integer, expression)
            value = float(spelling)
            if Decimal.from_float(value) == Decimal(spelling):
                return ResolvedValue(R.EXACT, value, expression)
        if (type_builtin and len(tokens) == 4 and tokens[0].kind == 'word'
                and tokens[0].value.casefold() in {'тип', 'type'}
                and tokens[1].value == '(' and tokens[2].kind == 'string' and tokens[3].value == ')'):
            return ResolvedValue(R.EXACT, tokens[2].value, expression)
        return ResolvedValue(R.PARTIAL, expression=expression, reason=DynamicReason.COMPUTED_VALUE)


_START = {'процедура': 'procedure', 'procedure': 'procedure', 'функция': 'function', 'function': 'function'}


_END = {'конецпроцедуры': 'procedure', 'endprocedure': 'procedure', 'конецфункции': 'function', 'endfunction': 'function'}


@dataclass(frozen=True)
class _Procedure:
    name: str
    parameters: tuple[str, ...]
    body: tuple[_Token, ...]
    start: int
    end: int
    shadowed: frozenset[str]


def _word(token: _Token) -> str:
    return token.value.casefold() if token.kind == 'word' else ''


def _matching(tokens: list[_Token] | tuple[_Token, ...], start: int) -> int:
    pairs = {'(': ')', '[': ']'}
    stack = []
    for i in range(start, len(tokens)):
        token = tokens[i]
        if token.kind != 'symbol':
            continue
        if token.value in pairs:
            stack.append(pairs[token.value])
            if len(stack) > _MAX_NESTING:
                raise _Failure('unsupported', 'nesting_budget')
        elif token.value in {')', ']'}:
            if not stack or token.value != stack.pop():
                raise _Failure('incomplete', 'unbalanced_delimiter')
            if not stack:
                return i
    raise _Failure('incomplete', 'unclosed_delimiter')


def _arguments(tokens: list[_Token] | tuple[_Token, ...], start: int) -> tuple[tuple[tuple[_Token, ...], ...], int]:
    if not _symbol(tokens[start], '('):
        raise _Failure('unsupported', 'expected_call')
    end = _matching(tokens, start)
    if end == start + 1:
        return (), end
    result = []
    begin = i = start + 1
    while i < end:
        if tokens[i].kind == 'symbol' and tokens[i].value in {'(', '['}:
            i = _matching(tokens, i) + 1
        elif _symbol(tokens[i], ','):
            result.append(tuple(tokens[begin:i]))
            i += 1
            begin = i
        else:
            i += 1
    result.append(tuple(tokens[begin:end]))
    return tuple(result), end


def _bindings(tokens: tuple[_Token, ...], parameters: tuple[str, ...], *, module_level: bool = False) -> frozenset[str]:
    names = {name.casefold() for name in parameters}
    for i, token in enumerate(tokens):
        if _word(token) in {'перем', 'var'}:
            j = i + 1
            while j < len(tokens):
                if not _name(tokens[j]):
                    raise _Failure('unsupported', 'unsupported_local_variable')
                names.add(tokens[j].value.casefold())
                j += 1
                if module_level and j < len(tokens) and _word(tokens[j]) in {'экспорт', 'export'}:
                    j += 1
                if j < len(tokens) and _symbol(tokens[j], ','):
                    j += 1
                    continue
                if j >= len(tokens) or not _symbol(tokens[j], ';'):
                    raise _Failure('incomplete', 'unclosed_local_variable')
                break
        # Conservatively collect every possible bare binding. BSL uses '='
        # for both assignment and comparison; uncertain comparisons may reduce
        # coverage, but a missed assignment after Try/Except/labels/directives
        # must never manufacture an implicit platform receiver.
        if (_name(token) and i + 1 < len(tokens) and _symbol(tokens[i + 1], '=')
                and (i == 0 or not _symbol(tokens[i - 1], '.'))):
            names.add(token.value.casefold())
        if _word(token) in {'для', 'for'}:
            j = i + 1
            if j < len(tokens) and _word(tokens[j]) in {'каждого', 'each'}:
                j += 1
            if j < len(tokens) and _name(tokens[j]):
                names.add(tokens[j].value.casefold())
    return frozenset(names)


def _procedures(tokens: list[_Token]) -> tuple[tuple[_Procedure, ...], frozenset[str], frozenset[str]]:
    result = []
    module_tokens = []
    seen = set()
    i = 0
    while i < len(tokens):
        word = _word(tokens[i])
        if word not in _START:
            if word in _END:
                raise _Failure('incomplete', 'unexpected_procedure_end')
            module_tokens.append(tokens[i])
            i += 1
            continue
        if i and _symbol(tokens[i - 1], '.'):
            raise _Failure('unsupported', 'reserved_member_name')
        start = tokens[i].start
        kind = _START[word]
        i += 1
        if i >= len(tokens) or not _name(tokens[i]):
            raise _Failure('incomplete', 'missing_procedure_name')
        name = tokens[i].value
        if name.casefold() in seen:
            raise _Failure('unsupported', 'duplicate_procedure')
        seen.add(name.casefold())
        i += 1
        if i >= len(tokens) or not _symbol(tokens[i], '('):
            raise _Failure('incomplete', 'missing_parameters')
        params, i = _parameters(tokens, i + 1)
        if i < len(tokens) and _word(tokens[i]) in {'экспорт', 'export'}:
            i += 1
        begin = i
        while i < len(tokens) and _word(tokens[i]) not in _END:
            if _word(tokens[i]) in _START:
                raise _Failure('incomplete', 'nested_procedure')
            i += 1
        if i >= len(tokens) or _END[_word(tokens[i])] != kind:
            raise _Failure('incomplete', 'unclosed_procedure')
        body = tuple(tokens[begin:i])
        result.append(_Procedure(name, params, body, start, tokens[i].end, _bindings(body, params)))
        i += 1
    j = 0
    while j < len(module_tokens):
        token = module_tokens[j]
        if token.kind == 'directive' or _word(token) in {'асинх', 'async'} or _symbol(token, ';'):
            j += 1
        elif _word(token) in {'перем', 'var'}:
            while j < len(module_tokens) and not _symbol(module_tokens[j], ';'):
                j += 1
            if j == len(module_tokens):
                raise _Failure('incomplete', 'unclosed_module_variable')
            j += 1
        else:
            raise _Failure('unsupported', 'module_initialization_code')
    return tuple(result), _bindings(tuple(module_tokens), (), module_level=True), frozenset(seen)


@dataclass
class _Block:
    kind: ConditionKind
    refs: list[ConditionReference]
    ended: bool = False


def _branch_contexts(tokens: list[_Token], directives: tuple[_Directive, ...], snapshot: _Snapshot) -> tuple[dict[int, tuple[tuple[ConditionReference, ...], bool]], tuple[DynamicCondition, ...]]:
    conditions: list[DynamicCondition] = []
    runtime: list[_Block] = []
    preprocessor: list[_Block] = []
    contexts: dict[int, tuple[tuple[ConditionReference, ...], bool]] = {}
    complete = True
    skip_to = -1
    header_context: tuple[ConditionReference, ...] = ()

    def guard(kind: ConditionKind, start: int, end: int, expression: str) -> ConditionReference:
        source = snapshot.source(start, end)
        ident = 'dcond_' + hashlib.sha256((kind.value + ':' + str(start) + ':' + str(end) + ':' + snapshot.sha).encode('utf-8')).hexdigest()
        condition = DynamicCondition(ident, kind, source, expression)
        if len(conditions) >= _MAX_RECORDS:
            raise _Failure('unsupported', 'condition_budget')
        conditions.append(condition)
        return ConditionReference(ident, BranchKind.THEN if kind != ConditionKind.LOOP else BranchKind.BODY)

    def context() -> tuple[ConditionReference, ...]:
        if sum(len(block.refs) for block in preprocessor + runtime) > _MAX_NESTING:
            raise _Failure('unsupported', 'condition_context_budget')
        return tuple(ref for block in preprocessor + runtime for ref in block.refs)

    def otherwise(blocks: list[_Block]) -> _Block:
        if not blocks or blocks[-1].kind == ConditionKind.LOOP or blocks[-1].ended:
            raise _Failure('incomplete', 'unexpected_else')
        block = blocks[-1]
        block.refs = [ConditionReference(ref.condition_id, BranchKind.ELSE) for ref in block.refs]
        return block

    def header_end(index: int, keyword: set[str]) -> int:
        i = index + 1
        while i < len(tokens):
            token = tokens[i]
            if token.kind == 'symbol' and token.value in {'(', '['}:
                i = _matching(tokens, i) + 1
                continue
            if token.kind == 'word' and token.value.casefold() in keyword:
                if i == index + 1:
                    raise _Failure('incomplete', 'empty_guard_expression')
                return i
            if token.kind == 'symbol' and token.value == ';':
                break
            i += 1
        raise _Failure('incomplete', 'unclosed_guard_header')

    events = sorted([(t.start, 1, i, t) for i, t in enumerate(tokens)] +
                    [(d.start, 0, -1, d) for d in directives], key=lambda e: (e[0], e[1]))
    for position, event_kind, index, item in events:
        if len(runtime) + len(preprocessor) > _MAX_NESTING:
            raise _Failure('unsupported', 'nesting_budget')
        if sum(len(block.refs) for block in preprocessor + runtime) > _MAX_NESTING:
            raise _Failure('unsupported','condition_context_budget')
        if event_kind == 0:
            assert isinstance(item, _Directive)
            word = item.word
            if word in {'если', 'if'}:
                ref = guard(ConditionKind.PREPROCESSOR, item.start, item.end, item.expression)
                preprocessor.append(_Block(ConditionKind.PREPROCESSOR, [ref]))
            elif word in {'иначеесли', 'elsif'}:
                block = otherwise(preprocessor)
                block.refs.append(guard(ConditionKind.PREPROCESSOR, item.start, item.end, item.expression))
            elif word in {'иначе', 'else'}:
                otherwise(preprocessor).ended = True
            elif word in {'конецесли', 'endif'}:
                if not preprocessor:
                    raise _Failure('incomplete', 'unexpected_preprocessor_end')
                preprocessor.pop()
            continue
        assert isinstance(item, _Token)
        token = item
        contexts[position] = (header_context if position < skip_to else context(), complete)
        if token.kind != 'word' or position < skip_to:
            continue
        word = token.value.casefold()
        if word in {'если', 'if', 'иначеесли', 'elsif'}:
            header_context = context()
            if word in {'иначеесли', 'elsif'}:
                otherwise(runtime)
                header_context = context()
            end = header_end(index, {'тогда', 'then'})
            expression = snapshot.text[tokens[index + 1].start:tokens[end - 1].end]
            ref = guard(ConditionKind.IF, token.start, tokens[end].end, expression)
            if word in {'если', 'if'}:
                runtime.append(_Block(ConditionKind.IF, [ref]))
            else:
                runtime[-1].refs.append(ref)
            skip_to = tokens[end].end
        elif word in {'иначе', 'else'}:
            otherwise(runtime).ended = True
        elif word in {'конецесли', 'endif'}:
            if not runtime or runtime[-1].kind != ConditionKind.IF:
                raise _Failure('incomplete', 'unexpected_if_end')
            runtime.pop()
        elif word in {'для', 'for', 'пока', 'while'}:
            header_context = context()
            end = header_end(index, {'цикл', 'do'})
            expression = snapshot.text[token.start:tokens[end].end]
            ref = guard(ConditionKind.LOOP, token.start, tokens[end].end, expression)
            runtime.append(_Block(ConditionKind.LOOP, [ref]))
            skip_to = tokens[end].end
        elif word in {'конеццикла', 'enddo'}:
            if not runtime or runtime[-1].kind != ConditionKind.LOOP:
                raise _Failure('incomplete', 'unexpected_loop_end')
            runtime.pop()
        elif word in {'перейти', 'goto', 'выполнить', 'execute'} and (index == 0 or not _symbol(tokens[index-1], '.')):
            # These constructs can revisit source sites or replace lexical bindings.
            # Marking only following sites incomplete would retain false exact facts.
            raise _Failure('unsupported', 'dynamic_control_flow')
        elif word in {'попытка', 'try', 'исключение', 'except'}:
            complete = False
        elif word in _END:
            if runtime:
                raise _Failure('incomplete', 'unclosed_runtime_guard')
            complete = True
    if runtime or preprocessor:
        raise _Failure('incomplete', 'unclosed_guard')
    return contexts, tuple(conditions)


_COLLECTIONS = {'элементы': TargetKind.ELEMENT, 'items': TargetKind.ELEMENT,
               'команды': TargetKind.COMMAND, 'commands': TargetKind.COMMAND}


_SELF = {'этаформа', 'thisform', 'этотобъект', 'thisobject'}


_METHODS = {'добавить': 'add', 'add': 'add', 'вставить': 'insert', 'insert': 'insert',
           'удалить': 'delete', 'delete': 'delete', 'переместить': 'move', 'move': 'move'}


_STATEMENT_END = {'иначе', 'else', 'иначеесли', 'elsif', 'конецесли', 'endif', 'конеццикла', 'enddo', 'конецпроцедуры', 'endprocedure', 'конецфункции', 'endfunction', 'исключение', 'except', 'конецпопытки', 'endtry'}


_BASE_FORM_PROPERTIES = frozenset({PropertyKind.TITLE, PropertyKind.READ_ONLY})


_PROPERTIES = {prop.value: prop for prop in PropertyKind}


_PROPERTIES.update(dict(zip(('visible', 'enabled', 'title', 'readonly', 'datapath', 'commandname', 'type', 'parent', 'currentpage', 'currentrow', 'tooltip', 'width', 'height'), PropertyKind)))


@dataclass(frozen=True)
class _PreparedDynamicModule:
    snapshot: _Snapshot
    tokens: tuple[_Token, ...]
    directives: tuple[_Directive, ...]
    procedures: tuple[_Procedure, ...]
    module_bindings: frozenset[str]
    procedure_names: frozenset[str]
    contexts: Mapping[int, tuple[tuple[ConditionReference, ...], bool]]
    conditions: tuple[DynamicCondition, ...]


def _prepare_dynamic_module(text: str, relative_path: str) -> _PreparedDynamicModule:
    snapshot = _Snapshot(text, relative_path)
    tokens, directives = _dynamic_tokens(text)
    procs, module_bindings, procedure_names = _procedures(tokens)
    contexts, conditions = _branch_contexts(tokens, directives, snapshot)
    return _PreparedDynamicModule(snapshot, tuple(tokens), directives, procs,
                                  module_bindings, procedure_names, contexts, conditions)


def _detect(text: str, relative_path: str, *, declared_elements: tuple[str, ...] = (),
            declared_commands: tuple[str, ...] = (), declared_attributes: tuple[str, ...] = (),
            prepared: _PreparedDynamicModule | None = None,
            receiver_proofs: Mapping[tuple[int, int], EntityReference | TargetKind] | None = None) -> DynamicOperationIndex:
    if prepared is None:
        prepared = _prepare_dynamic_module(text, relative_path)
    if prepared.snapshot.relative_path != relative_path or prepared.snapshot.text != text:
        raise ValueError('prepared module source mismatch')
    snapshot = prepared.snapshot
    procs, module_bindings, procedure_names = prepared.procedures, prepared.module_bindings, prepared.procedure_names
    contexts, conditions = prepared.contexts, prepared.conditions

    def proof(receiver: tuple[_Token, ...]) -> EntityReference | TargetKind | None:
        if receiver and receiver_proofs is not None:
            return receiver_proofs.get((receiver[0].start, receiver[-1].end))
        return None

    operations: list[DynamicFormOperation] = []
    form_attributes = frozenset(n.casefold() for n in declared_attributes)
    inventory = {TargetKind.ELEMENT: {n.casefold() for n in declared_elements},
                 TargetKind.COMMAND: {n.casefold() for n in declared_commands}}

    for proc in procs:
        body = proc.body
        shadowed = module_bindings | proc.shadowed | form_attributes

        def form_receiver(receiver: tuple[_Token, ...], shadowed: frozenset[str] = shadowed) -> bool:
            bound = proof(receiver)
            if isinstance(bound, EntityReference) and bound.target_kind == TargetKind.FORM:
                return bound.resolution == R.EXACT
            return (len(receiver) == 1 and _word(receiver[0]) in _SELF
                    and _word(receiver[0]) not in shadowed)

        def collection(receiver: tuple[_Token, ...], shadowed: frozenset[str] = shadowed) -> tuple[TargetKind | None, bool]:
            bound = proof(receiver)
            if isinstance(bound, TargetKind) and bound in {TargetKind.ELEMENT, TargetKind.COMMAND}:
                return bound, True
            if len(receiver) == 1 and _word(receiver[0]) in _COLLECTIONS:
                return _COLLECTIONS[_word(receiver[0])], _word(receiver[0]) not in shadowed
            if len(receiver) == 3 and receiver[1].value == '.' and form_receiver(receiver[:1]) and _word(receiver[2]) in _COLLECTIONS:
                return _COLLECTIONS[_word(receiver[2])], _word(receiver[2]) not in form_attributes
            return None, False

        def entity(receiver: tuple[_Token, ...]) -> EntityReference:
            bound = proof(receiver)
            if isinstance(bound, EntityReference):
                return bound
            if form_receiver(receiver):
                return EntityReference(TargetKind.FORM, TargetOrigin.FORM, R.EXACT)
            if len(receiver) >= 3 and receiver[-2].value == '.' and receiver[-1].kind == 'word':
                kind, proven = collection(receiver[:-2])
                if kind is not None:
                    name = snapshot.value((_Token('string', receiver[-1].value, receiver[-1].start, receiver[-1].end),))
                    if proven and receiver[-1].value.casefold() in inventory[kind]:
                        return EntityReference(kind, TargetOrigin.DECLARED, R.EXACT, name=name)
                    return EntityReference(kind, TargetOrigin.UNKNOWN, R.UNRESOLVED, name=name, reason=DynamicReason.RECEIVER_UNPROVEN)
            return EntityReference(TargetKind.UNKNOWN, TargetOrigin.UNKNOWN, R.UNRESOLVED, reason=DynamicReason.RECEIVER_UNPROVEN)

        def argument(name: str, expr: tuple[_Token, ...], shadowed: frozenset[str] = shadowed) -> OperationArgument:
            if name in {'parent', 'before'} and expr:
                ref = entity(expr)
                if ref.resolution == R.EXACT or isinstance(proof(expr), EntityReference):
                    return OperationArgument(name, ref)
            return OperationArgument(name, snapshot.value(expr, type_builtin=not ({'тип', 'type'} & (shadowed | procedure_names))))

        # Only scan maximal member chains: a suffix of Arbitrary.Items.Add
        # must never become an implicit platform collection call.
        i = 0
        while i < len(body):
            if len(operations) > _MAX_RECORDS:
                raise _Failure('unsupported', 'operation_budget')
            token = body[i]
            if token.kind != 'word' or (i and _symbol(body[i - 1], '.')):
                i += 1
                continue
            start = i
            chain_end = i + 1
            while chain_end + 1 < len(body) and _symbol(body[chain_end], '.') and body[chain_end + 1].kind == 'word':
                chain_end += 2
            chain = body[start:chain_end]
            refs, complete = contexts[token.start]
            if chain_end < len(body) and _symbol(body[chain_end], '('):
                args, end = _arguments(body, chain_end)
                method = _METHODS.get(_word(chain[-1]))
                kind, proven = collection(chain[:-2]) if len(chain) >= 3 else (None, False)
                op_kind: OperationKind | None = None
                target: EntityReference | None = None
                values: tuple[OperationArgument, ...] = ()
                if kind is not None and method is not None:
                    expected = {'add': (1, 1) if kind == TargetKind.COMMAND else (2, 3),
                                'insert': (2, 4), 'delete': (1, 1), 'move': (2, 3)}[method]
                    if not expected[0] <= len(args) <= expected[1]:
                        invalid_kind = ({'add': OperationKind.COMMAND_ADD, 'delete': OperationKind.COMMAND_DELETE}.get(method)
                                        if kind == TargetKind.COMMAND else
                                        {'add': OperationKind.ELEMENT_ADD, 'insert': OperationKind.ELEMENT_INSERT,
                                         'delete': OperationKind.ELEMENT_DELETE, 'move': OperationKind.ELEMENT_MOVE}.get(method))
                        if invalid_kind is not None:
                            invalid_names = {OperationKind.ELEMENT_ADD: ('name', 'type', 'parent'),
                                             OperationKind.ELEMENT_INSERT: ('name', 'type', 'parent', 'before'),
                                             OperationKind.ELEMENT_MOVE: ('parent', 'before'),
                                             OperationKind.COMMAND_ADD: ('name',)}.get(invalid_kind, ())
                            invalid_source = snapshot.source(token.start, body[end].end)
                            invalid_target = EntityReference(kind, TargetOrigin.UNKNOWN, R.UNRESOLVED,
                                                             reason=DynamicReason.UNSUPPORTED_SYNTAX)
                            invalid_values = tuple(OperationArgument(name, ResolvedValue(R.UNRESOLVED,
                                                   reason=DynamicReason.UNSUPPORTED_SYNTAX)) for name in invalid_names)
                            operations.append(DynamicFormOperation(operation_id(invalid_source, invalid_kind),
                                invalid_kind, invalid_source, proc.name, invalid_target, R.UNRESOLVED,
                                invalid_values, conditions=refs, condition_context_complete=complete,
                                provenance=('unsupported_call_arity',)))
                        i = chain_end + 1
                        continue
                    if method == 'add':
                        op_kind = OperationKind.COMMAND_ADD if kind == TargetKind.COMMAND else OperationKind.ELEMENT_ADD
                        names: tuple[str, ...] = ('name',) if kind == TargetKind.COMMAND else ('name', 'type', 'parent')
                    elif method == 'insert' and kind == TargetKind.ELEMENT:
                        op_kind = OperationKind.ELEMENT_INSERT
                        names = ('name', 'type', 'parent', 'before')
                    elif method == 'move' and kind == TargetKind.ELEMENT:
                        op_kind = OperationKind.ELEMENT_MOVE
                        target = entity(args[0])
                        names = ('parent', 'before')
                        args = args[1:]
                    elif method == 'delete':
                        op_kind = OperationKind.COMMAND_DELETE if kind == TargetKind.COMMAND else OperationKind.ELEMENT_DELETE
                        target = entity(args[0])
                        names = ()
                        args = ()
                    if op_kind is not None:
                        padded = args + ((),) * (len(names) - len(args))
                        values = tuple(argument(name, expr) for name, expr in zip(names, padded))
                        if target is None:
                            source = snapshot.source(token.start, body[end].end)
                            ident = operation_id(source, op_kind)
                            name_value = values[0].value
                            assert isinstance(name_value, ResolvedValue)
                            target = EntityReference(kind, TargetOrigin.DYNAMIC if proven else TargetOrigin.UNKNOWN,
                                                     R.EXACT if proven else R.UNRESOLVED,
                                                     ident if proven else None, name_value,
                                                     refs if proven else (), None if proven else DynamicReason.RECEIVER_UNPROVEN)
                        elif not proven or target.target_kind not in {kind, TargetKind.UNKNOWN}:
                            target = EntityReference(kind, TargetOrigin.UNKNOWN, R.UNRESOLVED, reason=DynamicReason.RECEIVER_UNPROVEN)
                elif (_word(chain[-1]) in {'изменитьреквизиты', 'changeattributes'} and
                      (len(chain) == 1 or (len(chain) == 3 and form_receiver(chain[:1])))):
                    if not 1 <= len(args) <= 2:
                        invalid_source = snapshot.source(token.start, body[end].end)
                        invalid_target = EntityReference(TargetKind.FORM, TargetOrigin.UNKNOWN, R.UNRESOLVED,
                                                         reason=DynamicReason.UNSUPPORTED_SYNTAX)
                        invalid_values = tuple(OperationArgument(name, ResolvedValue(R.UNRESOLVED,
                                               reason=DynamicReason.UNSUPPORTED_SYNTAX)) for name in ('add_list', 'delete_list'))
                        operations.append(DynamicFormOperation(operation_id(invalid_source, OperationKind.ATTRIBUTES_CHANGE),
                            OperationKind.ATTRIBUTES_CHANGE, invalid_source, proc.name, invalid_target, R.UNRESOLVED,
                            invalid_values, conditions=refs, condition_context_complete=complete,
                            provenance=('unsupported_call_arity',)))
                        i = chain_end + 1
                        continue
                    proven = len(chain) == 3 or _word(chain[0]) not in shadowed | procedure_names
                    op_kind = OperationKind.ATTRIBUTES_CHANGE
                    target = EntityReference(TargetKind.FORM, TargetOrigin.FORM if proven else TargetOrigin.UNKNOWN,
                                             R.EXACT if proven else R.UNRESOLVED,
                                             reason=None if proven else DynamicReason.RECEIVER_UNPROVEN)
                    values = tuple(argument(name, expr) for name, expr in zip(('add_list', 'delete_list'), args + ((),) * (2 - len(args))))
                if op_kind is not None:
                    assert target is not None
                    source = snapshot.source(token.start, body[end].end)
                    resolution = (R.UNRESOLVED if target.resolution != R.EXACT else
                                  R.EXACT if all(a.value.resolution == R.EXACT for a in values) else R.PARTIAL)
                    operations.append(DynamicFormOperation(operation_id(source, op_kind), op_kind, source, proc.name,
                        target, resolution, values, conditions=refs, condition_context_complete=complete,
                        provenance=('direct_form_module_syntax',) + (('local_binding_analysis',) if receiver_proofs is not None else ())))
                # Continue inside other calls to find genuinely nested direct calls.
                i = chain_end + 1
                continue
            if len(chain) >= 3 and _word(chain[-1]) in _PROPERTIES and chain_end < len(body) and _symbol(body[chain_end], '='):
                end = chain_end + 1
                while end < len(body) and not _symbol(body[end], ';') and _word(body[end]) not in _STATEMENT_END:
                    if body[end].kind == 'symbol' and body[end].value in {'(', '['}:
                        end = _matching(body, end) + 1
                    else:
                        end += 1
                if end == chain_end + 1:
                    raise _Failure('incomplete', 'unclosed_setter_statement')
                # A member comparison in If/While/function arguments is not a setter.
                boundary = start == 0 or _symbol(body[start - 1], ';') or _word(body[start - 1]) in {'тогда', 'then', 'иначе', 'else', 'цикл', 'do'}
                if boundary:
                    target = entity(chain[:-2])
                    value = snapshot.value(body[chain_end + 1:end])
                    source = snapshot.source(token.start, body[end - 1].end)
                    resolution = value.resolution if target.resolution == R.EXACT else R.UNRESOLVED
                    form_property_unproven = (target.target_kind == TargetKind.FORM and
                        (_PROPERTIES[_word(chain[-1])] not in _BASE_FORM_PROPERTIES or _word(chain[-1]) in form_attributes))
                    if form_property_unproven:
                        resolution = R.UNRESOLVED
                    operations.append(DynamicFormOperation(operation_id(source, OperationKind.PROPERTY_SET), OperationKind.PROPERTY_SET,
                        source, proc.name, target, resolution, property_kind=_PROPERTIES[_word(chain[-1])], value=value,
                        conditions=refs, condition_context_complete=complete, provenance=('direct_form_module_syntax',) + (('local_binding_analysis',) if receiver_proofs is not None else ()) + (('unproven_form_property',) if form_property_unproven else ())))
                    i = end + 1 if end < len(body) and _symbol(body[end], ';') else end
                    continue
            i = chain_end
    if len(operations) > _MAX_RECORDS:
        raise _Failure('unsupported', 'operation_budget')
    return DynamicOperationIndex(tuple(operations), conditions)
