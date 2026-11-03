"""Резолюция ссылочных типов: остаток ``Ref#uuid`` и его классы (issue #288).

Логика классификации перенесена без изменений из
``examples/unresolved_refs_report.py`` (#143, #278), чтобы post-run report и
диагностический пример считали метрику одним кодом. UUID, имена и пути наружу
не выдаются: :class:`ReferenceResolutionSummary` содержит только счётчики.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from v8unpack_agent.platform_reference_types import PLATFORM_REFERENCE_TYPES

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


REF_PREFIX = "Ref#"


# Вторая ступень резолюции (#165) отдаёт каноническое машинное имя XDTO
# вида prefix:LocalName. Применимость проверяется точным членством в
# значениях доказанной статической таблицы, а не эвристикой по двоеточию.
PLATFORM_REFERENCE_TYPE_NAMES = frozenset(PLATFORM_REFERENCE_TYPES.values())


#: Ключи, допустимые к публикации в нормализованном указателе.
KNOWN_TAGS = frozenset({
    "header", "props", "params", "form", "data", "raw", "child", "children",
    "items", "elements", "attributes", "columns", "identity", "meta",
    "Properties", "TabularSections", "Attributes", "Columns", "Elements",
    "Name", "Type", "Synonym", "Fields", "Rows", "Value", "Values",
})


DEPTH_OBJECT = 14


DEPTH_OTHER = 6


RAW_INDEX_DEPTH = 5


IDENTITY_MIN_COVERAGE = 0.5


LAYOUT_MIN_COVERAGE = 0.05


MAX_HITS_PER_UUID = 1.5


DENSE_HITS_PER_UUID = 5.0


def is_reference_type(type_name: str) -> bool:
    return (
        type_name.startswith(REF_PREFIX)
        or "Ref." in type_name
        or type_name in PLATFORM_REFERENCE_TYPE_NAMES
    )


def file_role(parts) -> tuple[str, str]:
    """Роль файла и вид метаданных, определяемые структурно, без белых списков.

    ``<Вид>/<Имя>/<Вид>.json`` — файл объекта; вид берётся из первого сегмента.
    """
    if not parts:
        return "config_other", "<root>"
    kind = parts[0]
    if len(parts) == 3 and parts[2] == f"{kind}.json":
        return "object_metadata", kind
    if "Forms" in parts:
        return "form_file", kind
    if "Templates" in parts:
        return "template_file", kind
    if "Commands" in parts:
        return "command_file", kind
    if len(parts) >= 2:
        return "object_other", kind
    return "config_other", kind


def normalize_pointer(pointer, depth: int) -> str:
    tokens = []
    for token in pointer[:depth]:
        if isinstance(token, int):
            tokens.append("*")
        elif str(token) in KNOWN_TAGS:
            tokens.append(str(token))
        else:
            tokens.append("<key>")
    if len(pointer) > depth:
        tokens.append("...")
    return "/".join(tokens) or "<root>"


def raw_index_pointer(pointer) -> str:
    tokens = []
    for token in pointer[:RAW_INDEX_DEPTH]:
        if isinstance(token, int) or str(token) in KNOWN_TAGS:
            tokens.append(str(token))
        else:
            tokens.append("<key>")
    return "/".join(tokens) or "<root>"


def iter_named_types(node, section: str = "root"):
    """Пары (имя реквизита, тип, секция) на любой глубине object_attributes."""
    out = []
    if isinstance(node, dict):
        type_value = node.get("Type") or node.get("type")
        name_value = node.get("Name") or node.get("name")
        if isinstance(type_value, str) and type_value:
            out.append((name_value if isinstance(name_value, str) else "", type_value, section))
        for key, value in node.items():
            nested = key if key in {"Properties", "TabularSections", "Attributes", "Columns"} else section
            out.extend(iter_named_types(value, nested))
    elif isinstance(node, list):
        for value in node:
            out.extend(iter_named_types(value, section))
    return out


@dataclass
class Evidence:
    occurrences: int = 0
    positions: int = 0
    form_kinds: Counter = field(default_factory=Counter)
    attributes: Counter = field(default_factory=Counter)
    form_names: Counter = field(default_factory=Counter)
    sections: Counter = field(default_factory=Counter)
    roles: Counter = field(default_factory=Counter)
    slots: Counter = field(default_factory=Counter)
    slot_files: dict = field(default_factory=lambda: defaultdict(set))
    slot_kinds: dict = field(default_factory=lambda: defaultdict(Counter))
    raw_indices: Counter = field(default_factory=Counter)


def walk(node, targets, pointer, sink, collector=None) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, targets, [*pointer, key], sink, collector)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, targets, [*pointer, i], sink, collector)
    elif isinstance(node, str):
        is_ref = node.startswith(REF_PREFIX)
        candidate = node[len(REF_PREFIX):] if is_ref else node
        if not UUID_RE.match(candidate):
            return
        if collector is not None and len(pointer) <= RAW_INDEX_DEPTH:
            collector(raw_index_pointer(pointer), candidate)
        if candidate in targets:
            sink(candidate, pointer, is_ref)


def preflight(root: Path, evidence: dict, control):
    control_evidence = {uuid: Evidence() for uuid in control}
    targets = set(evidence) | set(control)
    roles_hist: Counter[str] = Counter()
    kinds_hist: Counter[str] = Counter()
    slot_control_uuids, slot_residual_uuids = defaultdict(set), defaultdict(set)
    slot_hits_control: Counter[str] = Counter()
    file_identity: dict[int, dict[str, str]] = {}
    file_kind: dict[int, str] = {}

    for file_id, json_path in enumerate(sorted(root.rglob("*.json"))):
        parts = json_path.relative_to(root).parts
        role, kind = file_role(parts)
        roles_hist[role] += 1
        if role == "object_metadata":
            kinds_hist[kind] += 1
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue

        depth = DEPTH_OBJECT if role == "object_metadata" else DEPTH_OTHER
        identity_map: dict[str, str] = {}
        file_kind[file_id] = kind

        def collector(slot_key, uuid, _map=identity_map):
            _map.setdefault(slot_key, uuid)

        def sink(uuid, pointer, is_ref, _role=role, _kind=kind, _fid=file_id, _depth=depth):
            slot = f"{_role}|{'Ref#' if is_ref else 'bare'}|{normalize_pointer(pointer, _depth)}"
            record = evidence.get(uuid)
            is_residual = record is not None
            if record is None:
                record = control_evidence.get(uuid)
            if record is None:
                return
            record.positions += 1
            record.roles[_role] += 1
            record.slots[slot] += 1
            record.slot_files[slot].add(_fid)
            record.slot_kinds[slot][_kind] += 1
            if _role == "object_metadata" and len(pointer) <= RAW_INDEX_DEPTH:
                record.raw_indices[raw_index_pointer(pointer)] += 1
            if is_residual:
                slot_residual_uuids[slot].add(uuid)
            else:
                slot_hits_control[slot] += 1
                slot_control_uuids[slot].add(uuid)

        walk(data, targets, [], sink, collector if role == "object_metadata" else None)
        if role == "object_metadata" and identity_map:
            file_identity[file_id] = identity_map

    stats = {
        "roles": roles_hist,
        "object_metadata_kinds": kinds_hist,
        "slot_control_uuids": slot_control_uuids,
        "slot_residual_uuids": slot_residual_uuids,
        "slot_hits_control": slot_hits_control,
        "file_identity": file_identity,
        "file_kind": file_kind,
    }
    return control_evidence, stats


def slot_metrics(stats, control_evidence, control_total: int) -> dict:
    metrics = {}
    for slot, uuids in stats["slot_control_uuids"].items():
        hits = stats["slot_hits_control"][slot]
        max_files = max(
            (len(rec.slot_files.get(slot, ())) for rec in control_evidence.values()), default=0
        )
        metrics[slot] = {
            "coverage": len(uuids) / control_total if control_total else 0.0,
            "uuids": len(uuids),
            "hits_per_uuid": hits / len(uuids) if uuids else 0.0,
            "max_files": max_files,
            "residual_uuids": len(stats["slot_residual_uuids"].get(slot, ())),
        }
    return metrics


def pick_slots(metrics, min_coverage, exclude=frozenset()):
    return sorted(
        (
            slot for slot, m in metrics.items()
            if slot.startswith("object_metadata|")
            and slot not in exclude
            and m["coverage"] >= min_coverage
            and m["hits_per_uuid"] <= MAX_HITS_PER_UUID
            and m["max_files"] <= 1
        ),
        key=lambda s: (-metrics[s]["coverage"], s),
    )


def classify(record: Evidence, identity, layout, has_identity: bool) -> str:
    if not has_identity:
        return "ambiguous"
    if any(slot in identity for slot in record.slots):
        return "definition_known"
    for slot in record.slots:
        if slot in layout and record.slots[slot] == 1 and len(record.slot_files.get(slot, ())) == 1:
            return "definition_unknown_layout"
    return "reference_only" if record.positions else "ambiguous"


RESIDUAL_CLASSES = (
    "definition_known",
    "definition_unknown_layout",
    "reference_only",
    "ambiguous",
)
DEFAULT_CONTROL_SIZE = 120


@dataclass(frozen=True)
class ReferenceResolutionSummary:
    """Обезличенная сводка резолюции ссылочных типов реквизитов форм.

    ``applicable = resolved + unresolved``; ``unresolved`` равен сумме
    ``by_class`` (число вхождений), ``unresolved_uuids`` — сумме
    ``by_class_uuids``.
    """

    applicable: int
    resolved: int
    unresolved: int
    unresolved_uuids: int
    by_class: dict[str, int]
    by_class_uuids: dict[str, int]

    def __post_init__(self) -> None:
        if self.applicable != self.resolved + self.unresolved:
            raise ValueError("applicable must equal resolved + unresolved")
        if set(self.by_class) != set(RESIDUAL_CLASSES) or set(self.by_class_uuids) != set(RESIDUAL_CLASSES):
            raise ValueError("by_class must list every residual class")
        if sum(self.by_class.values()) != self.unresolved:
            raise ValueError("sum of by_class must equal unresolved")
        if sum(self.by_class_uuids.values()) != self.unresolved_uuids:
            raise ValueError("sum of by_class_uuids must equal unresolved_uuids")

    def to_dict(self) -> dict[str, object]:
        return {
            "applicable": self.applicable,
            "resolved": self.resolved,
            "unresolved": self.unresolved,
            "unresolved_uuids": self.unresolved_uuids,
            "unresolved_by_class": dict(sorted(self.by_class.items())),
            "unresolved_uuids_by_class": dict(sorted(self.by_class_uuids.items())),
        }


class ReferenceResolutionCollector:
    """Накопитель вхождений по ``object_attributes`` построенных контекстов."""

    def __init__(self) -> None:
        self.applicable = 0
        self.resolved = 0
        self.evidence: dict[str, Evidence] = defaultdict(Evidence)

    def add_attributes(self, attributes: Any) -> None:
        if not attributes:
            return
        for _name, type_name, _section in iter_named_types(attributes):
            if not is_reference_type(type_name):
                continue
            self.applicable += 1
            if not type_name.startswith(REF_PREFIX):
                self.resolved += 1
                continue
            uuid = type_name[len(REF_PREFIX):]
            if not UUID_RE.match(uuid):
                self.resolved += 1
                continue
            self.evidence[uuid].occurrences += 1

    def summarize(
        self,
        root: Path,
        index_uuids: Iterable[str],
        control_size: int = DEFAULT_CONTROL_SIZE,
    ) -> ReferenceResolutionSummary:
        evidence = dict(self.evidence)
        known = sorted(u for u in set(index_uuids) if u not in evidence)
        step = max(1, len(known) // control_size) if control_size and known else 1
        control = set(known[::step][:control_size]) if control_size else set()
        by_class = dict.fromkeys(RESIDUAL_CLASSES, 0)
        by_uuids = dict.fromkeys(RESIDUAL_CLASSES, 0)
        if evidence:
            control_evidence, stats = preflight(Path(root), evidence, control)
            metrics = slot_metrics(stats, control_evidence, len(control_evidence))
            identity = set(pick_slots(metrics, IDENTITY_MIN_COVERAGE))
            layout = set(pick_slots(metrics, LAYOUT_MIN_COVERAGE, exclude=identity))
            for record in evidence.values():
                cls = classify(record, identity, layout, bool(identity))
                by_class[cls] += record.occurrences
                by_uuids[cls] += 1
        unresolved = sum(rec.occurrences for rec in evidence.values())
        return ReferenceResolutionSummary(
            applicable=self.applicable,
            resolved=self.resolved,
            unresolved=unresolved,
            unresolved_uuids=len(evidence),
            by_class=by_class,
            by_class_uuids=by_uuids,
        )
