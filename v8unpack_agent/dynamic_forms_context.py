"""Opt-in form analysis, projection and source-free run counters (#217)."""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from v8unpack_agent.dynamic_forms_binding import DynamicBindingIndex

__all__ = ['DynamicAnalysisSummary', 'DynamicFormAnalysis', 'analyze_dynamic_form']

AnalysisStatus = Literal['complete', 'incomplete', 'unsupported', 'unavailable']


@dataclass(frozen=True)
class DynamicFormAnalysis:
    """Static operations, separate from the declared tree and runtime state."""

    status: AnalysisStatus
    reason: str | None = None
    index: DynamicBindingIndex | None = None

    def __post_init__(self) -> None:
        if self.reason is not None and (not isinstance(self.reason, str) or
                re.fullmatch(r'[a-z][a-z0-9_]{0,95}', self.reason) is None):
            raise ValueError('dynamic analysis requires a machine reason code')
        if self.index is not None and not isinstance(self.index, DynamicBindingIndex):
            raise TypeError('dynamic analysis requires a binding index')
        if self.status not in {'complete', 'incomplete', 'unsupported', 'unavailable'}:
            raise ValueError('invalid dynamic analysis status')
        if self.status == 'complete':
            if self.index is None or self.reason is not None:
                raise ValueError('complete analysis requires an index and no failure reason')
        elif self.index is not None or not self.reason:
            raise ValueError('failed analysis requires a reason and no fabricated index')

    def metrics(self) -> dict[str, int]:
        ops = () if self.index is None else self.index.bound.operations
        values = {name: 0 for name in ('operations', 'exact', 'partial', 'unresolved',
                                      'conditional', 'candidates')}
        values['operations'] = len(ops)
        for operation in ops:
            values[operation.operation_resolution.value] += 1
            values['conditional'] += int(bool(operation.conditions))
        values['candidates'] = 0 if self.index is None else len(self.index.candidates)
        return values

    def to_dict(self, *, include_expressions: bool = False) -> dict[str, Any]:
        return {
            'schema': 'dynamic_form_analysis/1',
            'status': self.status,
            'reason': self.reason,
            'runtime_state': 'not_reconstructed',
            'metrics': self.metrics(),
            'analysis': None if self.index is None else self.index.to_dict(
                include_expressions=include_expressions),
        }

    def projection(self) -> dict[str, Any]:
        """No raw/bound duplicate view; source-derived values remain untrusted."""
        rows = []
        if self.index is not None:
            for operation in self.index.bound.operations:
                row = operation.to_dict()
                row['conditional'] = bool(operation.conditions)
                row['runtime_execution'] = 'unknown'
                rows.append(row)
        return {
            'schema': 'dynamic_form_projection/1',
            'status': self.status, 'reason': self.reason,
            'runtime_state': 'not_reconstructed',
            'metrics': self.metrics(), 'operations': rows,
            'conditions': [] if self.index is None else [
                dict(condition.to_dict(), runtime_truth='unknown')
                for condition in self.index.bound.conditions],
            'candidates': [] if self.index is None else [
                candidate.to_dict() for candidate in self.index.candidates],
        }


def analyze_dynamic_form(
    text: str | None, relative_path: str, metadata_path: Path | None,
    elements_path: Path | None,
) -> DynamicFormAnalysis:
    """Unavailable inventories never become empty complete ones."""
    import json

    from v8unpack_agent.dynamic_forms_binding import bind_form_operations
    from v8unpack_agent.dynamic_forms_detector import DynamicAnalysisError
    from v8unpack_agent.elem_parser import _find_elem_json, parse_elem_json
    from v8unpack_agent.form_evidence import MAX_BYTES, FormSource, analyze_form

    if text is None:
        return DynamicFormAnalysis('unavailable', 'bsl_unavailable')
    if metadata_path is None or elements_path is None:
        return DynamicFormAnalysis('unavailable', 'declarations_unavailable')
    try:
        def read(path: Path) -> bytes:
            with path.open('rb') as stream:
                return stream.read(MAX_BYTES + 1)
        meta, elem = read(metadata_path), read(elements_path)
        if max(len(meta), len(elem)) > MAX_BYTES:
            return DynamicFormAnalysis('incomplete', 'declarations_byte_budget')
        evidence = analyze_form(FormSource('dynamic_context', meta, elem))
        if evidence.status != 'complete':
            return DynamicFormAnalysis(evidence.status, evidence.reason)
        # Evidence already rejected duplicate/nonfinite/malformed metadata.
        metadata = json.loads(meta)
        if metadata.get('Тип формы') != '1':
            return DynamicFormAnalysis('unsupported', 'ordinary_form')
        selected = _find_elem_json(elements_path.parent)
        if selected is None or selected.resolve() != elements_path.resolve():
            return DynamicFormAnalysis('incomplete', 'element_source_ambiguous')
        inventory = parse_elem_json(elements_path.parent, write_index=False)
        if not inventory.elem_index_ok:
            return DynamicFormAnalysis('incomplete', 'element_inventory_incomplete')
        if read(metadata_path) != meta or read(elements_path) != elem:
            return DynamicFormAnalysis('incomplete', 'declarations_snapshot_changed')
        elements = tuple(sorted({item['name'] for item in inventory.elements
                                 if item.get('source') in {'tree', 'data'}
                                 and isinstance(item.get('name'), str) and item['name']}))
        commands = tuple(sorted({item['name'] for item in inventory.elements
                                 if item.get('source') == 'commands'
                                 and isinstance(item.get('name'), str) and item['name']}))
        index = bind_form_operations(text, relative_path, declared_elements=elements,
                                    declared_commands=commands,
                                    declared_attributes=tuple(f.name for f in evidence.facts))
    except DynamicAnalysisError as exc:
        if exc.status == 'unsupported':
            return DynamicFormAnalysis('unsupported', exc.reason)
        return DynamicFormAnalysis('incomplete', exc.reason)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return DynamicFormAnalysis('incomplete', 'declarations_unreadable')
    return DynamicFormAnalysis('complete', index=index)


@dataclass(frozen=True)
class DynamicAnalysisSummary:
    """Source-free, deduplicated post-run counters for opted-in forms."""

    forms: int = 0
    complete: int = 0
    incomplete: int = 0
    unsupported: int = 0
    unavailable: int = 0
    failed: int = 0
    operations: int = 0
    exact: int = 0
    partial: int = 0
    unresolved: int = 0
    conditional: int = 0
    candidates: int = 0

    def __post_init__(self) -> None:
        from dataclasses import fields
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) < 0
               for f in fields(self)):
            raise ValueError('dynamic counters require nonnegative integers')
        if self.forms != self.complete + self.incomplete + self.unsupported + self.unavailable + self.failed:
            raise ValueError('dynamic form counters do not match')
        if self.operations != self.exact + self.partial + self.unresolved:
            raise ValueError('dynamic operation counters do not match')
        if self.conditional > self.operations:
            raise ValueError('conditional operations exceed total')

    def to_dict(self) -> dict[str, int]:
        from dataclasses import asdict
        return asdict(self)


class _DynamicAnalysisCollector:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._counts = DynamicAnalysisSummary().to_dict()

    def add(self, key: str, analysis: DynamicFormAnalysis | None) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self._counts['forms'] += 1
        if analysis is None:
            self._counts['failed'] += 1
        else:
            self._counts[analysis.status] += 1
            for name, value in analysis.metrics().items():
                self._counts[name] += value

    def summarize(self) -> DynamicAnalysisSummary:
        return DynamicAnalysisSummary(**self._counts)
