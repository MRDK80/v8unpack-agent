import json

import pytest

from v8unpack_agent.ordinary_form_data import (
    OrdinaryFormDataError,
    capture_ordinary_form,
    validate_ordinary_form_data,
)


@pytest.fixture
def extraction(tmp_path):
    def write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    folder = tmp_path / "Form" / "Main"
    folder.mkdir(parents=True)
    write(tmp_path / "ExternalDataProcessor.json", {"uuid": "1071d08d-a9c9-4e2e-a01d-853560cda419"})
    write(folder / "Form.id.json", {"uuid": "ba5641c6-e3bd-4945-b092-25d0b620dff9"})
    write(folder / "Form.json", {
        "Тип формы": "0",
        "Версия элементов формы": "0-27",
        "code_info_obj": 1,
        "header": [["header"]],
        "form": [[["layout", ["opaque"]]]],
    })
    write(folder / "Form.elem.json", {
        "tree": [{"name": "Action", "type": "Button", "page": "Main"}],
        "data": {"Main/Action": {"raw": ["node", ["preserved", "order"]], "id": "1"}},
        "props": [],
        "params": [],
        "commands": [],
    })
    (folder / "Form.obj.bsl").write_bytes(b"\xef\xbb\xbf// Module\r\n")
    return tmp_path, folder


def test_capture_keeps_source_order_and_module(extraction):
    root, folder = extraction
    originals = {path.name: path.read_bytes() for path in folder.iterdir()}
    data = capture_ordinary_form(root, "Form/Main")
    assert data["owner_kind"] == "epf"
    assert data["module"] == "// Module\r\n"
    assert data["metadata"]["form"] == [[["layout", ["opaque"]]]]
    assert data["elements"]["data"]["Main/Action"]["raw"] == ["node", ["preserved", "order"]]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == originals


def test_missing_module_fails_and_empty_module_is_retained(extraction):
    root, folder = extraction
    module = folder / "Form.obj.bsl"
    module.unlink()
    with pytest.raises(OrdinaryFormDataError, match="cannot read .bsl source"):
        capture_ordinary_form(root, "Form/Main")
    module.write_bytes(b"")
    assert capture_ordinary_form(root, "Form/Main")["module"] == ""


def test_split_areas_and_path_traversal_fail(extraction):
    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    data["elements"]["tree"][0]["name"] = "include_Area"
    with pytest.raises(OrdinaryFormDataError, match="split form areas"):
        validate_ordinary_form_data(data)
    with pytest.raises(OrdinaryFormDataError, match="invalid form location"):
        capture_ordinary_form(root, "../Form/Main")


def test_restore_edits_one_coordinate_and_preserves_source(extraction, tmp_path):
    from v8unpack_agent.ordinary_form_restore import restore_ordinary_form

    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    coordinate_block = ["8", "0", "10", "30", "35"] + ["0"] * 20
    raw = [
        "6ff79819-710e-4145-97cd-1618da79e3e2", "1", [], coordinate_block, [], [],
    ]
    data["elements"]["data"]["Main/Action"]["raw"] = raw
    original = json.dumps(data)
    destination = tmp_path / "edited"
    files = restore_ordinary_form(data, destination, element="Main/Action", right_edge=25)
    assert {file.name for file in files} == {
        "Form.json", "Form.id.json", "Form.elem.json", "Form.obj.bsl",
    }
    expected = json.loads(original)["elements"]
    expected["data"]["Main/Action"]["raw"][3][3] = "25"
    assert json.loads((destination / "Form.elem.json").read_bytes()) == expected
    assert (destination / "Form.obj.bsl").read_bytes() == b"// Module\r\n"
    assert json.dumps(data) == original
    with pytest.raises(FileExistsError):
        restore_ordinary_form(data, destination)


def test_restore_invalid_edit_and_write_failure_leave_no_result(extraction, tmp_path, monkeypatch):
    from pathlib import Path

    from v8unpack_agent.ordinary_form_restore import restore_ordinary_form

    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    destination = tmp_path / "output"
    with pytest.raises(OrdinaryFormDataError, match="unique root"):
        restore_ordinary_form(data, destination, element="absent", right_edge=10)
    assert not destination.exists()

    write_bytes = Path.write_bytes

    def fail_module(path, content):
        if path.suffix == ".bsl":
            raise OSError("simulated write failure")
        return write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_module)
    with pytest.raises(OSError, match="simulated write failure"):
        restore_ordinary_form(data, destination)
    assert not destination.exists()


def test_restore_cli_validation_and_existing_destination(extraction, tmp_path, capsys):
    from v8unpack_agent.ordinary_form_cli import main

    root, _ = extraction
    bundle = tmp_path / "capture.json"
    output = tmp_path / "output"
    command = ["restore-ordinary-form", str(bundle), str(output)]
    bundle.write_text("{}", encoding="utf-8")
    assert main(command) == 2
    assert "schema" in capsys.readouterr().err
    assert not output.exists()

    bundle.write_text(json.dumps(capture_ordinary_form(root, "Form/Main")), encoding="utf-8")
    original = bundle.read_bytes()
    assert main(command) == 0
    generated = (output / "Form.elem.json").read_bytes()
    assert main(command) == 5
    assert (output / "Form.elem.json").read_bytes() == generated
    assert bundle.read_bytes() == original


