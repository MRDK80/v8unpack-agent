"""Тесты для form_summary (issue #69).

Покрывают:
- новые публичные символы: FormSummary, build_form_summary,
  build_form_summary_from_elem_index, to_normalized_json;
- backward-compat алиасы в managed_form_summary (ManagedFormSummary,
  build_managed_form_summary, build_managed_form_summary_from_elem_index)
  с DeprecationWarning.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from v8unpack_agent.form_summary import (
    FormSummary,
    build_form_summary,
    build_form_summary_from_elem_index,
    to_normalized_json,
)
from v8unpack_agent.elem_parser import ElemIndexResult


# ---------------------------------------------------------------------------
# Основные тесты (новые имена)
# ---------------------------------------------------------------------------


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

    assert isinstance(summary, FormSummary)
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

    assert isinstance(summary, FormSummary)
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

    assert isinstance(summary, FormSummary)
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

    assert isinstance(summary, FormSummary)
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

    assert isinstance(summary, FormSummary)
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


# ---------------------------------------------------------------------------
# Backward-compat: старые имена из managed_form_summary — с DeprecationWarning
# ---------------------------------------------------------------------------


def test_backward_compat_import_managed_form_summary_class() -> None:
    """ManagedFormSummary — deprecated alias, должен выдавать DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from v8unpack_agent.managed_form_summary import ManagedFormSummary  # noqa: F401

    assert any(
        issubclass(w.category, DeprecationWarning)
        and "ManagedFormSummary" in str(w.message)
        for w in caught
    ), f"Ожидался DeprecationWarning про ManagedFormSummary, получено: {[str(w.message) for w in caught]}"


def test_backward_compat_managed_form_summary_is_form_summary() -> None:
    """ManagedFormSummary — тот же класс, что FormSummary (или его подкласс)."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        from v8unpack_agent.managed_form_summary import ManagedFormSummary

    assert ManagedFormSummary is FormSummary


def test_backward_compat_build_managed_form_summary() -> None:
    """build_managed_form_summary — deprecated alias, выдаёт DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from v8unpack_agent.managed_form_summary import build_managed_form_summary  # noqa: F401

    assert any(
        issubclass(w.category, DeprecationWarning)
        and "build_managed_form_summary" in str(w.message)
        for w in caught
    ), f"Ожидался DeprecationWarning про build_managed_form_summary, получено: {[str(w.message) for w in caught]}"


def test_backward_compat_build_managed_form_summary_returns_form_summary(
    tmp_path: Path,
) -> None:
    """build_managed_form_summary работает и возвращает FormSummary-совместимый объект."""
    elem_payload = {
        "tree": [{"name": "Кнопка", "type": "Button"}],
        "data": {"-pages-": []},
        "props": [],
    }
    (tmp_path / "Form.elem.json").write_text(
        json.dumps(elem_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        from v8unpack_agent.managed_form_summary import build_managed_form_summary as bms

    result = bms(tmp_path)
    assert isinstance(result, FormSummary)


def test_backward_compat_build_managed_form_summary_from_elem_index() -> None:
    """build_managed_form_summary_from_elem_index — deprecated alias, выдаёт DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from v8unpack_agent.managed_form_summary import (  # noqa: F401
            build_managed_form_summary_from_elem_index,
        )

    assert any(
        issubclass(w.category, DeprecationWarning)
        and "build_managed_form_summary_from_elem_index" in str(w.message)
        for w in caught
    ), (
        "Ожидался DeprecationWarning про build_managed_form_summary_from_elem_index, "
        f"получено: {[str(w.message) for w in caught]}"
    )
