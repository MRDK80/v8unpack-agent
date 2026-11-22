"""Behavior of SKD query editing on synthetic binary layouts."""
from codecs import BOM_UTF8
from struct import pack, unpack_from

import pytest

from v8unpack_agent.ordinary_form_cli import main
from v8unpack_agent.skd_edit import replace_skd_query

_NAMESPACE = "http://v8.1c.ru/8.1/data-composition-system/schema"
_SETTINGS = BOM_UTF8 + b"<Settings/>"
_TAIL = BOM_UTF8 + b"<SchemaFile/>"


def _template(*datasets):
    nodes = "".join(
        f"<dataSet><name>{name}</name><query>{query}</query></dataSet>"
        for name, query in datasets
    )
    xml = BOM_UTF8 + (
        f'<SchemaFile><dataCompositionSchema xmlns="{_NAMESPACE}">'
        f"{nodes}</dataCompositionSchema></SchemaFile>"
    ).encode()
    return pack("<IIQQ", 0, 1, len(xml), len(_SETTINGS)) + xml + _SETTINGS + _TAIL


def test_replace_skd_query_cli_updates_length_and_only_selected_xml(tmp_path):
    source = tmp_path / "Template.bin"
    both = _template(("First", "SELECT 10"), ("Second", "SELECT 10"))
    source.write_bytes(both)
    query_file = tmp_path / "query.txt"
    query_file.write_text("SELECT 'А & Б < В > Г'", encoding="utf-8")
    output = tmp_path / "edited.bin"
    args = [
        "replace-skd-query", str(source), str(output), "--dataset", "Second", "--query-file", str(query_file),
    ]
    assert main(args) == 0
    output_bytes = output.read_bytes()
    size = unpack_from("<IIQQ", output_bytes)[2]
    assert output_bytes[24 + size:] == _SETTINGS + _TAIL
    assert output_bytes == _template(("First", "SELECT 10"), ("Second", "SELECT 'А &amp; Б &lt; В &gt; Г'"))
    assert source.read_bytes() == both
    assert main(args) == 5
    assert output.read_bytes() == output_bytes


@pytest.mark.parametrize("names", [("Other",), ("Target", "Target")])
def test_missing_or_ambiguous_dataset_leaves_no_output(tmp_path, names):
    source = tmp_path / "Template.bin"
    source.write_bytes(_template(*((name, "SELECT 10") for name in names)))
    output = tmp_path / "edited.bin"
    with pytest.raises(ValueError, match="one query"):
        replace_skd_query(source, output, dataset="Target", query="SELECT 20")
    assert not output.exists()


def test_unknown_binary_layout_leaves_no_output(tmp_path):
    source = tmp_path / "Template.bin"
    source.write_bytes(_template(("Target", "SELECT 10"))[:-len(_TAIL)])
    output = tmp_path / "edited.bin"
    with pytest.raises(ValueError, match="unsupported SKD template layout"):
        replace_skd_query(source, output, dataset="Target", query="SELECT 20")
    assert not output.exists()
