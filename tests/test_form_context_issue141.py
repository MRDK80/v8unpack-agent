"""Issue #141: явное отрицательное знание для недоказанных ``data_path``.

Фикстуры синтетические и обезличенные. Проверяется контракт:

1. доказанный ``data_path`` не меняет смысла результата и не порождает
   записей отрицательного знания;
2. недоказанная привязка хранит канонический ``data_path: None`` вместе со
   стабильными ``status`` и ``reason``;
3. разные причины деградации не сводятся к одному коду, ``unknown_layout``
   и общий ``unresolved`` различаются, ``not_found`` не выдаётся без
   доказанного отсутствия;
4. LLM-проекция содержит видимый маркер в одной строке со статусом, и
   обрезка ``max_chars`` не может их разделить;
5. статус не зависит от ``warnings``, результат детерминирован.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from v8unpack_agent import form_context as form_context_module
from v8unpack_agent.elem_parser import ElemIndexResult
from v8unpack_agent.form_context import (
    BSL_MARKER,
    DATA_PATH_LINE_PREFIX,
    DATA_PATH_MARKERS,
    DATA_PATH_REASONS,
    DATA_PATH_STATUS_NOT_FOUND,
    DATA_PATH_STATUSES,
    OBJECT_ATTRIBUTES_MARKER,
    SUMMARY_MARKER,
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary

OBJECT_TYPE = "Catalog"
OBJECT_NAME = "Справочник1"
CONTAINER = "CatalogForm"
FORM_NAME = "ФормаЭлемента"

PROVEN_ELEMENT = "ПолеРеквизита"
PROVEN_PATH = "Объект.Наименование"
UNPROVEN_ELEMENT = "ПолеБезПривязки"

BSL_TEXT = "&НаКлиенте\nПроцедура ПриОткрытии(Отказ)\nКонецПроцедуры\n"

ENTRY_KEYS = {"scope", "element", "data_path", "status", "reason"}


@dataclass
class _Entry:
    """Структурно совместимая запись реестра форм (#191)."""

    form_name: str = FORM_NAME
    container_name: str = CONTAINER
    object_type: str = OBJECT_TYPE
    object_name: str = OBJECT_NAME
    form_path: str = ""
    bsl_path: str | None = None
    elem_json_path: str | None = None
    bsl_sha256: str | None = None
    elem_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)


def _form_dir(root: Path) -> Path:
    form_dir = root / OBJECT_TYPE / OBJECT_NAME / CONTAINER / FORM_NAME
    form_dir.mkdir(parents=True, exist_ok=True)
    return form_dir


def _elem_payload(with_unproven: bool) -> dict:
    tree = [{"name": PROVEN_ELEMENT, "type": "Field", "ПутьКДанным": PROVEN_PATH}]
    data = {"-pages-": ["Страница1"], f"Страница1/{PROVEN_ELEMENT}": {"id": 1}}
    if with_unproven:
        tree.append({"name": UNPROVEN_ELEMENT, "type": "Field"})
        data[f"Страница1/{UNPROVEN_ELEMENT}"] = {"id": 2}
    return {"tree": tree, "data": data, "props": []}


def _make_form(root: Path, *, with_unproven: bool = False) -> _Entry:
    form_dir = _form_dir(root)
    bsl_path = form_dir / f"{CONTAINER}.obj.bsl"
    bsl_path.write_text(BSL_TEXT, encoding="utf-8")
    (form_dir / f"{CONTAINER}.elem.json").write_text(
        json.dumps(_elem_payload(with_unproven), ensure_ascii=False),
        encoding="utf-8",
    )
    return _Entry(form_path=str(form_dir), bsl_path=str(bsl_path))


def _data_relations(context: FormContext) -> dict[str, str]:
    return {
        str(rel["element"]): str(rel["target"])
        for rel in context.summary.relations
        if rel.get("kind") == "data"
    }


def _status_lines(fragment: str) -> list[str]:
    return [line for line in fragment.split("\n") if line.startswith(DATA_PATH_LINE_PREFIX)]


