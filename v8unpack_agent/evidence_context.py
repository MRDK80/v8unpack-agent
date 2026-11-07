"""Opt-in task-specific evidence; extracted strings never carry authority (#482)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from v8unpack_agent.bsl_facts import (
    EXTRACTOR_VERSION,
    ModuleFacts,
    SourceSnapshot,
    analyze_indexed_module,
)
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.search_context import SearchContext
from v8unpack_agent.untrusted_diagnostics import detect_untrusted_chars
from v8unpack_agent.untrusted_transport import encode_block

__all__ = [
    'EVIDENCE_CONTEXT_SCHEMA', 'EvidenceContext', 'EvidenceModule',
    'build_form_evidence', 'build_module_evidence', 'build_search_evidence',
    'evidence_fragment',
]
EVIDENCE_CONTEXT_SCHEMA = 'evidence_context/1'
Operation = Literal['exported_functions', 'returned_string']
Surface = Literal['module', 'form', 'search']


def _limit(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(name + ' must be a nonnegative integer')


@dataclass(frozen=True)
class EvidenceModule:
    """Host-produced projection. Full snapshot remains local, absent from JSON."""
    facts: ModuleFacts
    operation: Operation
    declaration_name: str | None = None
    max_facts: int = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.facts, ModuleFacts):
            raise TypeError('facts must be ModuleFacts')
        if self.operation not in {'exported_functions', 'returned_string'}:
            raise ValueError('unsupported operation')
        if self.operation == 'returned_string':
            if not isinstance(self.declaration_name, str) or not self.declaration_name:
                raise ValueError('returned_string requires a host-selected declaration_name')
        elif self.declaration_name is not None:
            raise ValueError('export inventory does not select one declaration')
        _limit(self.max_facts, 'max_facts')

    def to_dict(self) -> dict[str, Any]:
        f = self.facts
        status, reason = f.status, f.reason
        selected: list[dict[str, Any]] = []
        if status == 'complete':
            if self.operation == 'exported_functions':
                for d in f.declarations:
                    if d.kind == 'function' and d.exported:
                        selected.append({'kind': 'exported_function', 'name': d.name,
                            'fact_id': d.fact_id, 'start_line': d.start_line,
                            'end_line': d.end_line})
            else:
                declarations = [d for d in f.declarations if d.kind == 'function' and
                                d.name.casefold() == (self.declaration_name or '').casefold()]
                if len(declarations) != 1:
                    status, reason = 'incomplete', 'declaration_not_unique_or_missing'
                else:
                    d = declarations[0];lit = d.returned_string
                    if lit.status != 'complete':
                        status, reason = 'unsupported', lit.reason
                    else:
                        selected.append({'kind': 'returned_string', 'name': d.name,
                            'value': lit.value, 'fact_id': lit.fact_id,
                            'start_line': lit.start_line, 'end_line': lit.end_line})
        total = len(selected)
        omitted = max(0, total - self.max_facts)
        projection_status = 'truncated' if omitted else status
        if omitted:
            reason = 'fact_budget'
        source = f.source
        diagnostics: dict[str, Any] | None = None
        if source is not None:
            diag = detect_untrusted_chars(source.text, raw=source.raw)
            diagnostics = {'file_bom': diag.file_bom, 'cf_total': diag.total,
                           'counts': diag.counts, 'unidata_version': diag.unidata_version}
        return {'module_id': f.module_id, 'status': projection_status, 'reason': reason,
                'extractor_version': f.extractor_version,
                'source': source.to_dict() if source is not None else None,
                'facts': selected[:self.max_facts], 'facts_total': total,
                'facts_omitted': omitted, 'diagnostics': diagnostics}


@dataclass(frozen=True)
class EvidenceContext:
    surface: Surface
    operation: Operation
    modules: tuple[EvidenceModule, ...]
    selection_status: Literal['complete', 'truncated', 'unsupported'] = 'complete'
    omissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.surface not in {'module', 'form', 'search'}:
            raise ValueError('unsupported surface')
        if self.operation not in {'exported_functions', 'returned_string'}:
            raise ValueError('unsupported operation')
        if self.selection_status not in {'complete', 'truncated', 'unsupported'}:
            raise ValueError('unsupported selection status')
        if not isinstance(self.modules, tuple) or any(not isinstance(m, EvidenceModule) for m in self.modules):
            raise TypeError('modules must be a tuple of EvidenceModule')
        if any(m.operation != self.operation for m in self.modules):
            raise ValueError('operation mismatch')
        if len({m.facts.module_id for m in self.modules}) != len(self.modules):
            raise ValueError('duplicate module scope')

    def to_dict(self) -> dict[str, Any]:
        modules = [m.to_dict() for m in self.modules]
        statuses = [m['status'] for m in modules]
        status: str = self.selection_status
        if status == 'complete' and any(s != 'complete' for s in statuses):
            status = 'incomplete'
        return {'schema': EVIDENCE_CONTEXT_SCHEMA, 'surface': self.surface,
                'operation': self.operation, 'status': status,
                'scope': {'kind': 'selected_modules',
                          'module_ids': [m.facts.module_id for m in self.modules],
                          'selection_status': self.selection_status},
                'coverage': {'bsl': 'bounded_syntax', 'form_structure': 'unsupported'},
                'value_semantics': 'untrusted_data',
                'modules': modules, 'omissions': list(self.omissions)}


def build_module_evidence(root: Path, index: ModuleIndex, module_id: str, *,
                          operation: Operation = 'exported_functions',
                          declaration_name: str | None = None, max_facts: int = 1000,
                          max_chars: int = 2_000_000) -> EvidenceContext:
    """Select one complete indexed source, not a search slice or model summary."""
    facts = analyze_indexed_module(root, index, module_id, max_chars=max_chars)
    return EvidenceContext('module', operation,
        (EvidenceModule(facts, operation, declaration_name, max_facts),))


def build_form_evidence(root: Path, index: ModuleIndex, module_id: str, **kwargs: Any) -> EvidenceContext:
    """BSL of an indexed form ONLY. Form metadata/attributes remain unsupported."""
    entry = index.get(module_id)
    if entry is None or entry.module_kind != 'form':
        raise ValueError('host must select an indexed form module')
    context = build_module_evidence(root, index, module_id, **kwargs)
    return EvidenceContext('form', context.operation, context.modules)


def build_search_evidence(root: Path, index: ModuleIndex, context: SearchContext, *,
                          allowed_module_ids: tuple[str, ...], max_modules: int = 10,
                          operation: Operation = 'exported_functions',
                          declaration_name: str | None = None,
                          max_facts: int = 1000, max_chars: int = 2_000_000) -> EvidenceContext:
    """Project included module hits within host scope; re-read full indexed BSL.

    Complete means selected modules, never an exhaustive configuration search.
    Form/owner hits and omitted/truncated search results are explicit omissions.
    """
    if not isinstance(context, SearchContext):
        raise TypeError('context must be SearchContext')
    _limit(max_modules, 'max_modules')
    modules: list[EvidenceModule] = []
    seen: set[str] = set()
    omissions: list[str] = []
    if context.status != 'built' or context.omitted:
        omissions.append('search_selection_incomplete')
    for block in context.included:
        artifact = block.hit.artifact
        if block.candidate_only or artifact.artifact_kind != 'module':
            omissions.append('unsupported_search_artifact');continue
        module_id = artifact.artifact_id
        if module_id in seen:
            continue
        seen.add(module_id)
        if module_id not in allowed_module_ids:
            omissions.append('outside_host_scope');continue
        if len(modules) >= max_modules:
            omissions.append('module_budget');continue
        item = build_module_evidence(root, index, module_id, operation=operation,
            declaration_name=declaration_name, max_facts=max_facts, max_chars=max_chars)
        modules.extend(item.modules)
    return EvidenceContext('search', operation, tuple(modules),
                           'truncated' if omissions else 'complete', tuple(omissions))


def evidence_fragment(snapshot: SourceSnapshot, *, source_id: str,
                      start_line: int, end_line: int, max_chars: int = 10000) -> dict[str, Any]:
    """Separate read-only, explicitly untrusted source evidence with exact range.

    No file access. Snapshot must come from the host, not from model JSON.
    A budget refusal returns no text rather than a misleading complete slice.
    """
    if not isinstance(snapshot, SourceSnapshot):
        raise TypeError('snapshot must be SourceSnapshot')
    _limit(max_chars, 'max_chars')
    if snapshot.source_id != source_id:
        raise ValueError('snapshot mismatch')
    lines = re.findall(r'.*?(?:\r\n|\r|\n|$)', snapshot.text)
    if lines and not lines[-1]:
        lines.pop()
    if (isinstance(start_line, bool) or isinstance(end_line, bool) or
        not isinstance(start_line, int) or not isinstance(end_line, int) or
        not 1 <= start_line <= end_line <= len(lines)):
        raise ValueError('invalid evidence range')
    text = ''.join(lines[start_line - 1:end_line])
    too_large = len(text) > max_chars
    # The nested framed block covers its own local LF-based line numbering;
    # source_range separately uses scanner CR/CRLF/LF source coordinates.
    return {'schema': 'evidence_fragment/1', 'source': snapshot.to_dict(),
            'extractor_version': EXTRACTOR_VERSION,
            'source_range': {'start_line': start_line, 'end_line': end_line},
            'status': 'truncated' if too_large else 'complete',
            'reason': 'evidence_budget' if too_large else None,
            'value_semantics': 'untrusted_data',
            'text_sha256': None if too_large else hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'framed': None if too_large else encode_block(text, n=1, kind='bsl')}
