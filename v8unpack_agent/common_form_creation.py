"""Trace explicitly forwarded form references to common-module element creation."""
from __future__ import annotations

import bisect
import heapq
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from v8unpack_agent.bsl_facts import _END, _START, _Failure, _symbol, _Token
from v8unpack_agent.dynamic_forms_detector import (
    _SELF,
    _arguments,
    _bindings,
    _dynamic_tokens,
    _word,
)

__all__ = ['CommonFormCreationAnalysis', 'CommonFormCreationSummary', 'CommonFormIndex']

_Kind = Literal['form', 'elements']
_ProcedureId = tuple[str, int]
_State = tuple[_ProcedureId, int, _Kind]
_ELEMENTS = {'элементы', 'items'}
_ADD = {'добавить': 'add', 'add': 'add', 'вставить': 'insert', 'insert': 'insert'}


@dataclass(frozen=True, order=True)
class _Site:
    module: str
    procedure: str
    line: int
    offset: int


@dataclass(frozen=True)
class _Call:
    site: _Site
    qualifier: str
    name: str
    args: tuple[tuple[str, ...], ...]
    compiler_conditional: bool


@dataclass(frozen=True)
class _Creation:
    site: _Site
    action: str
    receiver: tuple[str, ...]
    compiler_conditional: bool


@dataclass(frozen=True)
class _Procedure:
    name: str
    parameters: tuple[str, ...]
    required: tuple[bool, ...]
    written: frozenset[str]
    shadowed: frozenset[str]
    exported: bool
    calls: tuple[_Call, ...]
    creations: tuple[_Creation, ...]


@dataclass(frozen=True)
class _Witness:
    action: str
    site: _Site
    calls: tuple[_Site, ...]
    module_hops: int
    compiler_conditional: bool

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), 'depth': len(self.calls), 'runtime_execution': 'unknown'}


@dataclass(frozen=True)
class CommonFormCreationAnalysis:
    """Static evidence and source-free diagnostics for the intermodule pass."""

    max_depth: int | None
    witnesses: tuple[_Witness, ...] = ()
    diagnostics: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        _validate_depth(self.max_depth)

    def to_dict(self) -> dict[str, object]:
        return {
            'schema': 'common_form_creation/1',
            'max_depth': self.max_depth,
            'operations': [operation.to_dict() for operation in self.witnesses],
            'diagnostics': dict(self.diagnostics),
        }


@dataclass(frozen=True)
class CommonFormCreationSummary:
    """Numeric runner summary; source evidence stays in FormContext."""

    forms: int = 0
    forms_with_operations: int = 0
    operations: int = 0
    forms_at_depth_limit: int = 0
    max_observed_depth: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in asdict(self).values()):
            raise ValueError('common creation counters must be nonnegative integers')
        if max(self.forms_with_operations, self.forms_at_depth_limit) > self.forms:
            raise ValueError('invalid common creation form totals')
        if self.forms_with_operations > self.operations or self.max_observed_depth > 0 and not self.operations:
            raise ValueError('invalid common creation operation totals')

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class _CommonCreationCollector:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._counts: Counter[str] = Counter()

    def add(self, key: str, analysis: CommonFormCreationAnalysis | None) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self._counts['forms'] += 1
        if analysis is None:
            return
        self._counts['forms_with_operations'] += int(bool(analysis.witnesses))
        self._counts['operations'] += len(analysis.witnesses)
        self._counts['forms_at_depth_limit'] += int('depth_limit_reached' in dict(analysis.diagnostics))
        self._counts['max_observed_depth'] = max(
            self._counts['max_observed_depth'], max((len(w.calls) for w in analysis.witnesses), default=0),
        )

    def summarize(self) -> CommonFormCreationSummary:
        return CommonFormCreationSummary(**self._counts)


def _validate_depth(max_depth: int | None) -> None:
    if max_depth is not None and (type(max_depth) is not int or max_depth < 0):
        raise ValueError('max_depth must be a nonnegative integer or None')


def _words(tokens: tuple[_Token, ...]) -> tuple[str, ...]:
    return tuple(_word(token) if token.kind == 'word' else
                 token.value if token.kind == 'symbol' else '<literal>' for token in tokens)