def _fake_unindexed(monkeypatch) -> None:
    """Парсер не смог прочитать структуру: ветка ``elem_index_ok=False``."""

    def _parse(_form_dir):
        return ElemIndexResult(False, [], ["синтетический отказ разбора"])

    monkeypatch.setattr(form_context_module, "parse_elem_json", _parse)


# --- доказанный путь -----------------------------------------------------
def test_proven_data_path_is_unchanged(tmp_path: Path) -> None:
    entry = _make_form(tmp_path)

    context = build_form_context(entry, tmp_path)

    assert _data_relations(context) == {PROVEN_ELEMENT: PROVEN_PATH}
    assert context.unresolved_data_paths == []
    fragment = to_llm_prompt_fragment(context)
    assert _status_lines(fragment) == []
    for marker in DATA_PATH_MARKERS.values():
        assert marker not in fragment


def test_fragment_without_unproven_paths_keeps_previous_format(tmp_path: Path) -> None:
    entry = _make_form(tmp_path)
    context = build_form_context(entry, tmp_path)

    lines = to_llm_prompt_fragment(context).split("\n")

    summary_end = lines.index(OBJECT_ATTRIBUTES_MARKER)
    assert lines[summary_end - 1] == "}"


# --- отсутствующая привязка ---------------------------------------------
def test_unproven_binding_gets_explicit_status(tmp_path: Path) -> None:
    entry = _make_form(tmp_path, with_unproven=True)

    context = build_form_context(entry, tmp_path)

    names = {item["name"] for item in context.summary.elements}
    assert UNPROVEN_ELEMENT in names, "фикстура должна давать элемент без привязки"
    assert UNPROVEN_ELEMENT not in _data_relations(context)
    assert _data_relations(context) == {PROVEN_ELEMENT: PROVEN_PATH}

    assert context.unresolved_data_paths == [{
        "scope": "element",
        "element": UNPROVEN_ELEMENT,
        "data_path": None,
        "status": "unresolved",
        "reason": "binding_not_proven",
    }]

    lines = _status_lines(to_llm_prompt_fragment(context))
    assert lines == [
        'data_path: "<UNRESOLVED: путь не доказан>"; status: unresolved; '
        'reason: binding_not_proven; scope: element; '
        f'element: "{UNPROVEN_ELEMENT}"'
    ]


