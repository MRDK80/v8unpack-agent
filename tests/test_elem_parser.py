from pathlib import Path
import json

from v8unpack_agent.elem_parser import parse_elem_json


def test_parse_group_inside_group(tmp_path: Path):
    form_root = tmp_path / "ФормаЭлемента"
    form_root.mkdir()

    elem = {
        "items": [
            {
                "name": "РамкаГруппы1",
                "type": "Group",
                "page": 1,
                "items": [
                    {
                        "name": "КнопкаВоВложеннойГруппе",
                        "type": "Button",
                        "page": 1,
                    },
                    {
                        "name": "РамкаГруппы2",
                        "type": "Group",
                        "page": 1,
                        "items": [
                            {
                                "name": "КнопкаВложенность2",
                                "type": "Button",
                                "page": 1,
                            }
                        ],
                    },
                ],
            }
        ]
    }

    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False),
        encoding="utf-8",
    )

    result = parse_elem_json(form_root)

    assert result.elem_index_ok is True
    assert len(result.elements) == 4

    by_name = {e["name"]: e for e in result.elements}
    assert by_name["КнопкаВоВложеннойГруппе"]["parent"] == "РамкаГруппы1"
    assert by_name["КнопкаВложенность2"]["parent"] == "РамкаГруппы2"


def test_parse_nested_panel(tmp_path: Path):
    form_root = tmp_path / "ФормаПанели"
    form_root.mkdir()

    elem = {
        "items": [
            {
                "name": "Страница1",
                "type": "Panel",
                "page": 1,
                "items": [
                    {
                        "name": "Страница11",
                        "type": "Panel",
                        "page": 11,
                        "items": [
                            {
                                "name": "Кнопка1",
                                "type": "Button",
                                "page": 11,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False),
        encoding="utf-8",
    )

    result = parse_elem_json(form_root)

    assert result.elem_index_ok is True

    by_name = {e["name"]: e for e in result.elements}
    assert by_name["Страница11"]["parent"] == "Страница1"
    assert by_name["Кнопка1"]["parent"] == "Страница11"


def test_missing_elem_json_is_graceful(tmp_path: Path):
    result = parse_elem_json(tmp_path)

    assert result.elem_index_ok is False
    assert result.elements == []
    assert result.warnings


def test_broken_elem_json_is_graceful(tmp_path: Path):
    form_root = tmp_path / "Форма"
    form_root.mkdir()
    (form_root / "CatalogForm.elem.json").write_text("{ broken json", encoding="utf-8")

    result = parse_elem_json(form_root)

    assert result.elem_index_ok is False
    assert result.elements == []
    assert result.warnings


def test_handler_is_detected_from_bsl(tmp_path: Path):
    form_root = tmp_path / "Форма"
    form_root.mkdir()

    elem = {
        "items": [
            {
                "name": "КнопкаЗаполнить",
                "type": "Button",
                "page": 1,
            }
        ]
    }

    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False),
        encoding="utf-8",
    )

    (form_root / "Form.obj.bsl").write_text(
        """
&НаКлиенте
Процедура КнопкаЗаполнитьНажатие(Команда)
КонецПроцедуры
""",
        encoding="utf-8",
    )

    result = parse_elem_json(form_root)

    assert result.elem_index_ok is True
    assert result.elements[0]["handler"] == "КнопкаЗаполнитьНажатие"

def test_page_is_not_filled_from_id(tmp_path: Path):
    """id узла не должен подменять номер страницы (page)."""
    form_root = tmp_path / "ФормаБезPage"
    form_root.mkdir()

    elem = {
        "items": [
            {
                "name": "ПолеБезСтраницы",
                "type": "Field",
                "id": "e3b0c442-98fc-1c14-9afb-f4c8996fb924",
            }
        ]
    }

    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False),
        encoding="utf-8",
    )

    result = parse_elem_json(form_root)

    assert result.elem_index_ok is True
    element = result.elements[0]
    assert element["name"] == "ПолеБезСтраницы"
    assert element["page"] != "e3b0c442-98fc-1c14-9afb-f4c8996fb924"
    assert element["page"] is None