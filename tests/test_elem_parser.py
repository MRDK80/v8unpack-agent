"""Тесты парсера elem.json обычной формы 1С.

Покрытие:
- достоверная иерархия панелей/страниц из data-путей (вкл. пустые страницы);
- parent = ближайший узел дерева (страница), parent_path/path для развязки
  одноимённых узлов;
- группы: data-ключи плоские → parent = страница, вложенность групп НЕ
  реконструируется (raw не декодируем по лицензии), выставляется warning;
- типы/handler из tree, реквизиты из props (source=props);
- best-effort на отсутствие/битый файл;
- фолбэк-обход для elem.json без секции data.
"""

from __future__ import annotations

import json
from pathlib import Path

from v8unpack_agent.elem_parser import parse_elem_json


def _write(form_root: Path, elem: dict) -> None:
    form_root.mkdir(parents=True, exist_ok=True)
    (form_root / "Form.elem.json").write_text(
        json.dumps(elem, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------- панели

def test_data_paths_build_full_panel_hierarchy(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {
        "props": [{"name": "ОбработкаОбъект", "id": "1"}],
        "tree": [
            {"name": "ПанельВерхняя", "type": "Panel"},
            {"name": "ПанельВложенная", "type": "Panel"},
            {"name": "КнопкаНаСтранице11", "type": "Button"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/ПанельВерхняя": {"id": "4"},
            "Страница1/ПанельВерхняя/-pages-": ["Страница1", "Страница2", "Страница3"],
            "Страница1/ПанельВерхняя/Страница1/ПанельВложенная": {"id": "5"},
            "Страница1/ПанельВерхняя/Страница1/ПанельВложенная/-pages-": ["Страница11", "Страница12"],
            "Страница1/ПанельВерхняя/Страница1/ПанельВложенная/Страница11/КнопкаНаСтранице11": {"id": "6"},
        },
    }
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True
    by_path = {e["path"]: e for e in result.elements if e.get("path")}

    assert any(e["name"] == "Страница12" and e["type"] == "Page" for e in result.elements)
    assert any(e["name"] == "Страница2" for e in result.elements)
    assert any(e["name"] == "Страница3" for e in result.elements)

    btn = by_path["Страница1/ПанельВерхняя/Страница1/ПанельВложенная/Страница11/КнопкаНаСтранице11"]
    assert btn["type"] == "Button"
    assert btn["parent"] == "Страница11"
    assert btn["source"] == "data"

    inner = by_path["Страница1/ПанельВерхняя/Страница1/ПанельВложенная"]
    assert inner["type"] == "Panel"
    assert inner["parent"] == "Страница1"
    # групп нет — warning о группах не ставится
    assert not any("вложенность групп не восстановлена" in w for w in result.warnings)


def test_collision_resolved_by_parent_path(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {
        "tree": [{"name": "ПанельВерхняя", "type": "Panel"}],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/ПанельВерхняя": {"id": "4"},
            "Страница1/ПанельВерхняя/-pages-": ["Страница1", "Страница2"],
            "Страница1/ПанельВерхняя/Страница1/ПанельВложенная": {"id": "5"},
        },
    }
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    страница1 = [e for e in result.elements if e["name"] == "Страница1"]
    parent_paths = {e["parent_path"] for e in страница1}
    assert None in parent_paths
    assert "Страница1/ПанельВерхняя" in parent_paths

    inner = next(e for e in result.elements if e["name"] == "ПанельВложенная")
    assert inner["parent_path"] == "Страница1/ПанельВерхняя/Страница1"


# ---------------------------------------------------------------- группы (реальная плоская структура)

def test_group_paths_are_flat_and_warns(tmp_path: Path):
    """На реальной форме ключи data для групп плоские: вложенность группа->группа
    в путях отсутствует. Парсер ставит parent=страница и честный warning;
    raw не декодируется по лицензии."""
    form_root = tmp_path / "Форма"
    elem = {
        "props": [{"name": "ОбработкаОбъект", "id": "1"}, {"name": "ПолеВвода1", "id": "2"}],
        "tree": [
            {"name": "РамкаГруппыВнешняя", "type": "Group"},
            {"name": "НадписьВнутриГруппы", "type": "Label"},
            {"name": "РамкаГруппыВложенная", "type": "Group"},
            {"name": "ПолеВвода1", "type": "Field"},
        ],
        "data": {  # как в реальном Form.elem.json: пути плоские
            "-pages-": ["Страница1"],
            "Страница1/РамкаГруппыВнешняя": {"id": "4"},
            "Страница1/НадписьВнутриГруппы": {"id": "5"},
            "Страница1/РамкаГруппыВложенная": {"id": "6"},
            "Страница1/ПолеВвода1": {"id": "8"},
        },
    }
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True

    by_name = {e["name"]: e for e in result.elements if e["source"] == "data"}
    # вложенность групп НЕ восстановлена: всё на странице
    assert by_name["РамкаГруппыВнешняя"]["parent"] == "Страница1"
    assert by_name["РамкаГруппыВложенная"]["parent"] == "Страница1"
    assert by_name["ПолеВвода1"]["parent"] == "Страница1"

    # честный warning присутствует, упоминает raw и лицензию
    grp_warn = [w for w in result.warnings if "вложенность групп не восстановлена" in w]
    assert grp_warn
    assert "raw" in grp_warn[0]
    assert "лицензи" in grp_warn[0]


def test_props_kept_separate_from_data(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {
        "props": [{"name": "ПолеВвода1", "id": "2"}],
        "tree": [{"name": "ПолеВвода1", "type": "Field"}],
        "data": {"-pages-": ["Страница1"], "Страница1/ПолеВвода1": {"id": "8"}},
    }
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    sources = {(e["name"], e["source"]) for e in result.elements}
    assert ("ПолеВвода1", "props") in sources
    assert ("ПолеВвода1", "data") in sources


# ---------------------------------------------------------------- best-effort

def test_missing_elem_json_is_graceful(tmp_path: Path):
    form_root = tmp_path / "Форма"
    form_root.mkdir()
    result = parse_elem_json(form_root)
    assert result.elem_index_ok is False
    assert result.elements == []
    assert result.warnings


def test_broken_elem_json_is_graceful(tmp_path: Path):
    form_root = tmp_path / "Форма"
    form_root.mkdir()
    (form_root / "Form.elem.json").write_text("{не json", encoding="utf-8")
    result = parse_elem_json(form_root)
    assert result.elem_index_ok is False
    assert result.elements == []
    assert result.warnings


def test_handler_is_detected_from_bsl(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {
        "tree": [{"name": "КнопкаВыполнить", "type": "Button"}],
        "data": {"-pages-": ["Страница1"], "Страница1/КнопкаВыполнить": {"id": "4"}},
    }
    _write(form_root, elem)
    (form_root / "Form.obj.bsl").write_text(
        "Процедура КнопкаВыполнитьНажатие(Команда)\nКонецПроцедуры\n", encoding="utf-8"
    )

    result = parse_elem_json(form_root)
    btn = next(e for e in result.elements if e["name"] == "КнопкаВыполнить")
    assert btn.get("handler") == "КнопкаВыполнитьНажатие"


# ---------------------------------------------------------------- фолбэк без data

def test_fallback_items_tree_without_data(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {"tree": [{"name": "Группа", "type": "Group", "items": [{"name": "Поле", "type": "Field"}]}]}
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True
    field = next(e for e in result.elements if e["name"] == "Поле")
    assert field["parent"] == "Группа"


def test_fallback_flat_groups_warns(tmp_path: Path):
    form_root = tmp_path / "Форма"
    elem = {"tree": [{"name": "РамкаГруппыВнешняя", "type": "Group"}, {"name": "РамкаГруппыВложенная", "type": "Group"}]}
    _write(form_root, elem)

    result = parse_elem_json(form_root)
    assert result.elem_index_ok is True
    assert all(e["parent"] is None for e in result.elements)
    assert any("вложенность групп не восстановлена" in w for w in result.warnings)
