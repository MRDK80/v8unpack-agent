"""TDD-тесты для decode_element_data_path (issue #85).

Покрытие:
- успешное декодирование data_path из raw-вектора (список-список-строка);
- успешное декодирование из dict-формата {"DataPath": ...};
- неизвестный/повреждённый raw → None + warning, не исключение;
- None raw → None, без warning;
- интеграция: parse_elem_json читает data_path из data[*].raw;
- интеграция: FormSummary.relations содержит kind="data" с правильным target;
- обезличенный минимальный raw-фрагмент реальной формы.
"""

from __future__ import annotations

import json
from pathlib import Path


from v8unpack_agent.elem_parser import (
    ElemIndexResult,
    decode_element_data_path,
    parse_elem_json,
)
from v8unpack_agent.form_summary import build_form_summary_from_elem_index

# ---------------------------------------------------------------------------
# Обезличенный минимальный raw-фрагмент реальной формы
# (взят из CatalogForm.elem.json, поля обезличены, абсолютные пути удалены)
# ---------------------------------------------------------------------------

# Формат, который использует платформа 1С в elem.json:
# data["Объект/ИмяПоля"] = {"id": N, "raw": [[..., "ПутьКДанным", ...], ...]}
# Позиция строки data_path в raw варьируется; ищем по ключевым признакам.

_RAW_FIELD_GOROD = [
    [2, 1, 0],
    ["Объект.Город"],
]

_RAW_FIELD_TELEFONY = [
    [2, 1, 0],
    ["Объект.Телефоны"],
]

_RAW_DICT_FORMAT = {"DataPath": "Объект.Адрес"}

_RAW_BROKEN = {"unexpected": 42}

_RAW_EMPTY_LIST = []


# ---------------------------------------------------------------------------
# Юнит-тесты decode_element_data_path
# ---------------------------------------------------------------------------


def test_decode_raw_list_extracts_data_path_gorod():
    """Из raw-списка формата [[...], ["Объект.Город"]] извлекается 'Объект.Город'."""
    result, warnings = decode_element_data_path(_RAW_FIELD_GOROD)
    assert result == "Объект.Город"
    assert warnings == []


def test_decode_raw_list_extracts_data_path_telefony():
    """Из raw-списка формата [[...], ["Объект.Телефоны"]] извлекается 'Объект.Телефоны'."""
    result, warnings = decode_element_data_path(_RAW_FIELD_TELEFONY)
    assert result == "Объект.Телефоны"
    assert warnings == []


def test_decode_raw_dict_format():
    """Из raw-словаря {"DataPath": "..."} извлекается корректный путь."""
    result, warnings = decode_element_data_path(_RAW_DICT_FORMAT)
    assert result == "Объект.Адрес"
    assert warnings == []


def test_decode_broken_raw_returns_none_with_warning():
    """Повреждённый/неизвестный raw → None, warning, без исключения."""
    result, warnings = decode_element_data_path(_RAW_BROKEN)
    assert result is None
    assert len(warnings) == 1
    assert "raw" in warnings[0].lower() or "data_path" in warnings[0].lower()


def test_decode_empty_list_returns_none_with_warning():
    """Пустой список raw → None + warning."""
    result, warnings = decode_element_data_path(_RAW_EMPTY_LIST)
    assert result is None
    assert len(warnings) == 1


def test_decode_none_returns_none_no_warning():
    """None в качестве raw → None, warnings пустой."""
    result, warnings = decode_element_data_path(None)
    assert result is None
    assert warnings == []


def test_decode_string_data_path_passthrough():
    """Если raw уже является строкой (простой путь) — возвращается как есть."""
    result, warnings = decode_element_data_path("Объект.Наименование")
    assert result == "Объект.Наименование"
    assert warnings == []


# ---------------------------------------------------------------------------
# Интеграция: parse_elem_json читает data_path из data[*].raw
# ---------------------------------------------------------------------------


def _write_form(form_root: Path, elem: dict) -> None:
    form_root.mkdir(parents=True, exist_ok=True)
    (form_root / "Form.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False), encoding="utf-8"
    )


def test_parse_elem_json_extracts_data_path_from_raw(tmp_path: Path):
    """parse_elem_json добавляет data_path к элементу, если raw содержит его."""
    form_root = tmp_path / "ФормаЭлемента"
    elem = {
        "tree": [
            {"name": "Город", "type": "Field"},
            {"name": "Телефоны", "type": "Field"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/Город": {
                "id": 10,
                "raw": [[2, 1, 0], ["Объект.Город"]],
            },
            "Страница1/Телефоны": {
                "id": 11,
                "raw": [[2, 1, 0], ["Объект.Телефоны"]],
            },
        },
    }
    _write_form(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True

    by_name = {e["name"]: e for e in result.elements if e.get("source") == "data"}
    assert by_name["Город"].get("data_path") == "Объект.Город"
    assert by_name["Телефоны"].get("data_path") == "Объект.Телефоны"


def test_parse_elem_json_no_raw_no_data_path(tmp_path: Path):
    """Если raw отсутствует, data_path не добавляется и тест не падает."""
    form_root = tmp_path / "ФормаБезRaw"
    elem = {
        "tree": [{"name": "Наименование", "type": "Field"}],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/Наименование": {"id": 5},
        },
    }
    _write_form(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True
    naim = next(e for e in result.elements if e["name"] == "Наименование")
    assert "data_path" not in naim


def test_parse_elem_json_broken_raw_adds_warning(tmp_path: Path):
    """Если raw повреждён, элемент парсится без data_path, добавляется warning."""
    form_root = tmp_path / "ФормаПоврежденный"
    elem = {
        "tree": [{"name": "Поле", "type": "Field"}],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/Поле": {"id": 7, "raw": {"unexpected": 42}},
        },
    }
    _write_form(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True
    pole = next(e for e in result.elements if e["name"] == "Поле")
    assert "data_path" not in pole
    assert any("data_path" in w.lower() or "raw" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Интеграция: FormSummary.relations содержит kind="data"
# ---------------------------------------------------------------------------


def test_form_summary_relations_contain_data_kind_from_raw(tmp_path: Path):
    """FormSummary строит relations kind=data из data_path, декодированного из raw."""
    result = ElemIndexResult(
        elem_index_ok=True,
        elements=[
            {
                "name": "Город",
                "type": "Field",
                "source": "data",
                "path": "Страница1/Город",
                "parent": "Страница1",
                "parent_path": "Страница1",
                "page": "Страница1",
                "data_path": "Объект.Город",
            },
            {
                "name": "Телефоны",
                "type": "Field",
                "source": "data",
                "path": "Страница1/Телефоны",
                "parent": "Страница1",
                "parent_path": "Страница1",
                "page": "Страница1",
                "data_path": "Объект.Телефоны",
            },
        ],
        warnings=[],
    )

    summary = build_form_summary_from_elem_index(result)

    assert {"element": "Город", "target": "Объект.Город", "kind": "data"} in summary.relations
    assert {"element": "Телефоны", "target": "Объект.Телефоны", "kind": "data"} in summary.relations
