"""Тесты для issue #NEW: object_attributes и resolved_relations в FormContext."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
    OBJECT_ATTRIBUTES_MARKER,
    NO_OBJECT_PLACEHOLDER,
)
from v8unpack_agent.form_summary import FormSummary


@dataclass
class _FakeFormEntry:
    form_name: str = "ФормаЭлемента"
    container_name: str = "Номенклатура"
    object_type: str = "Catalog"
    object_name: str = "Номенклатура"
    form_path: str = ""
    bsl_path: str | None = None
    elem_json_path: str | None = None
    bsl_sha256: str | None = None
    elem_sha256: str | None = None
    warnings: list[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def _make_export(tmp_path: Path) -> Path:
    """Собрать минимальную структуру выгрузки Catalog/Номенклатура/... для теста."""
    object_dir = tmp_path / "Catalog" / "Номенклатура"
    object_dir.mkdir(parents=True)

    # Файл объекта метаданных: compact-layout, распознаваемый _walk_node
    # в object_decoder (best-effort fallback-путь модуля).
    object_json = {
        "header": [
            0,
            [
                0,
                [0, [0, 0, "11111111-1111-1111-1111-111111111111"],
                 '"ОсновнойПоставщик"', '"Ref#22222222-2222-2222-2222-222222222222"',
                 '"Основной поставщик"'],
            ],
        ]
    }
    (object_dir / "Catalog.json").write_text(
        json.dumps(object_json, ensure_ascii=False), encoding="utf-8"
    )

    form_dir = object_dir / "Forms" / "ФормаЭлемента"
    form_dir.mkdir(parents=True)
    return form_dir


def test_object_attributes_present_when_object_json_found(tmp_path):
    form_dir = _make_export(tmp_path)
    form_entry = _FakeFormEntry(form_path=str(form_dir / "form.json"))

    context = build_form_context(form_entry, tmp_path)

    assert context.object_attributes is not None
    assert context.metadata["has_object_attributes"] is True


def test_object_attributes_none_when_object_json_missing(tmp_path):
    lone_form_dir = tmp_path / "Catalog" / "БезОбъекта" / "Forms" / "Форма"
    lone_form_dir.mkdir(parents=True)
    form_entry = _FakeFormEntry(
        container_name="БезОбъекта",
        object_name="БезОбъекта",
        form_path=str(lone_form_dir / "form.json"),
    )

    context = build_form_context(form_entry, tmp_path)

    assert context.object_attributes is None
    assert context.metadata["has_object_attributes"] is False
    assert any("не найден" in w for w in context.metadata["warnings"])


def test_prompt_fragment_contains_object_marker(tmp_path):
    form_dir = _make_export(tmp_path)
    form_entry = _FakeFormEntry(form_path=str(form_dir / "form.json"))
    context = build_form_context(form_entry, tmp_path)

    fragment = to_llm_prompt_fragment(context, max_chars=-1)

    assert OBJECT_ATTRIBUTES_MARKER in fragment
    assert fragment.index(OBJECT_ATTRIBUTES_MARKER) < fragment.index("## BSL")


def test_prompt_fragment_placeholder_when_no_object(tmp_path):
    lone_form_dir = tmp_path / "Catalog" / "БезОбъекта" / "Forms" / "Форма"
    lone_form_dir.mkdir(parents=True)
    form_entry = _FakeFormEntry(
        container_name="БезОбъекта",
        object_name="БезОбъекта",
        form_path=str(lone_form_dir / "form.json"),
    )
    context = build_form_context(form_entry, tmp_path)

    fragment = to_llm_prompt_fragment(context, max_chars=-1)

    assert NO_OBJECT_PLACEHOLDER in fragment


def test_resolved_relations_only_covers_data_kind():
    summary = FormSummary(
        relations=[
            {"element": "Наименование", "target": "Объект.Наименование", "kind": "data"},
            {"element": "Наименование", "target": "НаименованиеПриИзменении", "kind": "event"},
        ]
    )
    context = FormContext(
        form_name="x", container_name="y", object_type="Catalog", object_name="z",
        bsl_text=None, summary=summary, metadata={},
        object_attributes=None, resolved_relations=[
            {"data_path": "Объект.Наименование", "object_type": "", "attribute_name": "Наименование",
             "value_type": None, "synonym": None, "resolved": False}
        ],
    )
    assert len(context.resolved_relations) == 1
    assert context.resolved_relations[0]["data_path"] == "Объект.Наименование"
