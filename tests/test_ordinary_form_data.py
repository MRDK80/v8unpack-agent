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
