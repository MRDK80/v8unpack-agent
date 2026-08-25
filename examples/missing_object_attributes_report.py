#!/usr/bin/env python3
"""Классификация форм без ``FormContext.object_attributes`` (issue #163).

Research-инструмент. Production-код не меняет: только публичные
``scan_forms``, ``build_form_context``, ``object_json_path``,
``decode_object_attributes``, ``classify_form``.

Контракты типов (main, 60c5c1c):

* ``DecodeError`` — ``enum.Enum``, у члена есть ``.value``;
* ``FormClass`` — подкласс ``str``, ``.value`` у него НЕТ (приводить ``str()``).

Требует РЕАЛЬНУЮ выгрузку ``cf_export`` (обязательный позиционный аргумент).
По умолчанию печатает только обезличенные агрегаты: имена форм, имена
объектов, UUID и абсолютные пути не выводятся.

Запуск:

    python examples/missing_object_attributes_report.py /path/to/cf_export --runs 2
    python examples/missing_object_attributes_report.py /path/to/cf_export --controls

Локальные режимы (в PR/issue не вставлять):

    --local-names   вывести реальные имена форм
    --csv PATH      выгрузить построчную таблицу (*.csv под .gitignore)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from v8unpack_agent.catalog_resolver import clear_object_cache, object_json_path
from v8unpack_agent.form_classifier import FormClass, classify_form
from v8unpack_agent.form_context import build_form_context
from v8unpack_agent.object_decoder import decode_object_attributes
from v8unpack_agent.scan_forms import scan_forms

CLASSES = (
    "no_owner_object",
    "type_out_of_scope",
    "layout_unsupported",
    "path_convention_miss",
    "broken_json",
    "insufficient_evidence",
)

UNKNOWN_CLASS = str(getattr(FormClass, "UNKNOWN", "unknown"))

# Возможные имена поля с FormSummary внутри FormContext. Пустой список
# элементов и отсутствующее поле дают одинаковый FormClass=unknown, поэтому
# источник элементов фиксируется явно и попадает в агрегат.
SUMMARY_FIELD_CANDIDATES = ("form_summary", "summary", "form_summary_obj")
ELEMENTS_FIELD_CANDIDATES = ("elements",)


@dataclass
class Row:
    """Обезличенная запись по одной форме без object_attributes."""

    form_id: str
    object_type: str
    object_name_level: str
    rel_depth: int
    failure_point: str
    decode_error: str | None
    levels_up: int | None
    candidate_role: str
    candidate_key: str | None
    form_class: str
    elements_source: str
    elements_count: int
    reason_class: str
    local_form_name: str | None = None


@dataclass
class Aggregate:
    total_forms: int = 0
    with_attrs: int = 0
    without_attrs: int = 0
    failure_points: Counter = field(default_factory=Counter)
    decode_errors: Counter = field(default_factory=Counter)
    object_types: Counter = field(default_factory=Counter)
    layouts: Counter = field(default_factory=Counter)
    candidate_roles: Counter = field(default_factory=Counter)
    candidate_keys: Counter = field(default_factory=Counter)
    form_classes: Counter = field(default_factory=Counter)
    elements_sources: Counter = field(default_factory=Counter)
    elements_buckets: Counter = field(default_factory=Counter)
    reason_classes: Counter = field(default_factory=Counter)
    distinct_candidates: int = 0
    context_errors: int = 0
    classifier_errors: int = 0

    def signature(self) -> str:
        payload = json.dumps(
            {
                "total": self.total_forms,
                "with": self.with_attrs,
                "without": self.without_attrs,
                "points": sorted(self.failure_points.items()),
                "errors": sorted(self.decode_errors.items()),
                "types": sorted(self.object_types.items()),
                "layouts": sorted(self.layouts.items()),
                "roles": sorted(self.candidate_roles.items()),
                "keys": sorted(self.candidate_keys.items()),
                "classes": sorted(self.form_classes.items()),
                "elem_sources": sorted(self.elements_sources.items()),
                "elem_buckets": sorted(self.elements_buckets.items()),
                "reasons": sorted(self.reason_classes.items()),
                "distinct_candidates": self.distinct_candidates,
                "context_errors": self.context_errors,
                "classifier_errors": self.classifier_errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# структурные помощники
# ---------------------------------------------------------------------------

def _rel_depth(root: Path, form_path: Path) -> int:
    try:
        return len(form_path.resolve().relative_to(root.resolve()).parts)
    except ValueError:
        return -1


def _levels_up(form_path: Path, candidate: Path) -> int | None:
    target = candidate.resolve().parent
    current = form_path.resolve()
    for depth in range(0, 6):
        if current == target:
            return depth
        if current.parent == current:
            return None
        current = current.parent
    return None


def _candidate_role(
    root: Path,
    form_path: Path,
    candidate: Path,
    object_type: str,
    object_name: str,
) -> str:
    """Структурная роль найденного JSON — без публикации пути."""
    parent = candidate.resolve().parent
    stem = candidate.stem
    if parent == root.resolve():
        return "export_root_neighbour"
    if object_name and parent.name == object_name and stem in {object_name, object_type}:
        return "owner_object_file"
    if parent == form_path.resolve():
        return "form_artifact"
    if parent.name == object_type:
        return "type_container_neighbour"
    return "unrelated_neighbour"


def _candidate_key(root: Path, candidate: Path) -> str:
    try:
        parts = candidate.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return "<outside-root>"
    return "/".join(["<root>", *(f"<L{i}>" for i in range(1, len(parts))), "<candidate>.json"])


def _as_element_dicts(elements: object) -> list[dict]:
    result: list[dict] = []
    for element in elements or []:  # type: ignore[union-attr]
        if isinstance(element, dict):
            result.append(element)
        else:
            result.append(
                {
                    "type": getattr(element, "type", "") or "",
                    "data_path": getattr(element, "data_path", None),
                    "name": getattr(element, "name", "") or "",
                }
            )
    return result


def _find_elements(context: object) -> tuple[list[dict], str]:
    """Найти элементы формы в FormContext и назвать источник.

    ``FormClass=unknown`` может означать и «нет data-элементов», и «элементы
    не найдены в контексте». Источник фиксируется, чтобы второе не выглядело
    как первое.
    """
    if context is None:
        return [], "no_context"
    for name in SUMMARY_FIELD_CANDIDATES:
        summary = getattr(context, name, None)
        if summary is None:
            continue
        for elem_name in ELEMENTS_FIELD_CANDIDATES:
            raw = getattr(summary, elem_name, None)
            if raw is not None:
                return _as_element_dicts(raw), f"{name}.{elem_name}"
    raw = getattr(context, "elements", None)
    if raw is not None:
        return _as_element_dicts(raw), "context.elements"
    fields = ",".join(sorted(k for k in vars(context)) ) if hasattr(context, "__dict__") else "?"
    return [], f"not_found[{fields[:60]}]"


def _form_class(context: object, form_name: str, agg: Aggregate) -> tuple[str, str, int]:
    elements, source = _find_elements(context)
    try:
        return str(classify_form(form_name, elements)), source, len(elements)
    except Exception:  # noqa: BLE001 — best-effort
        agg.classifier_errors += 1
        return UNKNOWN_CLASS, source, len(elements)


def _reason_class(
    role: str,
    point: str,
    error: str | None,
    object_name_level: str,
) -> str:
    """Класс причины — только по структурным признакам.

    Признак отсутствия владельца — отсутствие уровня ``ObjectName``
    (``object_name == ""``), а не абсолютная глубина пути: глубина зависит от
    того, где расположен корень выгрузки.
    """
    if point == "object_json_not_found":
        # issue #172: layout без уровня ObjectName — объекта-владельца нет по
        # конструкции, точка отказа object_json_not_found больше не означает
        # тип вне охвата. Класс type_out_of_scope сохраняется для #151.
        if object_name_level == "absent":
            return "no_owner_object"
        return "type_out_of_scope"
    if point == "unexpected_context_none":
        return "insufficient_evidence"
    if role == "owner_object_file":
        if error == "json_parse_error":
            return "broken_json"
        if error in {"header_missing", "version_unsupported"}:
            return "layout_unsupported"
        return "insufficient_evidence"
    if object_name_level == "absent" and role in {"export_root_neighbour", "form_artifact"}:
        return "no_owner_object"
    if role in {"type_container_neighbour", "unrelated_neighbour"}:
        return "path_convention_miss"
    return "insufficient_evidence"


# ---------------------------------------------------------------------------
# основной анализ
# ---------------------------------------------------------------------------

def analyse(root: Path, *, keep_names: bool) -> tuple[Aggregate, list[Row]]:
    clear_object_cache()
    index = scan_forms(root)
    agg = Aggregate()
    rows: list[Row] = []
    candidates: set[str] = set()

    for ordinal, entry in enumerate(index.forms):
        agg.total_forms += 1
        try:
            context = build_form_context(entry, root)
        except Exception:  # noqa: BLE001
            agg.context_errors += 1
            context = None
        if context is not None and getattr(context, "object_attributes", None) is not None:
            agg.with_attrs += 1
            continue
        agg.without_attrs += 1

        form_path = Path(entry.form_path)
        object_type = getattr(entry, "object_type", "") or ""
        object_name = getattr(entry, "object_name", "") or ""
        object_name_level = "present" if object_name else "absent"

        path = object_json_path(entry)
        if path is None:
            point, error, role, levels_up, key = (
                "object_json_not_found", None, "absent", None, None,
            )
        else:
            result = decode_object_attributes(path)
            if getattr(result, "ok", False):
                point, error = "unexpected_context_none", None
            else:
                error = getattr(getattr(result, "error", None), "value", "unknown")
                point = f"decode_error:{error}"
            role = _candidate_role(root, form_path, path, object_type, object_name)
            levels_up = _levels_up(form_path, path)
            key = _candidate_key(root, path)
            candidates.add(str(path.resolve()))

        form_class, elements_source, elements_count = _form_class(
            context, getattr(entry, "form_name", "") or "", agg
        )
        rows.append(
            Row(
                form_id=f"F{ordinal:05d}",
                object_type=object_type,
                object_name_level=object_name_level,
                rel_depth=_rel_depth(root, form_path),
                failure_point=point,
                decode_error=error,
                levels_up=levels_up,
                candidate_role=role,
                candidate_key=key,
                form_class=form_class,
                elements_source=elements_source,
                elements_count=elements_count,
                reason_class=_reason_class(role, point, error, object_name_level),
                local_form_name=(getattr(entry, "form_name", None) if keep_names else None),
            )
        )

    for row in rows:
        agg.failure_points[row.failure_point] += 1
        if row.decode_error:
            agg.decode_errors[row.decode_error] += 1
        agg.object_types[row.object_type] += 1
        agg.layouts[f"rel_depth={row.rel_depth}/object_name:{row.object_name_level}"] += 1
        agg.candidate_roles[row.candidate_role] += 1
        if row.candidate_key:
            agg.candidate_keys[row.candidate_key] += 1
        agg.form_classes[row.form_class] += 1
        agg.elements_sources[row.elements_source] += 1
        bucket = "0" if row.elements_count == 0 else ("1-9" if row.elements_count < 10 else "10+")
        agg.elements_buckets[bucket] += 1
        agg.reason_classes[row.reason_class] += 1
    agg.distinct_candidates = len(candidates)
    return agg, rows


# ---------------------------------------------------------------------------
# контроли A / B / C (DoD issue #163)
# ---------------------------------------------------------------------------

def _clone_entry(entry: object, form_path: Path) -> object:
    clone = copy.copy(entry)
    try:
        object.__setattr__(clone, "form_path", str(form_path))
    except Exception:  # noqa: BLE001
        return entry
    return clone


def controls(root: Path) -> int:
    """Позитивный и негативный контроль на реальной выгрузке.

    A — форма с объектом-владельцем: декодер обязан отдать ok=True.
    B — CommonForm: уровня ObjectName нет, кандидат не является владельцем.
    C — настоящий owner JSON без ``header``: обязан дать HEADER_MISSING и класс
        ``layout_unsupported`` — иначе классификатор не различает #160 и #163.
    """
    clear_object_cache()
    index = scan_forms(root)
    owner_entry = None
    common_entry = None
    for entry in index.forms:
        name = getattr(entry, "object_name", "") or ""
        otype = getattr(entry, "object_type", "") or ""
        if owner_entry is None and name and otype in {"Catalog", "Document"}:
            context = build_form_context(entry, root)
            if getattr(context, "object_attributes", None) is not None:
                owner_entry = entry
        if common_entry is None and otype == "CommonForm":
            common_entry = entry
        if owner_entry is not None and common_entry is not None:
            break

    failures = 0
    print("=" * 72)
    print("контроли issue #163")
    print("=" * 72)

    if owner_entry is None:
        print("A: НЕ ВЫПОЛНЕН — не найдена форма с объектом-владельцем")
        failures += 1
        owner_path = None
    else:
        owner_path = object_json_path(owner_entry)
        result = decode_object_attributes(owner_path) if owner_path else None
        role = (
            _candidate_role(
                root,
                Path(owner_entry.form_path),
                owner_path,
                owner_entry.object_type,
                owner_entry.object_name,
            )
            if owner_path
            else "absent"
        )
        ok = bool(owner_path) and bool(getattr(result, "ok", False)) and role == "owner_object_file"
        print(f"A: {'OK' if ok else 'ПРОВАЛ'} — 4-level, role={role}, decode.ok={getattr(result, 'ok', None)}")
        failures += 0 if ok else 1

    if common_entry is None:
        print("B: НЕ ВЫПОЛНЕН — CommonForm не найдены")
        failures += 1
    else:
        path = object_json_path(common_entry)
        role = (
            _candidate_role(root, Path(common_entry.form_path), path, "CommonForm", "")
            if path
            else "absent"
        )
        result = decode_object_attributes(path) if path else None
        name_level = "present" if (getattr(common_entry, "object_name", "") or "") else "absent"
        reason = _reason_class(
            role,
            "object_json_not_found" if path is None else f"decode_error:{getattr(getattr(result, 'error', None), 'value', 'unknown')}",
            getattr(getattr(result, "error", None), "value", None),
            name_level,
        )
        ok = name_level == "absent" and role != "owner_object_file" and reason == "no_owner_object"
        print(f"B: {'OK' if ok else 'ПРОВАЛ'} — object_name={name_level}, role={role}, class={reason}")
        failures += 0 if ok else 1

    if owner_entry is None or owner_path is None:
        print("C: НЕ ВЫПОЛНЕН — нет исходного owner JSON")
        failures += 1
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            rel = Path(owner_entry.form_path).resolve().relative_to(root.resolve())
            fake_form = tmp_root / rel
            fake_form.mkdir(parents=True, exist_ok=True)
            payload = json.loads(owner_path.read_text(encoding="utf-8"))
            payload.pop("header", None)
            fake_owner = tmp_root / owner_path.resolve().relative_to(root.resolve())
            fake_owner.parent.mkdir(parents=True, exist_ok=True)
            fake_owner.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            clear_object_cache()
            fake_entry = _clone_entry(owner_entry, fake_form)
            path = object_json_path(fake_entry)
            result = decode_object_attributes(path) if path else None
            error = getattr(getattr(result, "error", None), "value", None)
            role = (
                _candidate_role(
                    tmp_root, fake_form, path, owner_entry.object_type, owner_entry.object_name
                )
                if path
                else "absent"
            )
            reason = _reason_class(role, f"decode_error:{error}", error, "present")
            ok = error == "header_missing" and role == "owner_object_file" and reason == "layout_unsupported"
            print(f"C: {'OK' if ok else 'ПРОВАЛ'} — error={error}, role={role}, class={reason}")
            failures += 0 if ok else 1

    clear_object_cache()
    print(f"\nпровалов: {failures}")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# вывод
# ---------------------------------------------------------------------------

def _print_counter(title: str, counter: Counter, total: int) -> None:
    print(f"\n{title}")
    if not counter:
        print("  (пусто)")
        return
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        share = f"{count / total * 100:.1f}%" if total else "-"
        print(f"  {key:<48} {count:>5}  {share:>7}")


def report(agg: Aggregate) -> None:
    print("=" * 72)
    print("issue #163 — формы без object_attributes")
    print("=" * 72)
    print(f"форм всего            : {agg.total_forms}")
    print(f"с object_attributes   : {agg.with_attrs}")
    print(f"без object_attributes : {agg.without_attrs}")
    total = agg.without_attrs
    _print_counter("точка отказа", agg.failure_points, total)
    _print_counter("DecodeError", agg.decode_errors, total)
    _print_counter("object_type", agg.object_types, total)
    _print_counter("класс layout", agg.layouts, total)
    _print_counter("роль найденного JSON", agg.candidate_roles, total)
    _print_counter("нормализованный путь кандидата", agg.candidate_keys, total)
    _print_counter("FormClass", agg.form_classes, total)
    _print_counter("источник элементов для classify_form", agg.elements_sources, total)
    _print_counter("число элементов формы", agg.elements_buckets, total)
    _print_counter("класс причины", agg.reason_classes, total)
    print(f"\nразных путей-кандидатов на {total} форм: {agg.distinct_candidates}")
    print(f"ошибок build_form_context : {agg.context_errors}")
    print(f"ошибок classify_form      : {agg.classifier_errors}")
    print(f"подпись агрегата          : {agg.signature()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_root", type=Path, help="путь к реальной выгрузке cf_export")
    parser.add_argument("--runs", type=int, default=1, help="прогонов для проверки детерминированности")
    parser.add_argument("--controls", action="store_true", help="выполнить контроли A/B/C")
    parser.add_argument("--local-names", action="store_true", help="локально: показать имена форм")
    parser.add_argument("--csv", type=Path, default=None, help="локально: выгрузить построчный CSV")
    args = parser.parse_args(argv)

    root = args.export_root.expanduser()
    if not root.is_dir():
        print(f"не каталог: {root}", file=sys.stderr)
        return 2

    if args.controls:
        return controls(root)

    signatures: list[str] = []
    last_rows: list[Row] = []
    for run in range(1, max(1, args.runs) + 1):
        agg, rows = analyse(root, keep_names=args.local_names)
        signatures.append(agg.signature())
        last_rows = rows
        if run == 1:
            report(agg)

    if len(set(signatures)) == 1:
        print(f"\nдетерминированность  : OK ({args.runs} прогон(ов), подпись {signatures[0]})")
    else:
        print(f"\nдетерминированность  : РАСХОЖДЕНИЕ {signatures}", file=sys.stderr)
        return 1

    unclassified = [r for r in last_rows if r.reason_class not in CLASSES]
    if unclassified:
        print(f"формы вне классов    : {len(unclassified)}", file=sys.stderr)
        return 1

    if args.local_names:
        print("\n[локально] имена форм:")
        for row in last_rows:
            print(f"  {row.form_id} {row.reason_class:<22} {row.local_form_name}")

    if args.csv:
        import csv

        with args.csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "form_id", "object_type", "object_name_level", "rel_depth",
                    "failure_point", "decode_error", "levels_up", "candidate_role",
                    "candidate_key", "form_class", "elements_source", "elements_count",
                    "reason_class",
                ]
            )
            for row in last_rows:
                writer.writerow(
                    [
                        row.form_id, row.object_type, row.object_name_level, row.rel_depth,
                        row.failure_point, row.decode_error or "",
                        "" if row.levels_up is None else row.levels_up,
                        row.candidate_role, row.candidate_key or "", row.form_class,
                        row.elements_source, row.elements_count, row.reason_class,
                    ]
                )
        print(f"[локально] CSV записан: {args.csv} (держать под .gitignore)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