def test_missing_raw_record_fails_before_restore(extraction):
    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    data["elements"]["data"]["Main/Action"].pop("raw")
    with pytest.raises(OrdinaryFormDataError, match="raw element record"):
        validate_ordinary_form_data(data)


def test_add_button_cli_preserves_prototype_and_binds_new_handler(extraction, tmp_path):
    from v8unpack_agent.ordinary_form_cli import main

    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    anchor = ["0", ["2", "2", "1", "-22"], ["2", "-1", "6", "0"]]
    prototype_raw = [
        "6ff79819-710e-4145-97cd-1618da79e3e2", "1",
        ["1", [[], "14", ["1", "1", ['"ru"', '"Action"']]],
         ["1", ["0", "fbe38877-b914-4fd5-8540-07dde06ba2e1", ["6"]]]],
        ["8", "0", "50", "30", "75", "1", anchor, [*anchor[:1], anchor[1][:], anchor[2][:]]],
        ["14", '"Action"', "4294967295", "0", "0", "0"], ["0"],
    ]
    data["elements"]["data"]["Main/Action"]["raw"] = prototype_raw
    bundle = tmp_path / "form.json"
    bundle.write_text(json.dumps(data), encoding="utf-8")
    source = bundle.read_bytes()
    statements = tmp_path / "body.bsl"
    statements.write_text('    Сообщить("works");', encoding="utf-8")
    output = tmp_path / "edited"
    args = [
        "add-ordinary-form-button", str(bundle), str(output), "--prototype", "Main/Action",
        "--name", "NewButton", "--caption", 'Run "check"', "--handler", "OnCheck",
        "--handler-body", str(statements), "--vertical-offset", "-30",
    ]
    assert main(args) == 0
    actual = json.loads((output / "Form.elem.json").read_text(encoding="utf-8"))
    assert actual["tree"] == [
        {"name": "Action", "type": "Button", "page": "Main"},
        {"name": "NewButton", "type": "Button", "page": "Main"},
    ]
    assert actual["data"]["Main/Action"]["raw"] == prototype_raw
    new = actual["data"]["Main/NewButton"]
    assert new["id"] == new["raw"][1] == "2"
    assert new["raw"][2][2][1][2][1] == '"OnCheck"'
    assert new["raw"][2][1][2][2][1] == '"Run ""check"""'
    assert new["raw"][3][2:5] == ["20", "30", "45"]
    assert new["raw"][3][6][1][3] == "-52"
    assert b"// Module\r\n" in (output / "Form.obj.bsl").read_bytes()
    assert "Процедура OnCheck(Кнопка)" in (output / "Form.obj.bsl").read_text(encoding="utf-8")
    assert bundle.read_bytes() == source
    args[2] = str(tmp_path / "invalid")
    args[6] = "Action"
    assert main(args) == 2
    assert not (tmp_path / "invalid").exists()


def test_remove_menu_item_preserves_sibling_and_command_definitions(extraction):
    from copy import deepcopy

    from v8unpack_agent.ordinary_form_edits import remove_ordinary_form_menu_item

    root, _ = extraction
    data = capture_ordinary_form(root, "Form/Main")
    elements = data["elements"]
    elements["tree"][0]["type"] = "CommandPanel"
    def item(name):
        return ["8", f'"{name}"', "0", "1", ["caption"], "1"] + ["0"] * 10
    menu = [
        "5", "b78f2e80-ec68-11d4-9dcf-0050bae2bc79", "4", "0", "2",
        "command-a", item("Remove"), "command-b", item("Keep"), ["trailer"],
    ]
    commands = ["5", "table", "7", "1", "2", ["action-a"], ["action-b"], "1", menu]
    raw = [
        "e69bf21d-97b2-4f37-86db-675aea9ec2cb", "1",
        ["2", [[], "9", "2", "0", "2", "0", "1", commands]], [], [], [],
    ]
    elements["data"]["Main/Action"]["raw"] = raw
    original = deepcopy(data)
    actual = remove_ordinary_form_menu_item(data, panel="Main/Action", name="Remove")
    edited = actual["elements"]["data"]["Main/Action"]["raw"][2][1][7]
    assert edited[:-1] == commands[:-1]
    assert edited[-1] == menu[:4] + ["1", "command-b", item("Keep"), ["trailer"]]
    assert actual["module"] == original["module"]
    assert data == original
    with pytest.raises(OrdinaryFormDataError, match="expected one actionable"):
        remove_ordinary_form_menu_item(actual, panel="Main/Action", name="Remove")
