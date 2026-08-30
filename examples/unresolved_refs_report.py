#!/usr/bin/env python3
"""Отчёт по неразрешённым ссылочным типам `Ref#uuid` (issue #143).

Инструмент отвечает на вопрос: почему часть ссылочных типов реквизитов остаётся
безопасным fallback `Ref#uuid`, и есть ли у этих UUID доказуемая позиция
определения в выгрузке.

Метод
-----
1. Строится множество остатка: все `Ref#uuid` в `FormContext.object_attributes`.
2. Берётся контрольная группа UUID, которые resolver уже разрешает — у них
   определение существует по построению. Без неё нулевой результат детектора
   ничего не значит: он может означать слепоту детектора, а не отсутствие
   определения.
3. Из контроля выводится позиция идентичности: пара «роль файла × указатель»,
   которая покрывает большую часть контроля, даёт около одного попадания на UUID
   и встречается для каждого UUID ровно в одном файле. Плотные (ссылочные)
   позиции отсекаются по числу попаданий на UUID.
4. Остаток классифицируется на `definition_known`, `definition_unknown_layout`,
   `reference_only`, `ambiguous`, после чего для `definition_known` проверяется,
   индексируется ли объект по другому слоту того же файла и входит ли его вид
   метаданных в `scan_forms.REFERENCE_TYPE_PREFIXES`.

Обезличенность
--------------
По умолчанию печатаются только агрегаты: ранги `U01..UNN`, нормализованные
указатели, виды метаданных и платформенно-стандартные имена. UUID, имена
объектов, имена форм, реквизиты и пути НЕ выводятся.

Флаг ``--local-names`` и файл ``--annex`` выводят конкретные пути и имена.
ВНИМАНИЕ: такой вывод содержит локальные имена конкретной конфигурации и не
предназначен для публикации — не коммитьте его в репозиторий.

Запуск::

    python examples/unresolved_refs_report.py /path/to/cf_export
    python examples/unresolved_refs_report.py /path/to/cf_export --runs 2 --top 10
    python examples/unresolved_refs_report.py /path/to/cf_export --local-names
    python examples/unresolved_refs_report.py /path/to/cf_export --annex ~/annex.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from v8unpack_agent.form_context import build_form_context
from v8unpack_agent.scan_forms import scan_forms

try:
    from v8unpack_agent.scan_forms import REFERENCE_TYPE_PREFIXES
except ImportError:  # pragma: no cover - совместимость со старыми версиями
    REFERENCE_TYPE_PREFIXES = {}

LOCAL_NAMES_BANNER = (
    "ВНИМАНИЕ: ниже локальные имена и пути конкретной конфигурации. "
    "Не публиковать и не коммитить."
)

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
REF_PREFIX = "Ref#"

#: Ключи, допустимые к публикации в нормализованном указателе.
KNOWN_TAGS = frozenset({
    "header", "props", "params", "form", "data", "raw", "child", "children",
    "items", "elements", "attributes", "columns", "identity", "meta",
    "Properties", "TabularSections", "Attributes", "Columns", "Elements",
    "Name", "Type", "Synonym", "Fields", "Rows", "Value", "Values",
})

#: Платформенно-стандартные имена — публиковать допустимо.
STANDARD_ATTRS = frozenset({
    "Объект", "Отчет", "Обработка", "Запись", "КлючЗаписи", "Ссылка", "Список",
    "Объекты", "ЭтотОбъект", "Хранилище", "Параметры", "НастройкиКД",
})
STANDARD_FORMS = frozenset({
    "ФормаЭлемента", "ФормаСписка", "ФормаЗаписи", "ФормаВыбора", "ФормаГруппы",
    "ФормаВыбораГруппы", "ОсновнаяФорма", "ФормаОтчета", "ФормаНастроек",
    "ФормаДокумента", "ФормаЖурнала", "ФормаНабораЗаписей", "ФормаОбработки",
})

DEPTH_OBJECT = 14
DEPTH_OTHER = 6
RAW_INDEX_DEPTH = 5

IDENTITY_MIN_COVERAGE = 0.5
LAYOUT_MIN_COVERAGE = 0.05
MAX_HITS_PER_UUID = 1.5
DENSE_HITS_PER_UUID = 5.0


# --- вспомогательное -------------------------------------------------------


def mask(value: str, allowed) -> str:
    if not value:
        return "<empty>"
    return value if value in allowed else "<custom>"


def is_reference_type(type_name: str) -> bool:
    return type_name.startswith(REF_PREFIX) or "Ref." in type_name


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


def entry_kind(entry, root: Path) -> str:
    raw = getattr(entry, "form_path", None) or getattr(entry, "elem_json_path", None)
    if not raw:
        return "Unknown"
    try:
        parts = Path(str(raw)).relative_to(root).parts
    except ValueError:
        parts = Path(str(raw)).parts
    return parts[0] if parts else "Unknown"


def entry_form_name(entry, root: Path) -> str:
    for attr in ("form_name", "name"):
        value = getattr(entry, attr, None)
        if value:
            return str(value)
    raw = getattr(entry, "form_path", None) or getattr(entry, "elem_json_path", None)
    if not raw:
        return ""
    parts = [p for p in Path(str(raw)).parts if p not in {"Forms", "Ext"}]
    return Path(parts[-1]).stem if parts else ""


def entry_relative_path(entry, root: Path) -> str:
    raw = getattr(entry, "form_path", None) or getattr(entry, "elem_json_path", None)
    if not raw:
        return ""
    try:
        return str(Path(str(raw)).relative_to(root))
    except ValueError:
        return str(raw)


# --- модель ----------------------------------------------------------------


@dataclass
class Occurrence:
    uuid: str
    form_path: str
    metadata_kind: str
    form_name: str
    attribute: str
    section: str


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


# --- этап 1: остаток и контроль -------------------------------------------


def _warning_code_aggregate(index) -> tuple[int, str]:
    """Агрегат scan_warnings по машинному коду (#167): (без кода, "CODE=N, ...")."""
    from collections import Counter

    from v8unpack_agent.scan_forms import scan_warning_code

    warnings = list(getattr(index, "scan_warnings", []) or [])
    counts = Counter(scan_warning_code(w) for w in warnings)
    without_code = counts.pop(None, 0)
    by_code = ", ".join(f"{code}={n}" for code, n in sorted(counts.items()))
    return without_code, by_code or "-"


def collect_residual(root: Path, control_size: int):
    index = scan_forms(root)
    evidence: dict[str, Evidence] = defaultdict(Evidence)
    occurrences: list[Occurrence] = []
    applicable = resolved = with_attrs = forms = 0

    for entry in index.forms:
        forms += 1
        context = build_form_context(entry, root, type_resolver=index.resolve_reference_type)
        attributes = getattr(context, "object_attributes", None)
        if not attributes:
            continue
        with_attrs += 1
        kind = entry_kind(entry, root)
        form_name = entry_form_name(entry, root)
        form_path = entry_relative_path(entry, root)

        for attr_name, type_name, section in iter_named_types(attributes):
            if not is_reference_type(type_name):
                continue
            applicable += 1
            if not type_name.startswith(REF_PREFIX):
                resolved += 1
                continue
            uuid = type_name[len(REF_PREFIX):]
            if not UUID_RE.match(uuid):
                resolved += 1
                continue
            record = evidence[uuid]
            record.occurrences += 1
            record.form_kinds[kind] += 1
            record.attributes[mask(attr_name, STANDARD_ATTRS)] += 1
            record.form_names[mask(form_name, STANDARD_FORMS)] += 1
            record.sections[section] += 1
            occurrences.append(Occurrence(uuid, form_path, kind, form_name, attr_name, section))

    residual = {uuid: rec.occurrences for uuid, rec in evidence.items()}
    index_uuids = set(index.reference_types)
    known = sorted(u for u in index_uuids if u not in residual)
    step = max(1, len(known) // control_size) if control_size and known else 1
    control = set(known[::step][:control_size]) if control_size else set()

    _WARN_CACHE = _warning_code_aggregate(index)
    baseline = {
        "forms": forms,
        "forms_with_object_attributes": with_attrs,
        "applicable_reference_occurrences": applicable,
        "resolved": resolved,
        "unresolved": sum(residual.values()),
        "unique_uuids": len(residual),
        "index_size": len(index_uuids),
        "in_index_but_unresolved": sum(1 for u in residual if u in index_uuids),
        "control_group_size": len(control),
        "scan_warnings_total": len(getattr(index, "scan_warnings", []) or []),
        "scan_warnings_without_code": _WARN_CACHE[0],
        "scan_warnings_by_code": _WARN_CACHE[1],
    }
    return dict(evidence), occurrences, baseline, control, index_uuids


# --- этап 2: обход конфигурации -------------------------------------------


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
    roles_hist, kinds_hist = Counter(), Counter()
    slot_control_uuids, slot_residual_uuids = defaultdict(set), defaultdict(set)
    slot_hits_control = Counter()
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


# --- анализ слотов ---------------------------------------------------------


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


# --- отчёты ----------------------------------------------------------------


def anonymized_report(evidence, control_evidence, stats, baseline, top: int) -> str:
    ranks = sorted(evidence.items(), key=lambda kv: (-kv[1].occurrences, kv[0]))
    labels = {uuid: f"U{i:02d}" for i, (uuid, _) in enumerate(ranks, start=1)}
    total = sum(rec.occurrences for _, rec in ranks) or 1

    lines = ["# отчёт по неразрешённым Ref#uuid (#143)", ""]
    for key, value in baseline.items():
        lines.append(f"{key:34}: {value}")
    lines.append("REFERENCE_TYPE_PREFIXES: " + ", ".join(sorted(REFERENCE_TYPE_PREFIXES)))

    lines += ["", "## файлов по роли"]
    for role, count in stats["roles"].most_common():
        lines.append(f"{role} | {count}")
    lines.append("файлы объекта по виду: " + ", ".join(
        f"{k}={v}" for k, v in stats["object_metadata_kinds"].most_common(20)
    ))

    metrics = slot_metrics(stats, control_evidence, len(control_evidence))
    identity = set(pick_slots(metrics, IDENTITY_MIN_COVERAGE))
    layout = set(pick_slots(metrics, LAYOUT_MIN_COVERAGE, exclude=identity))
    has_identity = bool(identity)

    lines += ["", "## слот идентичности (выведен из контроля)"]
    for slot in sorted(identity):
        m = metrics[slot]
        lines.append(
            f"{slot} | покрытие {m['coverage'] * 100:.1f}% | попаданий/uuid {m['hits_per_uuid']:.2f} | "
            f"uuid остатка {m['residual_uuids']}"
        )
    if not identity:
        lines.append("не выведен — результаты классификации недостоверны")

    lines += ["", "## слоты иного layout"]
    lines.extend(
        f"{slot} | покрытие {metrics[slot]['coverage'] * 100:.1f}% | "
        f"uuid остатка {metrics[slot]['residual_uuids']}"
        for slot in sorted(layout)
    ) or lines.append("нет")

    classes, class_occ, class_pos = Counter(), Counter(), Counter()
    facet_rows, boundary_rows, anomaly_rows = [], [], []
    for uuid, record in ranks:
        cls = classify(record, identity, layout, has_identity)
        classes[cls] += 1
        class_occ[cls] += record.occurrences
        class_pos[cls] += record.positions
        if cls != "definition_known":
            continue
        file_ids = set()
        for slot in record.slots:
            if slot in identity:
                file_ids |= record.slot_files.get(slot, set())
        indexed, all_slots, own_slot, kinds = [], 0, "?", Counter()
        for fid in file_ids:
            identity_map = stats["file_identity"].get(fid, {})
            kinds[stats["file_kind"].get(fid, "<Other>")] += 1
            all_slots += len(identity_map)
            for slot_key, slot_uuid in identity_map.items():
                if slot_uuid == uuid:
                    own_slot = slot_key
                elif slot_uuid in getattr(anonymized_report, "index_uuids", set()):
                    indexed.append(slot_key)
        in_table = any(k in REFERENCE_TYPE_PREFIXES for k in kinds)
        row = (
            f"{labels[uuid]} | вхождений {record.occurrences} | вид {dict(kinds)} | "
            f"свой слот {own_slot} | слотов в файле {all_slots} | в индексе {len(indexed)}"
        )
        if indexed:
            facet_rows.append(row)
        elif not in_table:
            boundary_rows.append(row)
        else:
            anomaly_rows.append(row)

    lines += ["", "## классы остатка", "класс | uuid | вхождений | доля | позиций"]
    for cls in ("definition_known", "definition_unknown_layout", "reference_only", "ambiguous"):
        lines.append(
            f"{cls} | {classes[cls]} | {class_occ[cls]} | "
            f"{class_occ[cls] / total * 100:.2f}% | {class_pos[cls]}"
        )
    lines.append(f"ИТОГО | {sum(classes.values())} | {sum(class_occ.values())} | 100% | "
                 f"{sum(class_pos.values())}")

    lines += ["", "## definition_known: разбор"]
    lines.append(f"иная типовая грань проиндексированного объекта | {len(facet_rows)}")
    lines.extend(f"  {row}" for row in facet_rows[:10])
    lines.append(f"вид метаданных вне REFERENCE_TYPE_PREFIXES     | {len(boundary_rows)}")
    lines.extend(f"  {row}" for row in boundary_rows[:10])
    lines.append(f"аномалия индекса (требует RCA)                 | {len(anomaly_rows)}")
    lines.extend(f"  {row}" for row in anomaly_rows[:10])

    attrs, form_names, sections = Counter(), Counter(), Counter()
    for _, record in ranks:
        attrs.update(record.attributes)
        form_names.update(record.form_names)
        sections.update(record.sections)

    lines += ["", "## вхождения по имени реквизита (стандартные имена)"]
    lines.extend(f"{name} | {n} | {n / total * 100:.2f}%" for name, n in attrs.most_common())
    lines += ["", "## по виду формы"]
    lines.extend(f"{name} | {n} | {n / total * 100:.2f}%" for name, n in form_names.most_common())
    lines += ["", "## по секции object_attributes"]
    lines.extend(f"{name} | {n} | {n / total * 100:.2f}%" for name, n in sections.most_common())

    lines += ["", f"## top-{top}"]
    for uuid, record in ranks[:top]:
        lines.append(
            f"{labels[uuid]} | вхождений {record.occurrences} "
            f"({record.occurrences / total * 100:.2f}%) | позиций {record.positions} | "
            f"класс {classify(record, identity, layout, has_identity)} | "
            f"виды форм {dict(record.form_kinds)} | реквизиты {dict(record.attributes)}"
        )
    return "\n".join(lines)


def local_names_report(evidence, occurrences, top: int) -> str:
    ranks = sorted(evidence.items(), key=lambda kv: (-kv[1].occurrences, kv[0]))
    labels = {uuid: f"U{i:02d}" for i, (uuid, _) in enumerate(ranks, start=1)}
    by_uuid = defaultdict(list)
    for occ in occurrences:
        by_uuid[occ.uuid].append(occ)

    lines = ["", LOCAL_NAMES_BANNER, "", f"## конкретные формы и реквизиты (top-{top})"]
    for uuid, record in ranks[:top]:
        lines.append(f"\n{labels[uuid]} — вхождений {record.occurrences}")
        for occ in sorted(by_uuid[uuid], key=lambda o: (o.form_path, o.attribute))[:20]:
            lines.append(f"  {occ.form_path} | реквизит {occ.attribute} | секция {occ.section}")
        if len(by_uuid[uuid]) > 20:
            lines.append(f"  ... ещё {len(by_uuid[uuid]) - 20} вхождений, полный список в --annex")
    return "\n".join(lines)


def write_annex(path: Path, evidence, occurrences) -> int:
    ranks = sorted(evidence.items(), key=lambda kv: (-kv[1].occurrences, kv[0]))
    labels = {uuid: f"U{i:02d}" for i, (uuid, _) in enumerate(ranks, start=1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([LOCAL_NAMES_BANNER])
        writer.writerow(["rank", "form_path", "metadata_kind", "form_name", "attribute", "section"])
        for occ in sorted(occurrences, key=lambda o: (labels[o.uuid], o.form_path, o.attribute)):
            writer.writerow([
                labels[occ.uuid], occ.form_path, occ.metadata_kind,
                occ.form_name, occ.attribute, occ.section,
            ])
    return len(occurrences)


# --- issue #164: compare-режим reference_only ---

def _load_compare_module():
    """Загрузить examples/reference_only_compare.py как модуль.

    Регистрация в sys.modules обязательна до exec_module: при
    `from __future__ import annotations` dataclasses резолвит строковые
    аннотации через sys.modules[cls.__module__].
    """
    import importlib.util
    import sys as _sys

    path = Path(__file__).resolve().with_name("reference_only_compare.py")
    if not path.is_file():
        raise SystemExit("не найден examples/reference_only_compare.py")
    spec = importlib.util.spec_from_file_location("reference_only_compare", path)
    module = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        _sys.modules.pop(spec.name, None)
        raise
    return module


def _analyze_export(root, label, control_size, runs, cmp_mod):
    """Агрегат одной выгрузки: остаток, классы, контроль, детерминированность."""
    digests, stats = [], None
    for _ in range(max(1, runs)):
        evidence, _occurrences, baseline, control, index_uuids = collect_residual(
            root, control_size
        )
        control_evidence, walk_stats = preflight(root, evidence, control)
        metrics = slot_metrics(walk_stats, control_evidence, len(control_evidence))
        identity = set(pick_slots(metrics, IDENTITY_MIN_COVERAGE))
        layout = set(pick_slots(metrics, LAYOUT_MIN_COVERAGE, exclude=identity))
        stats = cmp_mod.from_residual(
            label, baseline, evidence, control_evidence, identity, layout,
            classify, index_uuids, True,
        )
        digests.append(cmp_mod.aggregate_digest(stats))
    stats.deterministic = len(set(digests)) == 1
    return stats


def _run_compare(args):
    """Сравнить класс reference_only двух выгрузок. Публикуются только ранги."""
    import sys as _sys

    cmp_mod = _load_compare_module()
    for root in (args.root, args.compare_root):
        if not Path(root).is_dir():
            print("каталог выгрузки не найден", file=_sys.stderr)
            return cmp_mod.EXIT_INPUT

    stats_a = _analyze_export(args.root, "A", args.control, args.runs, cmp_mod)
    stats_b = _analyze_export(args.compare_root, "B", args.control, args.runs, cmp_mod)
    result = cmp_mod.compare_reference_only(stats_a, stats_b, args.control_threshold)
    text = cmp_mod.compare_report(result)
    cmp_mod.anonymity_guard(text)
    print(text)
    return result["exit_code"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="каталог распакованной выгрузки cf_export")
    parser.add_argument("--runs", type=int, default=1, help="число прогонов для проверки детерминированности")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--control", type=int, default=120, help="размер контрольной группы")
    parser.add_argument("--local-names", action="store_true",
                        help="печатать конкретные пути и имена реквизитов (НЕ публиковать вывод)")
    parser.add_argument("--annex", type=Path, default=None,
                        help="CSV со всеми вхождениями: пути, формы, реквизиты (НЕ коммитить файл)")
    parser.add_argument("--compare-root", type=Path, default=None,
                        help="корень второй независимой выгрузки: сравнение "
                             "классов reference_only (#164)")
    parser.add_argument("--control-threshold", type=float, default=90.0,
                        help="минимальное покрытие позитивного контроля, %% (порог 90)")
    args = parser.parse_args()

    if args.compare_root is not None:
        raise SystemExit(_run_compare(args))

    digests, text = [], ""
    evidence = occurrences = None
    for _ in range(max(1, args.runs)):
        evidence, occurrences, baseline, control, index_uuids = collect_residual(
            args.root, args.control
        )
        control_evidence, stats = preflight(args.root, evidence, control)
        anonymized_report.index_uuids = index_uuids
        text = anonymized_report(evidence, control_evidence, stats, baseline, args.top)
        digests.append(sha256(text.encode("utf-8")).hexdigest())

    print(text)
    print()
    print(f"детерминированность: {'OK' if len(set(digests)) == 1 else 'РАСХОЖДЕНИЕ'} ({args.runs} прогонов)")

    if args.local_names and evidence and occurrences:
        print(local_names_report(evidence, occurrences, args.top))
    if args.annex and evidence and occurrences:
        written = write_annex(args.annex.expanduser(), evidence, occurrences)
        print(f"\n{LOCAL_NAMES_BANNER}")
        print(f"локальное приложение: {written} строк записано в файл (не коммитить)")


if __name__ == "__main__":
    main()
