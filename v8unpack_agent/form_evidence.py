"""Opt-in static top-level managed form names from real export bytes (#500).

No filesystem/model/writes, runtime interpretation, arbitrary metadata resolver
or original-CF completeness claim. Existing best-effort APIs are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from v8unpack_agent.elem_parser import _extract_props

__all__ = [
    'FormContract',
    'FormEvidence',
    'FormFact',
    'FormSource',
    'FormValidation',
    'analyze_form',
    'make_form_report',
    'validate_form_report',
]

MAX_BYTES = 2_000_000
MAX_FACTS = 1000
Status = Literal['complete', 'incomplete', 'unsupported', 'unavailable']
DETACHED = 'Дочерние элементы отдельно'
OPERATION = 'declared_form_attributes'


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(*parts: Any) -> str:
    return _sha(json.dumps(parts, ensure_ascii=False, separators=(',', ':'),
                           allow_nan=False).encode('utf-8'))


@dataclass(frozen=True)
class FormSource:
    """Two immutable host-selected files; form_id is a host namespace, not authority."""
    form_id: str
    metadata_raw: bytes | None = field(repr=False)
    elements_raw: bytes | None = field(repr=False)
    source_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.form_id) is not str or not 0 < len(self.form_id) <= 128:
            raise ValueError('host form namespace required')
        self.form_id.encode('utf-8')
        for raw in (self.metadata_raw, self.elements_raw):
            if raw is not None and (type(raw) is not bytes or len(raw) > MAX_BYTES):
                raise ValueError('source requires bounded immutable bytes or unavailable')
        object.__setattr__(self, 'source_id', _identity('form-source/1', self.form_id,
                          self._hash(self.metadata_raw), self._hash(self.elements_raw)))

    @staticmethod
    def _hash(raw: bytes | None) -> str | None:
        return _sha(raw) if raw is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {'form_id': self.form_id, 'source_id': self.source_id,
                'metadata_sha256': self._hash(self.metadata_raw),
                'elements_sha256': self._hash(self.elements_raw)}


@dataclass(frozen=True)
class FormFact:
    name: str
    attribute_id: str
    fact_id: str
    pointer: str
    raw_pointer: str

    def to_dict(self) -> dict[str, Any]:
        return {'name': self.name, 'attribute_id': self.attribute_id, 'fact_id': self.fact_id,
                'artifact': 'elements', 'pointer': self.pointer, 'raw_pointer': self.raw_pointer,
                'value_semantics': 'untrusted_data'}


@dataclass(frozen=True)
class FormEvidence:
    source: FormSource
    status: Status
    reason: str | None
    facts: tuple[FormFact, ...] = ()
    total_facts: int = 0
    omitted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {'schema': 'static_form_evidence/1', 'operation': OPERATION,
                'source': self.source.to_dict(), 'status': self.status, 'reason': self.reason,
                'coverage': {'static_top_level_attributes': self.status,
                             'full_form': 'unknown', 'runtime': 'unsupported',
                             'nested_attributes': 'unsupported'},
                'facts': [f.to_dict() for f in self.facts],
                'total_facts': self.total_facts, 'omitted': self.omitted}

    def contract(self) -> FormContract:
        if self.status != 'complete':
            raise ValueError('full report requires complete static evidence')
        return FormContract(self.source)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _bad_constant(value: str) -> Any:
    raise ValueError('nonfinite JSON')


def _tree(value: Any, *, raw_only: bool = False) -> None:
    """Bound decoded depth/node count; no hidden dictionaries inside raw brace lists."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 40 or count > 200_000:
            raise ValueError('document budget')
        if type(item) is str:
            item.encode('utf-8')
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict and not raw_only:
            pending.extend((child, depth + 1) for pair in item.items() for child in pair)
        elif raw_only or (type(item) is float and not math.isfinite(item)):
            raise ValueError('invalid raw or JSON value')


def _decode(raw: bytes) -> Any:
    result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                        parse_constant=_bad_constant)
    _tree(result)
    return result


