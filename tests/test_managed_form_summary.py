"""Тесты семантической выжимки формы (issue #69: имена обновлены).

Модуль исторически назывался managed_form_summary; после issue #69 публичные
символы живут в v8unpack_agent.form_summary. Обратная совместимость (старые
имена + DeprecationWarning) проверяется отдельно в tests/test_form_summary.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from v8unpack_agent.form_summary import (
    build_form_summary,
    build_form_summary_from_elem_index,
    to_normalized_json,
)
from v8unpack_agent.elem_parser import ElemIndexResult


def test_summary_builds_data_relations_from_elem_index() -> None:
    result = ElemIndexResult(
        elem_index_ok=True,
        elements=[
            {
                "name": "Таблица",
                "type": "Table",
                "source": "data",
                "path": "Страница1/Таблица",
                "parent": "Страница1",
                "parent_path": "Страница1",
                "page": "Страница1",
                "data_path": "Объект.Товары",
            }
        ],
        warnings=[],
    )

    summary = build_form_summary_from_elem_index(result)

    assert summary.elements == [
        {
            "name": "Таблица",
            "kind": "Table",
            "path": "Страница1/Таблица",
            "parent": "Страница1",
            "parent_path": "Страница1",
            "page": "Страница1",
            "source": "data",
        }
    ]
    assert summary.relations == [
        {
            "element": "Таблица",
            "target": "Объект.Товары",
            "kind": "data",
        }
    ]


def test_summary_builds_events_from_elem_index() -> None:
    result = ElemIndexResult(
        elem_index_ok=True,
        elements=[
            {
                "name": "КнопкаЗаписать",
                "type": "Button",
                "source": "data",
                "handler": "КнопкаЗаписатьНажатие",
            }
        ],
        warnings=[],
    )

    summary = build_form_summary_from_elem_index(result)

    assert summary.events == [
        {
            "name": "КнопкаЗаписатьНажатие",
            "element": "КнопкаЗаписать",
        }
    ]
    assert {
        "element": "КнопкаЗаписать",
        "target": "КнопкаЗаписатьНажатие",
        "kind": "event",
    } in summary.relations


def test_summary_maps_props_to_attributes() -> None:
    result = ElemIndexResult(
        elem_index_ok=True,
        elements=[
            {
                "name": "Контрагент",
                "type": "Field",
                "source": "props",
            }
        ],
        warnings=[],
    )

    summary = build_form_summary_from_elem_index(result)

    assert summary.attributes == [
        {
            "name": "Контрагент",
            "type": "Field",
        }
    ]
    assert summary.elements == []


def test_summary_keeps_parser_warnings_on_failure() -> None:
    result = ElemIndexResult(
        elem_index_ok=False,
        elements=[],
        warnings=["elem.json не найден"],
    )

    summary = build_form_summary_from_elem_index(result)

    assert summary.warnings == ["elem.json не найден"]
    assert summary.elements == []
    assert summary.relations == []


def test_summary_reads_form_dir_via_parse_elem_json(tmp_path: Path) -> None:
    elem_payload = {
        "tree": [
            {
                "name": "Таблица",
                "type": "Table",
                "ПутьКДанным": "Объект.Товары",
            }
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/Таблица": {"id": 1},
        },
        "props": [
            {
                "name": "Реквизит",
                "type": "String",
            }
        ],
    }
    (tmp_path / "Form.elem.json").write_text(
        json.dumps(elem_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = build_form_summary(tmp_path)

    assert {
        "element": "Таблица",
        "target": "Объект.Товары",
        "kind": "data",
    } in summary.relations
    assert {
        "name": "Реквизит",
        "type": "String",
    } in summary.attributes


def test_to_normalized_json_is_deterministic() -> None:
    result = ElemIndexResult(
        elem_index_ok=True,
        elements=[
            {
                "name": "Поле",
                "type": "InputField",
                "source": "data",
            }
        ],
        warnings=[],
    )

    summary = build_form_summary_from_elem_index(result)

    assert to_normalized_json(summary) == to_normalized_json(summary)
