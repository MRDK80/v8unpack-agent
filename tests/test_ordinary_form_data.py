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
