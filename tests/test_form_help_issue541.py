"""A help page and a zero footer do not prove a complete attribute inventory."""
import base64

from scripts.audit_form_help_issue541 import compare
from scripts.form_fixture_issue500 import encoded
from v8unpack_agent.form_evidence import FormSource, analyze_form


def test_help_artifacts_are_correlated_without_admitting_an_empty_report(tmp_path):
    unpacked = tmp_path / "unpacked"
    form_dir = unpacked / "CommonForm" / "Example"
    form_dir.mkdir(parents=True)
    help_bytes = b"<html><body>Form instructions</body></html>"
    blob = "#base64:" + base64.b64encode(help_bytes).decode("ascii")
    metadata = encoded({"Тип формы": "0", "Версия элементов формы": "0-5-1",
                        "form": [[["5", "1", '"Title"', [blob], "0"]]]})
    elements = encoded({"props": []})
    (form_dir / "CommonForm.json").write_bytes(metadata)
    (form_dir / "CommonForm.elem.json").write_bytes(elements)
    (form_dir / "CommonForm.id.json").write_bytes(encoded({"uuid": "a"}))

    designer = tmp_path / "designer"
    ext = designer / "Forms" / "Example" / "Ext"
    (ext / "Help").mkdir(parents=True)
    (designer / "Forms" / "Example.xml").write_text('<Root><Form uuid="A"/></Root>', encoding="utf-8")
    (ext / "Help.xml").write_text("<Help/>", encoding="utf-8")
    (ext / "Help" / "ru.html").write_bytes(b"<p>Form   instructions</p>")
    assert compare(unpacked, designer) == {
        "forms": 1, "designer_uuid_matches": 1, "binary_form": 0,
        "xml_form": 0, "help_descriptor": 1, "matching_help_text": 1,
    }
    evidence = analyze_form(FormSource("example", metadata, elements))
    assert evidence.status == "unsupported"