def _parse_module(text: str, module: str) -> dict[_ProcedureId, _Procedure]:
    tokens, directives = _dynamic_tokens(text, allow_formatted_dates=True)
    lines = [i for i, char in enumerate(text) if char == '\n']
    guard_positions = []
    guard_depths = []
    depth = 0
    for directive in directives:
        if directive.word in {'если', 'if'}:
            depth += 1
        elif directive.word in {'конецесли', 'endif'}:
            depth -= 1
        guard_positions.append(directive.start)
        guard_depths.append(depth)

    def conditional(offset: int) -> bool:
        i = bisect.bisect_right(guard_positions, offset)
        return i > 0 and bool(guard_depths[i - 1])

    def boundary_word(index: int) -> str:
        if index and _symbol(tokens[index - 1], '.'):
            return ''
        return _word(tokens[index])

    # First extract procedure boundaries and the module's bindings.
    ranges = []
    top = []
    i = 0
    while i < len(tokens):
        word = boundary_word(i)
        if word not in _START:
            if word in _END:
                raise _Failure('incomplete', 'unexpected_procedure_end')
            top.append(tokens[i])
            i += 1
            continue
        start = tokens[i].start
        if i + 2 >= len(tokens) or not _word(tokens[i + 1]) or not _symbol(tokens[i + 2], '('):
            raise _Failure('incomplete', 'invalid_procedure_header')
        name = _word(tokens[i + 1])
        args, end = _arguments(tokens, i + 2)
        params = []
        required = []
        for arg in args:
            part = arg[1:] if arg and _word(arg[0]) in {'знач', 'val'} else arg
            if not part or not _word(part[0]) or (len(part) > 1 and not _symbol(part[1], '=')):
                raise _Failure('incomplete', 'invalid_parameter')
            params.append(_word(part[0]))
            required.append(len(part) == 1)
        if len(set(params)) != len(params):
            raise _Failure('incomplete', 'duplicate_parameter')
        i = end + 1
        exported = i < len(tokens) and _word(tokens[i]) in {'экспорт', 'export'}
        i += int(exported)
        begin = i
        while i < len(tokens) and boundary_word(i) not in _END:
            if boundary_word(i) in _START:
                raise _Failure('incomplete', 'nested_procedure')
            i += 1
        if i >= len(tokens) or _END[_word(tokens[i])] != _START[word]:
            raise _Failure('incomplete', 'unclosed_procedure')
        ranges.append((start, name, tuple(params), tuple(required), exported, begin, i))
        i += 1

    top_written = _bindings(tuple(top), (), module_level=True)
    result = {}
    for start, name, params_tuple, required_tuple, exported, begin, end in ranges:
        body = tuple(tokens[begin:end])
        written = _bindings(body, ())
        shadowed = top_written | written | frozenset(params_tuple)
        calls = []
        creations = []
        for index, token in enumerate(body[:-1]):
            word = _word(token)
            if not word or not _symbol(body[index + 1], '('):
                continue
            qualified = index >= 1 and _symbol(body[index - 1], '.')
            qualifier = _word(body[index - 2]) if qualified and index >= 2 else ''
            chained = qualified and index >= 3 and _symbol(body[index - 3], '.')
            # Skip complex receivers unless they have the shape Name.Items.
            if qualified and not qualifier:
                continue
            if chained and (word not in _ADD or index < 4 or qualifier not in _ELEMENTS
                            or not _word(body[index - 4])
                            or (index >= 5 and _symbol(body[index - 5], '.'))):
                continue
            # Constructors are not procedure calls.
            if not qualified and index and _word(body[index - 1]) in {'новый', 'new'}:
                continue
            site = _Site(module, name, bisect.bisect_left(lines, token.start) + 1, token.start)
            guarded = conditional(start) or conditional(token.start)
            args, _ = _arguments(body, index + 1)
            if qualified and word in _ADD:
                sizes = (2, 3) if _ADD[word] == 'add' else (3, 4)
                if len(args) in sizes and all(args[:2]):
                    receiver = (_word(body[index - 4]), '.', qualifier) if chained else (qualifier,)
                    creations.append(_Creation(site, _ADD[word], receiver, guarded))
            if not chained:
                calls.append(_Call(site, qualifier, word, tuple(map(_words, args)), guarded))
        result[(module, start)] = _Procedure(name, params_tuple, required_tuple,
                                            written, shadowed, exported, tuple(calls), tuple(creations))
    return result


def _reference(expression: tuple[str, ...], name: str, kind: _Kind) -> _Kind | None:
    if expression == (name,):
        return kind
    if kind == 'form' and len(expression) == 3 and expression[:2] == (name, '.') and expression[2] in _ELEMENTS:
        return 'elements'
    return None


def _resolve(procedures: dict[_ProcedureId, _Procedure]) -> dict[tuple[str, str], list[_ProcedureId]]:
    result: dict[tuple[str, str], list[_ProcedureId]] = defaultdict(list)
    for key, proc in procedures.items():
        result[(key[0], proc.name)].append(key)
    return result


