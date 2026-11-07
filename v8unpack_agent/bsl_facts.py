"""Bounded syntactic BSL facts with snapshot-bound evidence (#481).

This is a declaration scanner, not a compiler/AST or prompt-injection detector.
Conditional compilation and unsupported syntax never become a complete empty list.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    validate_relative_module_path,
)

__all__ = [
    'EXTRACTOR_VERSION',
    'DeclarationFact',
    'LiteralFact',
    'ModuleFacts',
    'SourceSnapshot',
    'analyze_indexed_module',
    'analyze_module',
]

EXTRACTOR_VERSION = 'bsl-facts/1.0'
Status = Literal['complete', 'unsupported', 'incomplete', 'unavailable']
Kind = Literal['function', 'procedure']
DEFAULT_MAX_CHARS = 2_000_000


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(*parts: Any) -> str:
    return _sha(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable host-selected source. Identity/integrity is not author authority."""
    module_id: str
    relative_path: str
    text: str = field(repr=False)
    raw: bytes | None = field(default=None, repr=False)
    normalization: str = 'text_only'
    text_sha256: str = field(init=False)
    bytes_sha256: str | None = field(init=False)
    source_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.module_id, str) or not isinstance(self.text, str):
            raise TypeError('module_id and text must be str')
        validate_relative_module_path(self.relative_path)
        if self.raw is not None and not isinstance(self.raw, bytes):
            raise TypeError('raw must be bytes or None')
        try:
            text_hash = _sha(self.text.encode('utf-8'))
        except UnicodeError:
            raise ValueError('text must be UTF-8 representable') from None
        if self.raw is not None:
            try:
                decoded = self.raw.decode('utf-8')
            except UnicodeError:
                raise ValueError('raw must be UTF-8') from None
            forms = {'utf8_exact': decoded, 'utf8_sig': decoded.removeprefix('\ufeff'),
                     'utf8_sig_lf': decoded.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')}
            normalization = next((key for key, value in forms.items() if value == self.text), None)
            if normalization is None or self.normalization not in {'text_only', normalization}:
                raise ValueError('snapshot text does not match bytes')
            object.__setattr__(self, 'normalization', normalization)
        elif self.normalization != 'text_only':
            raise ValueError('byte normalization requires bytes')
        byte_hash = _sha(self.raw) if self.raw is not None else None
        object.__setattr__(self, 'text_sha256', text_hash)
        object.__setattr__(self, 'bytes_sha256', byte_hash)
        object.__setattr__(self, 'source_id', _identity('bsl-source/1', self.module_id,
                           self.relative_path, text_hash, byte_hash, self.normalization))

    def to_dict(self) -> dict[str, Any]:
        """Metadata only: raw text/bytes are not sent to a model implicitly."""
        return {'source_id': self.source_id, 'module_id': self.module_id,
                'relative_path': self.relative_path, 'text_sha256': self.text_sha256,
                'bytes_sha256': self.bytes_sha256, 'normalization': self.normalization}


@dataclass(frozen=True)
class LiteralFact:
    status: Literal['complete', 'unsupported']
    reason: str | None
    value: str | None = None
    fact_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'status': self.status, 'reason': self.reason, 'value': self.value,
                'fact_id': self.fact_id, 'start_line': self.start_line, 'end_line': self.end_line}


@dataclass(frozen=True)
class DeclarationFact:
    fact_id: str
    kind: Kind
    name: str
    exported: bool
    asynchronous: bool
    parameters: tuple[str, ...]
    start_line: int
    header_end_line: int
    end_line: int
    returned_string: LiteralFact

    def to_dict(self) -> dict[str, Any]:
        return {'fact_id': self.fact_id, 'kind': self.kind, 'name': self.name,
                'exported': self.exported, 'asynchronous': self.asynchronous,
                'parameters': list(self.parameters), 'start_line': self.start_line,
                'header_end_line': self.header_end_line, 'end_line': self.end_line,
                'returned_string': self.returned_string.to_dict()}


