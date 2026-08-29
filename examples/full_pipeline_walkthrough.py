#!/usr/bin/env python
"""Сквозной пример публичного API v8unpack-agent на реальной выгрузке (#175).

Скрипт демонстрирует **композицию** существующего публичного API и не
является production-оркестратором: он не добавляет новый API, не меняет
``v8unpack_agent/``, не дублирует parsing, не хранит состояние в выгрузке и
не подменяет отсутствующие данные пустыми.

Пайплайн форм (шаги 1–10) и общие модули (шаг 11) — две независимые ветки
метаданных, они не смешиваются в одном типе данных.

Вывод обезличен: печатаются только количества, флаги, статусы, коды
предупреждений, generic ``object_type`` и длины строк. Имена объектов и форм,
контейнеры, пути, UUID, тексты BSL, запросы СКД и полный LLM-промпт в stdout
не попадают.

Запуск::

    python examples/full_pipeline_walkthrough.py /path/to/cf_export

Скрипт ничего не записывает в выгрузку: временный индекс создаётся в
системном временном каталоге и удаляется автоматически.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from v8unpack_agent.catalog_resolver import object_json_path
from v8unpack_agent.common_modules import (
    build_common_module_context,
    scan_common_modules,
)
from v8unpack_agent.coverage_metric import calc_coverage_from_elem_index
from v8unpack_agent.drift_checker import check_drift
from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.form_classifier import classify_form
from v8unpack_agent.form_context import (
    NO_OBJECT_PLACEHOLDER,
    OBJECT_ATTRIBUTES_MARKER,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.managed_forms import discover_elem_forms
from v8unpack_agent.object_decoder import decode_object_attributes
from v8unpack_agent.scan_forms import scan_forms, scan_warning_code
from v8unpack_agent.skd_extractor import extract_all_skd_queries

TOTAL_STEPS = 11
INDEX_FILE_NAME = "forms_scan_index.json"

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_CONTRACT = 3


def step(number: int, title: str) -> None:
    print()
    print(f"[{number}/{TOTAL_STEPS}] {title}")


def line(key: str, value: object) -> None:
    print(f"{key}: {value}")


def flag(value: object) -> str:
    return "true" if bool(value) else "false"


def absolute_path(root: Path, raw: object) -> Path:
    """Снять смешанную семантику путей FormEntry (#57) без угадывания.

    ``form_path`` / ``bsl_path`` / ``json_path`` документированы как
    абсолютные, ``elem_json_path`` — как relative-to-root. Скрипт принимает
    оба варианта и никогда не печатает результат.
    """
    path = Path(str(raw))
    return path if path.is_absolute() else root / path


def warning_code_summary(warnings: list[str]) -> tuple[int, list[str]]:
    counts = Counter(scan_warning_code(warning) for warning in warnings)
    without_code = counts.pop(None, 0)
    codes = sorted(f"{code}={count}" for code, count in counts.items())
    return without_code, codes


def select_form(root: Path, entries: list[Any]) -> tuple[int, Any, dict[str, bool]]:
    """Детерминированно выбрать одну форму: по признакам, затем по порядку индекса."""

    def traits(entry: Any) -> dict[str, bool]:
        elem_raw = getattr(entry, "elem_json_path", None)
        has_elem = bool(elem_raw) and absolute_path(root, elem_raw).is_file()
        has_owner_level = bool(getattr(entry, "object_name", ""))
        owner_json = object_json_path(entry) if has_owner_level else None
        has_bsl = absolute_path(root, entry.bsl_path).is_file()
        return {
            "has_elem_json": has_elem,
            "has_owner_level": has_owner_level,
            "has_owner_json": owner_json is not None,
            "has_bsl": has_bsl,
        }

    best_position = 0
    best_entry = entries[0]
    best_traits = traits(entries[0])
    best_score = (-1, -1, -1, -1)

    for position, entry in enumerate(entries):
        current = traits(entry)
        score = (
            int(current["has_elem_json"]),
            int(current["has_owner_level"]),
            int(current["has_owner_json"]),
            int(current["has_bsl"]),
        )
        if score > best_score:
            best_score = score
            best_position = position
            best_entry = entry
            best_traits = current
        if score == (1, 1, 1, 1):
            break

    return best_position, best_entry, best_traits


def run_scan(root: Path):
    step(1, "scan_forms")
    index = scan_forms(root)
    entries = list(index.forms)
    without_code, codes = warning_code_summary(list(index.scan_warnings))
    line("status", "ok")
    line("forms", index.total)
    line("entries", len(entries))
    line("scan_warnings", len(index.scan_warnings))
    line("scan_warnings_without_code", without_code)
    line("warning_codes", ", ".join(codes) if codes else "none")
    line("reference_types", len(index.reference_types))
    return index, entries


def run_drift(root: Path, index) -> None:
    step(2, "save index (temporary)")
    with TemporaryDirectory(prefix="v8unpack_walkthrough_") as temp_dir:
        index_path = Path(temp_dir) / INDEX_FILE_NAME
        saved = index.save(index_path)
        line("status", "ok")
        line("saved_inside_temp_dir", flag(Path(saved).is_file()))
        line("saved_inside_export", flag(str(root) in str(Path(saved).parent)))

        step(3, "check_drift")
        report = check_drift(root, index_path)
        line("status", "ok" if not report.has_drift else "degraded")
        line("has_drift", flag(report.has_drift))
        line("added", len(report.added))
        line("removed", len(report.removed))
        line("modified", len(report.modified))
        line("structure_modified", len(report.structure_modified))
        line("stale_extractions", len(report.stale_extractions))


def run_owner_decode(root: Path, entry: Any, index) -> None:
    step(5, "object_json_path + decode_object_attributes")
    if not getattr(entry, "object_name", ""):
        line("status", "not_applicable")
        line("reason", "no_owner_level")
        return
    owner_json = object_json_path(entry)
    if owner_json is None:
        line("status", "not_applicable")
        line("reason", "object_json_not_found")
        return

    result = decode_object_attributes(
        owner_json,
        type_resolver=index.resolve_reference_type,
    )
    properties = result.data.get("Properties") or []
    tabular = result.data.get("TabularSections") or []
    line("status", "ok" if result.ok else "degraded")
    line("decode_ok", flag(result.ok))
    line("error_code", result.error.name if result.error is not None else "none")
    line("properties", len(properties))
    line("tabular_sections", len(tabular))
    line("warnings", len(result.warnings))


def run_elem_and_coverage(root: Path, entry: Any) -> Any:
    step(6, "parse_elem_json + calc_coverage_from_elem_index")
    elem_raw = getattr(entry, "elem_json_path", None)
    if not elem_raw or not absolute_path(root, elem_raw).is_file():
        line("status", "not_applicable")
        line("reason", "elem_json_missing")
        return None

    form_root = absolute_path(root, entry.form_path)
    if not form_root.is_dir():
        line("status", "not_applicable")
        line("reason", "form_dir_missing")
        return None

    elem_result = parse_elem_json(form_root)
    line("status", "ok" if elem_result.elem_index_ok else "degraded")
    line("elem_index_ok", flag(elem_result.elem_index_ok))
    line("elements", len(elem_result.elements))
    line("elem_warnings", len(elem_result.warnings))

    coverage = calc_coverage_from_elem_index(elem_result, form_name=entry.form_name)
    line("total_elements", coverage.total_elements)
    line("data_elements", coverage.data_elements)
    line("bound_data_elements", coverage.bound_data_elements)
    line("coverage_pct", f"{coverage.coverage_pct:.2f}")
    line("coverage_form_class", coverage.form_class)
    return elem_result


def run_classify(entry: Any, elem_result: Any) -> None:
    step(7, "classify_form")
    if elem_result is None:
        line("status", "not_applicable")
        line("reason", "no_elements")
        return
    form_class = classify_form(entry.form_name, elem_result.elements)
    line("status", "ok")
    line("form_class", str(form_class))


def run_context(root: Path, entry: Any, index, max_prompt_chars: int) -> None:
    step(8, "build_form_context + to_llm_prompt_fragment")
    context = build_form_context(
        entry,
        root,
        type_resolver=index.resolve_reference_type,
    )
    bsl_text = context.bsl_text or ""
    relations = context.resolved_relations
    resolved = sum(1 for item in relations if item.get("resolved"))
    metadata_warnings = context.metadata.get("warnings") or []

    line("status", "ok")
    line("has_bsl", flag(bsl_text))
    line("bsl_chars", len(bsl_text))
    line("has_summary", flag(context.summary is not None))
    line(
        "object_attributes_status",
        "present" if context.object_attributes is not None else "absent",
    )
    line("resolved_relations_total", len(relations))
    line("resolved_relations_resolved", resolved)
    line("metadata_warnings", len(metadata_warnings))

    prompt = to_llm_prompt_fragment(context, max_prompt_chars)
    line("prompt_chars", len(prompt))
    line("object_attributes_marker", flag(OBJECT_ATTRIBUTES_MARKER in prompt))
    line("no_object_placeholder", flag(NO_OBJECT_PLACEHOLDER in prompt))


def run_managed_forms(root: Path) -> None:
    step(9, "discover_elem_forms")
    discovered = discover_elem_forms(root)
    with_elem = sum(
        1
        for item in discovered
        if item.elem_json_path and absolute_path(root, item.elem_json_path).is_file()
    )
    line("status", "ok" if discovered else "not_applicable")
    line("discovered", len(discovered))
    line("with_elem_json", with_elem)
    line("without_elem_json", len(discovered) - with_elem)


def run_skd(root: Path) -> None:
    step(10, "extract_all_skd_queries")
    batch = extract_all_skd_queries(root)
    results = list(batch.results)
    if not results:
        line("status", "not_applicable")
        line("batch_results", 0)
        line("queries_total", 0)
        return

    success = sum(1 for item in results if item.skd_extracted and item.datasets)
    empty = sum(1 for item in results if item.skd_extracted and not item.datasets)
    failed = sum(1 for item in results if not item.skd_extracted)
    queries = sum(len(item.datasets) for item in results)
    line("status", "ok" if failed == 0 else "degraded")
    line("skd_extracted", flag(batch.skd_extracted))
    line("batch_results", len(results))
    line("success", success)
    line("empty", empty)
    line("error", failed)
    line("queries_total", queries)
    line("batch_warnings", len(batch.warnings))


def run_common_modules(root: Path) -> None:
    step(11, "scan_common_modules + build_common_module_context")
    module_index = scan_common_modules(root)
    modules = list(module_index.modules)
    if not modules:
        line("status", "not_applicable")
        line("modules_total", 0)
        return

    statuses: Counter[str] = Counter()
    for module_entry in modules:
        statuses[build_common_module_context(module_entry, root).read_status] += 1

    line("status", "ok")
    line("modules_total", len(modules))
    line("ok", statuses.get("ok", 0))
    line("empty", statuses.get("empty", 0))
    line("missing", statuses.get("missing", 0))
    line("read_error", statuses.get("read_error", 0))

    demo = build_common_module_context(modules[0], root)
    demo_bsl = demo.bsl_text or ""
    line("demo_read_status", demo.read_status)
    line("demo_has_bsl", flag(demo_bsl))
    line("demo_bsl_chars", len(demo_bsl))
    line("demo_metadata_keys", len(demo.metadata))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Сквозной обезличенный пример публичного API на реальной выгрузке. "
            "Поддерживается config-layout распакованной выгрузки."
        ),
    )
    parser.add_argument(
        "root",
        metavar="EXPORT_ROOT",
        help="каталог распакованной выгрузки (cf_export)",
    )
    parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=-1,
        metavar="N",
        help="лимит длины LLM-фрагмента; -1 — без обрезки (по умолчанию)",
    )
    parser.add_argument(
        "--skip-skd",
        action="store_true",
        help="пропустить шаг 10 (обход всей выгрузки может быть долгим)",
    )
    parser.add_argument(
        "--skip-common-modules",
        action="store_true",
        help="пропустить шаг 11 (ветка общих модулей)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print("error: export root is not an existing directory", file=sys.stderr)
        return EXIT_BAD_INPUT

    index, entries = run_scan(root)
    run_drift(root, index)

    step(4, "selected form")
    if not entries:
        line("status", "not_applicable")
        line("reason", "index_is_empty")
        return EXIT_OK

    position, entry, traits = select_form(root, entries)
    line("status", "ok")
    line("selected_form_index", position)
    line("object_type", entry.object_type or "unknown")
    line("has_elem_json", flag(traits["has_elem_json"]))
    line("has_owner_level", flag(traits["has_owner_level"]))
    line("has_owner_json", flag(traits["has_owner_json"]))
    line("has_bsl", flag(traits["has_bsl"]))

    run_owner_decode(root, entry, index)
    elem_result = run_elem_and_coverage(root, entry)
    run_classify(entry, elem_result)
    run_context(root, entry, index, args.max_prompt_chars)
    run_managed_forms(root)

    if args.skip_skd:
        step(10, "extract_all_skd_queries")
        line("status", "skipped")
    else:
        run_skd(root)

    if args.skip_common_modules:
        step(11, "scan_common_modules + build_common_module_context")
        line("status", "skipped")
    else:
        run_common_modules(root)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