def test_service_elements_are_not_reported(tmp_path: Path) -> None:
    form_dir = _form_dir(tmp_path)
    payload = {
        "tree": [
            {"name": "Надпись1", "type": "Label"},
            {"name": "Группа1", "type": "Group"},
        ],
        "data": {"-pages-": ["Страница1"], "Страница1/Надпись1": {"id": 1}},
        "props": [],
    }
    (form_dir / f"{CONTAINER}.elem.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    context = build_form_context(_Entry(form_path=str(form_dir)), tmp_path)

    assert all(entry["scope"] != "element" for entry in context.unresolved_data_paths)


# --- деградация всей формы ----------------------------------------------
def test_missing_form_dir(tmp_path: Path) -> None:
    missing = tmp_path / OBJECT_TYPE / OBJECT_NAME / CONTAINER / "НетФормы"

    context = build_form_context(_Entry(form_path=str(missing)), tmp_path)

    assert context.unresolved_data_paths == [{
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unresolved",
        "reason": "form_dir_missing",
    }]


def test_missing_elem_json(tmp_path: Path) -> None:
    form_dir = _form_dir(tmp_path)

    context = build_form_context(_Entry(form_path=str(form_dir)), tmp_path)

    assert context.unresolved_data_paths == [{
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unresolved",
        "reason": "elem_json_missing",
    }]


def test_unknown_layout(tmp_path: Path, monkeypatch) -> None:
    form_dir = _form_dir(tmp_path)
    (form_dir / f"{CONTAINER}.elem.json").write_text(
        json.dumps({"unexpected": True}), encoding="utf-8"
    )
    _fake_unindexed(monkeypatch)

    context = build_form_context(_Entry(form_path=str(form_dir)), tmp_path)

    assert context.unresolved_data_paths == [{
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unknown_layout",
        "reason": "layout_not_recognized",
    }]
    lines = _status_lines(to_llm_prompt_fragment(context))
    assert lines == [
        'data_path: "<UNKNOWN_LAYOUT: путь не доказан>"; status: unknown_layout; '
        "reason: layout_not_recognized; scope: form"
    ]


def test_parse_error(tmp_path: Path, monkeypatch) -> None:
    form_dir = _form_dir(tmp_path)
    (form_dir / f"{CONTAINER}.elem.json").write_text("{не json", encoding="utf-8")
    _fake_unindexed(monkeypatch)

    context = build_form_context(_Entry(form_path=str(form_dir)), tmp_path)

    assert context.unresolved_data_paths == [{
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unresolved",
        "reason": "elem_json_invalid",
    }]


def test_reasons_are_not_collapsed(tmp_path: Path, monkeypatch) -> None:
    missing_dir = build_form_context(
        _Entry(form_path=str(tmp_path / "a" / "b" / "c" / "d")), tmp_path
    )
    no_elem_root = tmp_path / "no_elem"
    no_elem = build_form_context(
        _Entry(form_path=str(_form_dir(no_elem_root))), no_elem_root
    )
    binding_root = tmp_path / "binding"
    binding = build_form_context(
        _make_form(binding_root, with_unproven=True), binding_root
    )

    layout_root = tmp_path / "layout"
    layout_dir = _form_dir(layout_root)
    (layout_dir / f"{CONTAINER}.elem.json").write_text("{}", encoding="utf-8")
    broken_root = tmp_path / "broken"
    broken_dir = _form_dir(broken_root)
    (broken_dir / f"{CONTAINER}.elem.json").write_text("[", encoding="utf-8")
    _fake_unindexed(monkeypatch)
    layout = build_form_context(_Entry(form_path=str(layout_dir)), layout_root)
    broken = build_form_context(_Entry(form_path=str(broken_dir)), broken_root)

    contexts = (missing_dir, no_elem, binding, layout, broken)
    reasons = [ctx.unresolved_data_paths[0]["reason"] for ctx in contexts]
    assert len(set(reasons)) == 5
    statuses = {ctx.unresolved_data_paths[0]["status"] for ctx in contexts}
    assert statuses == {"unresolved", "unknown_layout"}
    assert DATA_PATH_STATUS_NOT_FOUND not in statuses


# --- канонический контракт ----------------------------------------------
def test_vocabulary_is_finite_and_consistent() -> None:
    assert DATA_PATH_STATUSES == {"unresolved", "unknown_layout", "not_found"}
    assert set(DATA_PATH_REASONS.values()) <= DATA_PATH_STATUSES
    assert DATA_PATH_STATUS_NOT_FOUND not in DATA_PATH_REASONS.values()
    assert set(DATA_PATH_MARKERS) == DATA_PATH_STATUSES
    assert len(set(DATA_PATH_MARKERS.values())) == len(DATA_PATH_MARKERS)


def test_canonical_entry_keeps_null_type(tmp_path: Path) -> None:
    context = build_form_context(_make_form(tmp_path, with_unproven=True), tmp_path)

    assert context.unresolved_data_paths
    for entry in context.unresolved_data_paths:
        assert set(entry) == ENTRY_KEYS
        assert entry["data_path"] is None
        assert json.loads(json.dumps(entry))["data_path"] is None


def test_status_does_not_depend_on_warnings(tmp_path: Path) -> None:
    context = build_form_context(_make_form(tmp_path, with_unproven=True), tmp_path)
    silent = replace(
        context,
        summary=replace(context.summary, warnings=[]),
        metadata={**context.metadata, "warnings": []},
    )

    assert _status_lines(to_llm_prompt_fragment(silent)) == _status_lines(
        to_llm_prompt_fragment(context)
    )
    assert _status_lines(to_llm_prompt_fragment(silent))


def test_manual_context_without_new_field_is_backward_compatible() -> None:
    context = FormContext(
        form_name="Ф",
        container_name="C",
        object_type="Catalog",
        object_name="О",
        bsl_text=None,
        summary=FormSummary(),
        metadata={},
    )

    assert context.unresolved_data_paths == []
    assert _status_lines(to_llm_prompt_fragment(context)) == []


def test_result_is_deterministic(tmp_path: Path) -> None:
    entry = _make_form(tmp_path, with_unproven=True)

    first = build_form_context(entry, tmp_path)
    second = build_form_context(entry, tmp_path)

    assert first.unresolved_data_paths == second.unresolved_data_paths
    assert to_llm_prompt_fragment(first) == to_llm_prompt_fragment(second)


def test_section_order_is_preserved(tmp_path: Path) -> None:
    fragment = to_llm_prompt_fragment(
        build_form_context(_make_form(tmp_path, with_unproven=True), tmp_path)
    )

    summary_at = fragment.index(SUMMARY_MARKER)
    status_at = fragment.index(DATA_PATH_LINE_PREFIX)
    object_at = fragment.index(OBJECT_ATTRIBUTES_MARKER)
    bsl_at = fragment.index(BSL_MARKER)
    assert fragment.startswith("# FORM ")
    assert summary_at < status_at < object_at < bsl_at


# --- обрезка -------------------------------------------------------------
def _multi_status_context() -> FormContext:
    entries = [
        {
            "scope": "element",
            "element": f"Поле{index}",
            "data_path": None,
            "status": "unresolved",
            "reason": "binding_not_proven",
        }
        for index in range(3)
    ] + [{
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unknown_layout",
        "reason": "layout_not_recognized",
    }]
    return FormContext(
        form_name=FORM_NAME,
        container_name=CONTAINER,
        object_type=OBJECT_TYPE,
        object_name=OBJECT_NAME,
        bsl_text=BSL_TEXT,
        summary=FormSummary(),
        metadata={},
        unresolved_data_paths=entries,
    )


def test_truncation_never_splits_marker_and_status() -> None:
    context = _multi_status_context()
    full = to_llm_prompt_fragment(context)
    full_lines = set(_status_lines(full))
    markers = tuple(DATA_PATH_MARKERS.values())

    for limit in range(1, len(full) + 2):
        fragment = to_llm_prompt_fragment(context, max_chars=limit)
        assert len(fragment) <= limit
        assert full.startswith(fragment)
        for line in fragment.split("\n"):
            if any(marker in line for marker in markers):
                assert line in full_lines, (limit, line)
            if line.startswith(DATA_PATH_LINE_PREFIX):
                assert line in full_lines, (limit, line)


def test_truncation_inside_status_line_drops_it_whole() -> None:
    context = _multi_status_context()
    full = to_llm_prompt_fragment(context)
    first = _status_lines(full)[0]
    start = full.index(first)

    for cut in (start + 1, start + len(DATA_PATH_LINE_PREFIX) + 3, start + len(first) - 1):
        fragment = to_llm_prompt_fragment(context, max_chars=cut)
        assert fragment == full[:start]
        assert len(fragment) <= cut

    exact = to_llm_prompt_fragment(context, max_chars=start + len(first))
    assert exact.endswith(first)


def test_unknown_status_is_fail_closed() -> None:
    context = replace(
        _multi_status_context(),
        unresolved_data_paths=[{
            "scope": "form",
            "element": None,
            "data_path": None,
            "status": "что-то новое",
            "reason": "x",
        }],
    )

    lines = _status_lines(to_llm_prompt_fragment(context))

    assert lines and lines[0].startswith(
        DATA_PATH_LINE_PREFIX + '"' + DATA_PATH_MARKERS["unresolved"] + '"'
    )


@pytest.mark.parametrize("max_chars", [0, -2])
def test_non_positive_limits_keep_contract(max_chars: int) -> None:
    assert to_llm_prompt_fragment(_multi_status_context(), max_chars=max_chars) == ""