def analyze_form(source: FormSource, *, max_facts: int = MAX_FACTS,
                 max_bytes: int = MAX_BYTES) -> FormEvidence:
    """Strict preflight then existing elem parser; completeness is projection-only."""
    if not isinstance(source, FormSource):
        raise TypeError('host FormSource required')
    for limit, maximum in ((max_facts, MAX_FACTS), (max_bytes, MAX_BYTES)):
        if type(limit) is not int or not 0 <= limit <= maximum:
            raise ValueError('invalid host budget')
    def fail(status: Status, reason: str) -> FormEvidence:
        return FormEvidence(source, status, reason)
    if source.metadata_raw is None or source.elements_raw is None:
        return fail('unavailable', 'source_unavailable')
    if max(len(source.metadata_raw), len(source.elements_raw)) > max_bytes:
        return fail('incomplete', 'byte_budget')
    try:
        metadata, elements = _decode(source.metadata_raw), _decode(source.elements_raw)
        if type(metadata) is not dict or type(elements) is not dict:
            return fail('incomplete', 'document_not_object')
        if 'attributes' in elements and 'props' not in elements:
            return fail('unsupported', 'artificial_attributes_adapter_unsupported')
        if 'Тип формы' not in metadata or 'Версия элементов формы' not in metadata:
            return fail('incomplete', 'format_metadata_missing')
        if metadata['Тип формы'] != '1' or metadata['Версия элементов формы'] != '1':
            return fail('unsupported', 'form_kind_or_version_unsupported')
        root = metadata['form'][0][0]
        if type(root) is not list or len(root) < 4:
            return fail('incomplete', 'raw_form_missing')
        if root[0] not in ('3', '4'):
            return fail('unsupported', 'raw_root_version_unsupported')
        section = root[3]
        if type(section) is not list or len(section) < 2:
            return fail('incomplete', 'raw_props_section_missing')
        props = elements['props']
        if props is None or props == []:
            if section[1] != '0':
                return fail('incomplete', 'empty_without_zero_counter')
            return FormEvidence(source, 'complete', None)
        if type(props) is not list or section[1] != DETACHED:
            return fail('incomplete', 'props_not_completely_detached')
        names: list[str] = []
        identifiers: list[str] = []
        for prop in props:
            if type(prop) is not dict:
                return fail('incomplete', 'invalid_property_record')
            if 'child' in prop or (type(prop.get('raw')) is list and len(prop['raw']) > 13
                                   and prop['raw'][13] != '0'):
                return fail('unsupported', 'nested_attributes_unsupported')
            if set(prop) != {'name', 'id', 'raw'}:
                return fail('incomplete', 'invalid_property_fields')
            name, ident, raw = prop['name'], prop['id'], prop['raw']
            if type(name) is not str or not 0 < len(name) <= 4096:
                return fail('incomplete', 'invalid_name')
            if type(ident) is not str or re.fullmatch(r'(?:0|[1-9][0-9]{0,19})', ident) is None:
                return fail('incomplete', 'invalid_attribute_id')
            if type(raw) is not list or len(raw) != 16:
                return fail('incomplete', 'invalid_raw_property')
            _tree(raw, raw_only=True)
            if raw[0] != '9':
                return fail('unsupported', 'raw_property_version_unsupported')
            if raw[1] != [ident] or raw[3] != '"' + name + '"':
                return fail('incomplete', 'raw_projection_mismatch')
            names.append(name)
            identifiers.append(ident)
        if len(set(names)) != len(names) or len(set(identifiers)) != len(identifiers):
            return fail('incomplete', 'duplicate_attribute')
        # Existing parser is best-effort; exact cardinality/order is checked.
        # Raw subtrees contain only brace-list strings, so they cannot inject
        # extra named dict records into its recursive walk.
        parsed = _extract_props(props)
        if [p.get('name') for p in parsed] != names:
            return fail('incomplete', 'parser_projection_mismatch')
        facts = tuple(FormFact(name, ident,
                      _identity('form-attribute/1', source.source_id, index, ident, name),
                      f'/props/{index}/name', f'/props/{index}/raw/3')
                      for index, (name, ident) in enumerate(zip(names, identifiers)))
        omitted = max(0, len(facts) - max_facts)
        return FormEvidence(source, 'incomplete' if omitted else 'complete',
                            'fact_budget' if omitted else None, facts[:max_facts], len(facts), omitted)
    except (ValueError, UnicodeError, KeyError, IndexError, TypeError, RecursionError):
        return fail('incomplete', 'invalid_or_partial_document')


@dataclass(frozen=True)
class FormContract:
    """Host-owned grant input; source flags/hashes are never authorization."""
    source: FormSource

    def __post_init__(self) -> None:
        if analyze_form(self.source).status != 'complete':
            raise ValueError('form operation unsupported or incomplete')


@dataclass(frozen=True)
class FormValidation:
    allowed: bool
    reason: str
    canonical_report: str | None = None


def make_form_report(contract: FormContract) -> dict[str, Any]:
    """Deterministic host result; not a model success."""
    if not isinstance(contract, FormContract):
        raise TypeError('host FormContract required')
    evidence = analyze_form(contract.source)
    if evidence.status != 'complete':
        raise ValueError('source no longer satisfies static contract')
    return {'attribute_names': [f.name for f in evidence.facts]}


def validate_form_report(contract: FormContract, report: Any) -> FormValidation:
    """Re-extract from immutable host bytes; do not trust supplied facts or scope."""
    expected = make_form_report(contract)
    if type(report) is not dict or set(report) != {'attribute_names'}:
        return FormValidation(False, 'invalid_report_fields')
    names = report['attribute_names']
    if type(names) is not list or any(type(n) is not str for n in names):
        return FormValidation(False, 'invalid_report_types')
    if names != expected['attribute_names']:
        return FormValidation(False, 'report_mismatch')
    return FormValidation(True, 'validated_static_snapshot',
                          json.dumps(expected, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False) + '\n')