class CommonFormIndex:
    """Reusable snapshot of common modules from one unpacked configuration."""

    def __init__(self, root: Path) -> None:
        self._procedures: dict[_ProcedureId, _Procedure] = {}
        self._modules: dict[str, list[str]] = defaultdict(list)
        self._diagnostics: Counter[str] = Counter()
        root = root.resolve()
        for metadata in sorted((root / 'CommonModule').glob('*/CommonModule.json')):
            source = metadata.with_name('CommonModule.obj.bsl')
            try:
                if any(not path.resolve().is_relative_to(root) for path in (metadata, source)):
                    self._diagnostics['common_module_outside_export'] += 1
                    continue
                name = json.loads(metadata.read_text(encoding='utf-8-sig'))['name'].casefold()
                module = source.relative_to(root).as_posix()
                self._modules[name].append(module)
                if not source.is_file():
                    continue
                text = source.read_bytes().decode('utf-8-sig')
                self._procedures.update(_parse_module(text, module))
            except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, _Failure):
                self._diagnostics['common_module_unreadable_or_unsupported'] += 1
        self._definitions = _resolve(self._procedures)

    def analyze(self, text: str | None, module: str, *, max_depth: int | None = None) -> CommonFormCreationAnalysis:
        """Follow explicit argument/parameter flow, retaining a shortest witness."""
        _validate_depth(max_depth)
        path = PurePosixPath(module)
        if (not path.parts or '\\' in module or path.is_absolute()
                or PureWindowsPath(module).drive or '..' in path.parts):
            raise ValueError('form module identifier must be relative to the export')
        diagnostics = self._diagnostics.copy()
        if text is None:
            diagnostics['form_bsl_unavailable'] += 1
            return CommonFormCreationAnalysis(max_depth, diagnostics=tuple(sorted(diagnostics.items())))
        try:
            form = _parse_module(text, module)
        except _Failure:
            diagnostics['form_bsl_unsupported'] += 1
            return CommonFormCreationAnalysis(max_depth, diagnostics=tuple(sorted(diagnostics.items())))
        return self._trace(form, module, max_depth, diagnostics)

    def _trace(self, form: dict[_ProcedureId, _Procedure], module: str, max_depth: int | None,
               diagnostics: Counter[str]) -> CommonFormCreationAnalysis:
        definitions = _resolve(form)
        pending: list[tuple[int, int, bool, tuple[_Site, ...], _State]] = []
        visited: set[_State] = set()
        hits: dict[_Site, _Witness] = {}
        truncated: set[_State] = set()
        unresolved: set[_Site] = set()

        def advance(proc: _Procedure, proc_module: str, name: str, kind: _Kind,
                    trail: tuple[_Site, ...], hops: int, conditional: bool) -> None:
            for call in proc.calls:
                forwarded = [(i, value) for i, arg in enumerate(call.args)
                             if (value := _reference(arg, name, kind)) is not None]
                if not forwarded:
                    continue
                if not call.qualifier or (proc_module == module and call.qualifier in _SELF
                                           and call.qualifier not in proc.shadowed):
                    choices = (definitions if proc_module == module else self._definitions).get(
                        (proc_module, call.name), [])
                elif call.qualifier not in proc.shadowed and call.qualifier in self._modules:
                    modules = self._modules[call.qualifier]
                    choices = ([key for key in self._definitions.get((modules[0], call.name), [])
                                if self._procedures[key].exported] if len(modules) == 1 else [])
                else:
                    unresolved.add(call.site)
                    continue
                if len(choices) != 1:
                    unresolved.add(call.site)
                    continue
                dest = choices[0]
                callee = form[dest] if dest in form else self._procedures[dest]
                if len(call.args) > len(callee.parameters) or any(
                    required and (i >= len(call.args) or not call.args[i])
                    for i, required in enumerate(callee.required)
                ):
                    unresolved.add(call.site)
                    continue
                for slot, value in forwarded:
                    state = (dest, slot, value)
                    if callee.parameters[slot] in callee.written:
                        unresolved.add(call.site)
                        continue
                    if max_depth is not None and len(trail) >= max_depth:
                        truncated.add(state)
                        continue
                    if state not in visited:
                        heapq.heappush(pending, (len(trail) + 1, hops + int(dest[0] != proc_module),
                                                 conditional or call.compiler_conditional,
                                                 (*trail, call.site), state))

        for proc in form.values():
            for name in sorted((_SELF | _ELEMENTS) - proc.shadowed):
                advance(proc, module, name, 'form' if name in _SELF else 'elements', (), 0, False)
        while pending:
            _, hops, conditional, trail, state = heapq.heappop(pending)
            if state in visited:
                continue
            visited.add(state)
            proc_id, slot, kind = state
            proc = form[proc_id] if proc_id in form else self._procedures[proc_id]
            name = proc.parameters[slot]
            if proc_id[0] != module:
                for creation in proc.creations:
                    if _reference(creation.receiver, name, kind) == 'elements':
                        hits.setdefault(creation.site, _Witness(
                            creation.action, creation.site, trail, hops,
                            conditional or creation.compiler_conditional,
                        ))
            advance(proc, proc_id[0], name, kind, trail, hops, conditional)
        if truncated - visited:
            diagnostics['depth_limit_reached'] = len(truncated - visited)
        if unresolved:
            diagnostics['unresolved_forwarding_sites'] = len(unresolved)
        if hits:
            diagnostics['by_reference_effects_unverified'] = len(hits)
        return CommonFormCreationAnalysis(max_depth, tuple(hits[key] for key in sorted(hits)),
                                          tuple(sorted(diagnostics.items())))