@dataclass(frozen=True)
class ModuleFacts:
    module_id: str
    status: Status
    reason: str | None
    source: SourceSnapshot | None = field(default=None, repr=False)
    declarations: tuple[DeclarationFact, ...] = ()
    extractor_version: str = EXTRACTOR_VERSION

    @property
    def exported_function_names(self) -> tuple[str, ...] | None:
        """None means unknown, () means a proven empty syntactic inventory."""
        if self.status != 'complete':
            return None
        return tuple(d.name for d in self.declarations if d.kind == 'function' and d.exported)

    def to_dict(self) -> dict[str, Any]:
        exports = self.exported_function_names
        return {'schema': 'bsl_facts/1', 'extractor_version': self.extractor_version,
                'module_id': self.module_id, 'status': self.status, 'reason': self.reason,
                'source': self.source.to_dict() if self.source is not None else None,
                'declarations': [d.to_dict() for d in self.declarations],
                'exported_function_names': list(exports) if exports is not None else None}


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int
    multiline: bool = False


class _Failure(Exception):
    def __init__(self, status: Status, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


_START = {'функция': 'function', 'function': 'function', 'процедура': 'procedure', 'procedure': 'procedure'}
_END = {'конецфункции': 'function', 'endfunction': 'function', 'конецпроцедуры': 'procedure', 'endprocedure': 'procedure'}
_ASYNC = {'асинх', 'async'}
_EXPORT = {'экспорт', 'export'}
_VAL = {'знач', 'val'}
_RETURN = {'возврат', 'return'}
_CONST = {'истина', 'true', 'ложь', 'false', 'неопределено', 'undefined', 'null'}
_RESERVED = set(_START) | set(_END) | _ASYNC | _EXPORT | _VAL | _RETURN | _CONST | {'перем', 'var', 'если', 'if', 'тогда', 'then', 'иначе', 'else', 'иначеесли', 'elsif', 'конецесли', 'endif', 'для', 'for', 'каждого', 'each', 'из', 'in', 'по', 'to', 'пока', 'while', 'цикл', 'do', 'конеццикла', 'enddo', 'попытка', 'try', 'исключение', 'except', 'конецпопытки', 'endtry', 'вызватьисключение', 'raise', 'продолжить', 'continue', 'прервать', 'break', 'перейти', 'goto', 'новый', 'new', 'выполнить', 'execute', 'добавитьобработчик', 'addhandler', 'удалитьобработчик', 'removehandler', 'и', 'and', 'или', 'or', 'не', 'not', 'ждать', 'await'}
_ANNOTATIONS = {'наклиенте', 'насервере', 'насерверебезконтекста', 'наклиентенасервере',
                'наклиентенасерверебезконтекста', 'atclient', 'atserver', 'atservernocontext',
                'atclientatserver', 'atclientatservernocontext'}
_NUMBER = re.compile(r'[0-9]+(?:\.[0-9]+)?')
_NEWLINE = re.compile(r'[\r\n]')


def _letter(char: str) -> bool:
    return char == '_' or unicodedata.category(char).startswith('L')


def _tokens(text: str) -> list[_Token]:
    tokens = []
    i = 1 if text.startswith('\ufeff') else 0
    line_start = i
    regions = 0
    while i < len(text):
        char = text[i]
        if char in ' \t\r\n':
            if char == '\r' and i + 1 < len(text) and text[i + 1] == '\n':
                i += 1
            i += 1
            if char in '\r\n':
                line_start = i
            continue
        if text.startswith('//', i):
            match = _NEWLINE.search(text, i)
            i = match.start() if match else len(text)
            continue
        start = i
        if char in '#&':
            # Directives must occupy a line; source inside a literal never reaches here.
            if text[line_start:i].strip():
                raise _Failure('unsupported', 'inline_directive')
            match = _NEWLINE.search(text, i)
            end = match.start() if match else len(text)
            directive = text[i + 1:end].split('//', 1)[0].strip().split()
            if not directive:
                raise _Failure('incomplete', 'invalid_directive')
            word = directive[0].casefold()
            if char == '#':
                if word in {'область', 'region'} and len(directive) == 2:
                    regions += 1
                elif word in {'конецобласти', 'endregion'} and len(directive) == 1 and regions:
                    regions -= 1
                else:
                    raise _Failure('unsupported', 'conditional_or_unknown_directive')
            elif word not in _ANNOTATIONS or len(directive) != 1:
                raise _Failure('unsupported', 'unsupported_annotation')
            tokens.append(_Token('directive', word, start, end))
            i = end
            continue
        if char == '"':
            i += 1
            value = []
            multiline = False
            while i < len(text):
                char = text[i]
                if char == '"':
                    if i + 1 < len(text) and text[i + 1] == '"':
                        value.append('"');i += 2
                        continue
                    i += 1
                    tokens.append(_Token('string', ''.join(value), start, i, multiline))
                    break
                if char in '\r\n':
                    multiline = True
                    if char == '\r' and i + 1 < len(text) and text[i + 1] == '\n':
                        i += 1
                    i += 1
                    line_start = i
                    while i < len(text) and text[i] in ' \t':
                        i += 1
                    if i >= len(text) or text[i] != '|':
                        raise _Failure('incomplete', 'invalid_string_continuation')
                    value.append('\n');i += 1
                else:
                    value.append(char);i += 1
            else:
                raise _Failure('incomplete', 'unclosed_string')
            continue
        if char == "'":
            end = text.find("'", i + 1)
            if end < 0 or not text[i + 1:end] or not all(c in '0123456789' for c in text[i + 1:end]):
                raise _Failure('unsupported', 'invalid_date_literal')
            i = end + 1;tokens.append(_Token('date', text[start:i], start, i));continue
        if _letter(char):
            i += 1
            while i < len(text) and (_letter(text[i]) or text[i] in '0123456789'):
                i += 1
            tokens.append(_Token('word', text[start:i], start, i));continue
        if char in '0123456789':
            match = _NUMBER.match(text, i)
            assert match is not None
            i = match.end();tokens.append(_Token('number', match[0], start, i));continue
        if char in '()[],;.=+-*/%<>?:~':
            i += 1;tokens.append(_Token('symbol', char, start, i));continue
        raise _Failure('unsupported', 'unsupported_character')
    if regions:
        raise _Failure('incomplete', 'unclosed_region')
    return tokens


def _name(token: _Token) -> bool:
    return token.kind == 'word' and token.value.casefold() not in _RESERVED


def _symbol(token: _Token, value: str) -> bool:
    return token.kind == 'symbol' and token.value == value


def _parameters(tokens: list[_Token], i: int) -> tuple[tuple[str, ...], int]:
    names: list[str] = []
    seen: set[str] = set()
    if i < len(tokens) and _symbol(tokens[i], ')'):
        return (), i + 1
    while i < len(tokens):
        if tokens[i].kind == 'word' and tokens[i].value.casefold() in _VAL:
            i += 1
        if i >= len(tokens) or not _name(tokens[i]):
            raise _Failure('unsupported', 'unsupported_parameter')
        if tokens[i].value.casefold() in seen:
            raise _Failure('unsupported', 'duplicate_parameter')
        seen.add(tokens[i].value.casefold())
        names.append(tokens[i].value);i += 1
        if i < len(tokens) and _symbol(tokens[i], '='):
            i += 1
            if i < len(tokens) and tokens[i].kind == 'symbol' and tokens[i].value in {'+', '-'}:
                i += 1
                if i >= len(tokens) or tokens[i].kind != 'number':
                    raise _Failure('unsupported', 'unsupported_default')
            if i >= len(tokens) or not (tokens[i].kind in {'number', 'string', 'date'} or
                                       tokens[i].value.casefold() in _CONST):
                raise _Failure('unsupported', 'unsupported_default')
            string = tokens[i].kind == 'string';i += 1
            while string and i < len(tokens) and tokens[i].kind == 'string':
                i += 1
        if i < len(tokens) and _symbol(tokens[i], ')'):
            return tuple(names), i + 1
        if i >= len(tokens) or not _symbol(tokens[i], ','):
            raise _Failure('incomplete', 'unclosed_or_invalid_parameters')
        i += 1
    raise _Failure('incomplete', 'unclosed_parameters')


def _scan(snapshot: SourceSnapshot) -> tuple[DeclarationFact, ...]:
    tokens = _tokens(snapshot.text)
    if not tokens:
        return ()
    line_starts = [0] + [m.end() for m in re.finditer(r'\r\n|\r|\n', snapshot.text)]
    def line(pos: int) -> int:
        return bisect.bisect_right(line_starts, pos)
    definitions: list[DeclarationFact] = []
    seen: set[str] = set()
    i = 0
    module_code = False
    while i < len(tokens):
        token = tokens[i];word = token.value.casefold()
        if i and _symbol(tokens[i - 1], '.') and token.kind == 'word' and word in (set(_START) | set(_END) | _ASYNC):
            raise _Failure('unsupported', 'reserved_member_name')
        if token.kind == 'directive':
            i += 1;continue
        if token.kind == 'word' and word in {'var', 'перем'}:
            if definitions or module_code:
                raise _Failure('unsupported', 'module_variable_position')
            i += 1
            while True:
                if i >= len(tokens) or not _name(tokens[i]):
                    raise _Failure('unsupported', 'unsupported_module_variable')
                i += 1
                if i < len(tokens) and tokens[i].kind == 'word' and tokens[i].value.casefold() in _EXPORT:
                    i += 1
                if i < len(tokens) and _symbol(tokens[i], ';'):
                    i += 1
                    break
                if i >= len(tokens) or not _symbol(tokens[i], ','):
                    raise _Failure('unsupported', 'unsupported_module_variable')
                i += 1
            continue
        asynchronous = word in _ASYNC and token.kind == 'word'
        if asynchronous:
            start = token.start;i += 1
            if i >= len(tokens) or tokens[i].value.casefold() not in _START:
                raise _Failure('unsupported', 'async_without_declaration')
            token = tokens[i];word = token.value.casefold()
        if i and _symbol(tokens[i - 1], '.') and token.kind == 'word' and word in (set(_START) | set(_END) | _ASYNC):
            raise _Failure('unsupported', 'reserved_member_name')
        if not asynchronous:
            start = token.start
        if token.kind != 'word' or word not in _START:
            if token.kind == 'word' and word in _END:
                raise _Failure('incomplete', 'unexpected_end')
            if not _symbol(token, ';'):
                module_code = True
            i += 1;continue
        if module_code:
            raise _Failure('unsupported', 'declaration_after_module_code')
        kind: Kind = 'function' if _START[word] == 'function' else 'procedure'
        i += 1
        if i >= len(tokens) or not _name(tokens[i]):
            raise _Failure('incomplete', 'invalid_declaration_name')
        name = tokens[i].value;i += 1
        if name.casefold() in seen:
            raise _Failure('unsupported', 'duplicate_declaration')
        seen.add(name.casefold())
        if i >= len(tokens) or not _symbol(tokens[i], '('):
            raise _Failure('incomplete', 'missing_parameters')
        params, i = _parameters(tokens, i + 1)
        exported = i < len(tokens) and tokens[i].value.casefold() in _EXPORT and tokens[i].kind == 'word'
        if exported:
            i += 1
        header_end = tokens[i - 1].end - 1
        body_start = i
        while i < len(tokens) and not (tokens[i].kind == 'word' and tokens[i].value.casefold() in _END):
            if tokens[i].kind == 'word' and tokens[i].value.casefold() in (set(_START) | _ASYNC):
                raise _Failure('incomplete', 'nested_declaration')
            i += 1
        if i >= len(tokens):
            raise _Failure('incomplete', 'unclosed_declaration')
        if i and _symbol(tokens[i - 1], '.'):
            raise _Failure('unsupported', 'reserved_member_name')
        if _END[tokens[i].value.casefold()] != kind:
            raise _Failure('incomplete', 'mismatched_end')
        body = tokens[body_start:i]
        end = tokens[i].end - 1
        literal = LiteralFact('unsupported', 'not_direct_literal_return')
        if kind == 'function' and len(body) in (2, 3) and body[0].value.casefold() in _RETURN and body[0].kind == 'word' and body[1].kind == 'string' and (len(body) == 2 or _symbol(body[2], ';')):
            value = body[1]
            if not value.multiline:
                literal = LiteralFact('complete', None, value.value,
                    _identity(EXTRACTOR_VERSION, snapshot.source_id, 'returned_string', value.start, value.end, value.value),
                    line(value.start), line(value.end - 1))
        fact_id = _identity(EXTRACTOR_VERSION, snapshot.source_id, kind, name, exported, start, end)
        definitions.append(DeclarationFact(fact_id, kind, name, exported, asynchronous, params,
                                           line(start), line(header_end), line(end), literal))
        i += 1
    return tuple(definitions)


def analyze_module(entry: ModuleEntry, bsl_text: str | None, *, bsl_bytes: bytes | None = None,
                   max_chars: int = DEFAULT_MAX_CHARS) -> ModuleFacts:
    """Extract supported syntactic facts without modifying or executing source.

    Complete describes declaration inventory, NOT successful BSL compilation.
    Unsupported literal bodies have their own status. Hashes bind a source;
    the host must still authorize the task and snapshot.
    """
    if not isinstance(entry, ModuleEntry):
        raise TypeError('entry must be ModuleEntry')
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < -1:
        raise ValueError('max_chars must be -1 or a nonnegative int')
    if bsl_bytes is not None and not isinstance(bsl_bytes, bytes):
        raise TypeError('bsl_bytes must be bytes or None')
    if bsl_text is not None and not isinstance(bsl_text, str):
        raise TypeError('bsl_text must be str or None')
    if entry.read_status in {'missing', 'read_error'} or bsl_text is None:
        return ModuleFacts(entry.module_id, 'unavailable', 'source_unavailable')
    normalization = 'text_only'
    if bsl_bytes is not None:
        if entry.sha256 is not None and _sha(bsl_bytes) != entry.sha256:
            return ModuleFacts(entry.module_id, 'incomplete', 'index_hash_mismatch')
        if entry.size_bytes is not None and len(bsl_bytes) != entry.size_bytes:
            return ModuleFacts(entry.module_id, 'incomplete', 'index_size_mismatch')
        try:
            decoded = bsl_bytes.decode('utf-8')
        except UnicodeError:
            return ModuleFacts(entry.module_id, 'unavailable', 'encoding_error')
        forms = {'utf8_exact': decoded, 'utf8_sig': decoded.removeprefix('\ufeff'),
                 'utf8_sig_lf': decoded.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')}
        normalization = next((name for name, text in forms.items() if bsl_text == text), '')
        if not normalization:
            return ModuleFacts(entry.module_id, 'incomplete', 'text_bytes_mismatch')
    try:
        snapshot = SourceSnapshot(entry.module_id, entry.relative_path, bsl_text, bsl_bytes, normalization)
    except ValueError:
        return ModuleFacts(entry.module_id, 'unsupported', 'invalid_unicode')
    if max_chars != -1 and len(bsl_text) > max_chars:
        return ModuleFacts(entry.module_id, 'incomplete', 'analysis_budget', snapshot)
    try:
        declarations = _scan(snapshot)
    except _Failure as exc:
        return ModuleFacts(entry.module_id, exc.status, exc.reason, snapshot)
    return ModuleFacts(entry.module_id, 'complete', None, snapshot, declarations)


def analyze_indexed_module(root: Path, index: ModuleIndex, module_id: str, *,
                           max_chars: int = DEFAULT_MAX_CHARS) -> ModuleFacts:
    """Read only an indexed BSL path; require a byte hash and reject symlinks.

    The host owns index/scope. This is not an OS sandbox against another
    process with the same filesystem rights; snapshot/currentness is checked
    again by the action executor.
    """
    if not isinstance(index, ModuleIndex):
        raise TypeError('index must be ModuleIndex')
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < -1:
        raise ValueError('max_chars must be -1 or a nonnegative int')
    entry = index.get(module_id)
    if entry is None:
        return ModuleFacts(module_id, 'unavailable', 'module_not_indexed')
    if entry.sha256 is None:
        return ModuleFacts(module_id, 'unavailable', 'index_hash_required')
    try:
        base = Path(root).resolve(strict=True)
        candidate = base.joinpath(*entry.path.parts)
        part = candidate
        while part != base:
            if part.is_symlink():
                return ModuleFacts(module_id, 'unavailable', 'symlink_source')
            part = part.parent
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(base) or not resolved.is_file():
            return ModuleFacts(module_id, 'unavailable', 'source_outside_root')
        with resolved.open('rb') as reader:
            raw = reader.read(max_chars * 4 + 4 if max_chars != -1 else -1)
        if max_chars != -1 and len(raw) > max_chars * 4 + 3:
            return ModuleFacts(module_id, 'incomplete', 'analysis_budget')
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        return ModuleFacts(module_id, 'unavailable', 'encoding_error')
    except (OSError, ValueError, RuntimeError):
        return ModuleFacts(module_id, 'unavailable', 'source_read_error')
    return analyze_module(entry, text, bsl_bytes=raw, max_chars=max_chars)
