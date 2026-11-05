"""Диагностика символов Cf и файлового BOM (#458, решение 4 ADR #457).

Ожидаемые значения заданы явно и одинаковы для всех версий Python в CI
(3.10 и 3.12): так проверяется совпадение символов, позиций, групп и
количеств. ``unidata_version`` сверяется только с самим интерпретатором.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from test_output_characterization_issue456 import FORM_DIR, MODULE_DIR, _make_export

from v8unpack_agent.untrusted_diagnostics import (
    FILE_BOM_BYTES,
    GROUPS,
    CfOccurrence,
    cf_group,
    detect_untrusted_chars,
)


def _occ(code: str, line: int, column: int, group: str) -> dict[str, object]:
    return {"codepoint": code, "line": line, "column": column, "group": group}


def test_fixture_456_form_module(tmp_path: Path) -> None:
    root = _make_export(tmp_path)
    raw = root.joinpath(*FORM_DIR, "ItemForm.obj.bsl").read_bytes()
    text = raw.decode("utf-8")
    diag = detect_untrusted_chars(text, raw=raw)
    assert diag.to_dict() == {
        "file_bom": True,
        "cf": [
            _occ("U+200B", 4, 13, "zero_width"),
            _occ("U+202E", 4, 15, "bidi"),
            _occ("U+00AD", 4, 17, "soft_hyphen"),
        ],
        "counts": {"bidi": 1, "zero_width": 1, "soft_hyphen": 1, "other_cf": 0},
        "total": 3,
        "unidata_version": unicodedata.unidata_version,
    }
    assert text == raw.decode("utf-8")


def test_fixture_456_common_module(tmp_path: Path) -> None:
    root = _make_export(tmp_path)
    raw = root.joinpath(*MODULE_DIR, "CommonModule.obj.bsl").read_bytes()
    diag = detect_untrusted_chars(raw.decode("utf-8"), raw=raw)
    assert diag.file_bom is True
    assert [o.to_dict() for o in diag.occurrences] == [_occ("U+200D", 4, 12, "zero_width")]
    assert diag.total == 1


def test_groups_table() -> None:
    cases = {
        "\u061c": "bidi",
        "\u200e": "bidi",
        "\u200f": "bidi",
        "\u202a": "bidi",
        "\u202e": "bidi",
        "\u2066": "bidi",
        "\u2069": "bidi",
        "\u200b": "zero_width",
        "\u200c": "zero_width",
        "\u200d": "zero_width",
        "\u2060": "zero_width",
        "\ufeff": "zero_width",
        "\u00ad": "soft_hyphen",
        "\u0600": "other_cf",
        "\u2061": "other_cf",
    }
    for char, group in cases.items():
        assert unicodedata.category(char) == "Cf"
        assert cf_group(char) == group
    assert GROUPS == ("bidi", "zero_width", "soft_hyphen", "other_cf")


def test_positions_lines_columns_and_cr() -> None:
    text = "a\u200bb\r\nc\n\u2061x\u202a"
    diag = detect_untrusted_chars(text)
    assert diag.file_bom is None
    assert diag.occurrences == (
        CfOccurrence("U+200B", 1, 2, "zero_width"),
        CfOccurrence("U+2061", 3, 1, "other_cf"),
        CfOccurrence("U+202A", 3, 3, "bidi"),
    )
    assert diag.counts == {"bidi": 1, "zero_width": 1, "soft_hyphen": 0, "other_cf": 1}


def test_leading_feff_without_file_bom_is_cf() -> None:
    text = "\ufeffx"
    no_raw = detect_untrusted_chars(text)
    assert [o.codepoint for o in no_raw.occurrences] == ["U+FEFF"]
    plain = detect_untrusted_chars(text, raw=text.encode("utf-8")[3:])
    assert plain.file_bom is False
    assert [o.codepoint for o in plain.occurrences] == ["U+FEFF"]


def test_file_bom_not_counted_twice_but_inner_feff_is() -> None:
    text = "\ufeffa\ufeff"
    raw = FILE_BOM_BYTES + "a\ufeff".encode()
    diag = detect_untrusted_chars(text, raw=raw)
    assert diag.file_bom is True
    assert diag.occurrences == (CfOccurrence("U+FEFF", 1, 3, "zero_width"),)


def test_bom_already_stripped_from_text() -> None:
    raw = FILE_BOM_BYTES + b"abc"
    diag = detect_untrusted_chars("abc", raw=raw)
    assert diag.file_bom is True
    assert diag.total == 0


def test_empty_and_plain() -> None:
    assert detect_untrusted_chars("").to_dict()["total"] == 0
    assert detect_untrusted_chars("Процедура\n\tКонец\n", raw=b"x").file_bom is False


def test_type_errors() -> None:
    with pytest.raises(TypeError):
        detect_untrusted_chars(b"x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        detect_untrusted_chars("x", raw="x")  # type: ignore[arg-type]
