"""Явный признак недоказанного ссылочного типа во фрагменте — issue #289.

Имена и UUID синтетические. Проверяются пометка реквизита рядом с
``Ref#<uuid>``, агрегат формы в ``## SUMMARY``, его сохранение при обрезке
хвоста и неизменность значения типа.
"""

from __future__ import annotations

import json

from v8unpack_agent.form_context import (
    BSL_MARKER,
    OBJECT_ATTRIBUTES_MARKER,
    REFERENCE_TYPES_LINE_PREFIX,
    SUMMARY_MARKER,
    FormContext,
    FormSummary,
    to_llm_prompt_fragment,
)
from v8unpack_agent.platform_reference_types import PLATFORM_REFERENCE_TYPES

UUID = "00000000-0000-0000-0000-0000000000aa"
REF = "Ref#" + UUID
STATIC_NAME = min(PLATFORM_REFERENCE_TYPES.values())
BSL = "Процедура П()\n" + "// строка\n" * 200 + "КонецПроцедуры"


def ctx(attributes):
    return FormContext(
        form_name="Ф",
        container_name="C",
        object_type="Catalog",
        object_name="О",
        bsl_text=BSL,
        summary=FormSummary(),
        metadata={},
        object_attributes=attributes,
    )


MIXED = {
    "Properties": [
        {"Name": "А", "Type": "CatalogRef.Товары"},
        {"Name": "Б", "Type": REF},
        {"Name": "В", "Type": "String"},
        {"Name": "Г", "Type": STATIC_NAME},
    ],
    "TabularSections": [
        {"Name": "Т", "Attributes": [{"Name": "Д", "Type": REF}]},
    ],
}


def object_block(fragment: str) -> dict:
    start = fragment.index(OBJECT_ATTRIBUTES_MARKER) + len(OBJECT_ATTRIBUTES_MARKER) + 1
    end = fragment.index("\n" + BSL_MARKER)
    return json.loads(fragment[start:end])


def aggregate(fragment: str) -> list[str]:
    return [line for line in fragment.splitlines() if line.startswith(REFERENCE_TYPES_LINE_PREFIX)]


def test_partial_unresolved_marks_only_unresolved_and_keeps_value() -> None:
    fragment = to_llm_prompt_fragment(ctx(MIXED))
    block = object_block(fragment)
    props = {p["Name"]: p for p in block["Properties"]}
    assert props["Б"] == {"Name": "Б", "Type": REF, "type_resolved": False}
    for name in ("А", "В", "Г"):
        assert "type_resolved" not in props[name]
    nested = block["TabularSections"][0]["Attributes"][0]
    assert nested["Type"] == REF and nested["type_resolved"] is False
    assert aggregate(fragment) == [
        REFERENCE_TYPES_LINE_PREFIX + "applicable: 4; resolved: 2; unresolved: 2"
    ]


def test_all_resolved_has_no_marker_and_zero_unresolved() -> None:
    attrs = {"Properties": [{"Name": "А", "Type": "CatalogRef.Товары"}], "TabularSections": []}
    fragment = to_llm_prompt_fragment(ctx(attrs))
    assert "type_resolved" not in fragment
    assert aggregate(fragment) == [
        REFERENCE_TYPES_LINE_PREFIX + "applicable: 1; resolved: 1; unresolved: 0"
    ]


def test_without_object_attributes_has_no_aggregate() -> None:
    fragment = to_llm_prompt_fragment(ctx(None))
    assert aggregate(fragment) == []
    assert "type_resolved" not in fragment


def test_aggregate_survives_truncation_of_bsl_tail() -> None:
    full = to_llm_prompt_fragment(ctx(MIXED))
    limit = full.index(BSL_MARKER) + len(BSL_MARKER) + 20
    cut = to_llm_prompt_fragment(ctx(MIXED), max_chars=limit)
    assert len(cut) <= limit and len(cut) < len(full)
    assert aggregate(cut) == aggregate(full)
    # Агрегат стоит в SUMMARY, до блока BSL.
    assert full.index(aggregate(full)[0]) < full.index(OBJECT_ATTRIBUTES_MARKER)
    assert full.index(SUMMARY_MARKER) < full.index(aggregate(full)[0])


def test_aggregate_line_is_atomic_under_truncation() -> None:
    full = to_llm_prompt_fragment(ctx(MIXED))
    line = aggregate(full)[0]
    start = full.index(line)
    for limit in range(start + 1, start + len(line)):
        cut = to_llm_prompt_fragment(ctx(MIXED), max_chars=limit)
        assert line[:5] not in cut[start:] or cut.endswith(line) is False
        assert not any(
            part.startswith(REFERENCE_TYPES_LINE_PREFIX) and part != line
            for part in cut.splitlines()
        )


def test_deterministic_bytes() -> None:
    assert to_llm_prompt_fragment(ctx(MIXED)).encode() == to_llm_prompt_fragment(ctx(MIXED)).encode()


def test_source_attributes_are_not_mutated() -> None:
    attrs = json.loads(json.dumps(MIXED))
    to_llm_prompt_fragment(ctx(attrs))
    assert attrs == MIXED


def test_docs_example_matches_code() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs" / "form_context.md").read_text(encoding="utf-8")
    section = text[text.index("## Недоказанный ссылочный тип во фрагменте (#289)"):]
    block = section[section.index("```\n") + 4: section.index("\n```", section.index("```\n") + 4)]
    example = FormContext(
        form_name="ФормаЭлемента",
        container_name="CatalogForm",
        object_type="Catalog",
        object_name="Пример",
        bsl_text="Процедура ПриОткрытии(Отказ)\nКонецПроцедуры",
        summary=FormSummary(),
        metadata={},
        object_attributes={
            "Properties": [
                {"Name": "Владелец", "Type": "CatalogRef.Пример"},
                {"Name": "Основание", "Type": "Ref#00000000-0000-0000-0000-000000000001"},
            ],
            "TabularSections": [],
        },
    )
    assert block == to_llm_prompt_fragment(example)
