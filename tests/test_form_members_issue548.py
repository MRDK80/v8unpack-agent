"""A help-only source is distinct from a source with a layout member."""
from v8unpack.helper import brace_file_write

from scripts.audit_form_members_issue548 import trace
from scripts.form_fixture_issue500 import encoded


def test_trace_separates_help_only_from_form_member(tmp_path):
    uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    source = tmp_path / "source"
    form = source / "CommonForm" / "Synthetic"
    form.mkdir(parents=True)
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    help_root = [["5", "1", '"Help"', ['"HTML"'], "0"]]
    (form / "CommonForm.json").write_bytes(encoded({
        "Версия элементов формы": "0-5-1", "form": [help_root],
    }))
    (form / "CommonForm.elem.json").write_bytes(encoded({"props": []}))
    (form / "CommonForm.id.json").write_bytes(encoded({"uuid": uuid.upper()}))
    (catalog / uuid).write_bytes(b"metadata")
    brace_file_write(help_root, str(catalog), uuid + ".1")
    assert trace(source, catalog) == {"forms": 1, "header_and_1_only": 1, "identical_help_root": 1}

    (catalog / (uuid + ".0")).mkdir()
    assert trace(source, catalog) == {"forms": 1, "other_member_layout": 1}
