from pathlib import Path

from v8unpack_agent import form_paths, item_modules, all_module_paths


def test_form_paths_convention(tmp_path):
    p = form_paths(tmp_path / "unpacked", "ФормаЭлемента")
    assert p["object_module"] == tmp_path / "unpacked" / "Form" / "ФормаЭлемента" / "Form.obj.bsl"
    assert p["ext_module"] == tmp_path / "unpacked" / "Form" / "ФормаЭлемента" / "Ext" / "ObjectModule.bsl"
    assert p["metadata"] == tmp_path / "unpacked" / "Form" / "ФормаЭлемента" / "Form.json"


def test_item_modules_empty_without_dir(tmp_path):
    assert item_modules(tmp_path / "unpacked", "ФормаЭлемента") == ()


def test_item_modules_collects_nested_panels(tmp_path):
    base = tmp_path / "unpacked" / "Form" / "ФормаСписка" / "Items"
    (base / "Группа1" / "Страница1").mkdir(parents=True)
    a = base / "Группа1" / "Панель.bsl"
    b = base / "Группа1" / "Страница1" / "Вложенная.bsl"
    a.write_text("// код", encoding="utf-8")
    b.write_text("// код", encoding="utf-8")
    (base / "readme.txt").write_text("not bsl", encoding="utf-8")

    items = item_modules(tmp_path / "unpacked", "ФормаСписка")
    assert set(items) == {a, b}
    assert items == tuple(sorted(items))  # отсортировано


def test_all_module_paths_only_existing(tmp_path):
    root = tmp_path / "unpacked"
    form_dir = root / "Form" / "ФормаЭлемента"
    (form_dir).mkdir(parents=True)
    (form_dir / "Form.obj.bsl").write_text("// форма", encoding="utf-8")
    # ext_module отсутствует на диске → не должен попасть
    (form_dir / "Items").mkdir()
    (form_dir / "Items" / "Панель.bsl").write_text("// панель", encoding="utf-8")

    paths = all_module_paths(root, "ФормаЭлемента")
    names = {p.name for p in paths}
    assert names == {"Form.obj.bsl", "Панель.bsl"}
