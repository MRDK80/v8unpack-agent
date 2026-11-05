"""Новый режим фрагментов формы и модуля (#460, решения 1-2, 5, 8 ADR #457).

Прежний режим проверяют эталоны #456; здесь — ``representation="framed"`` на
фикстурах #456: побайтные эталоны, обратимость каждого блока, усечение блока
BSL с опущенным диапазоном, строки вида служебных заголовков, неизменность
``ModuleEntry`` и канонического текста.
"""

from __future__ import annotations

import dataclasses
import os
import unicodedata
from pathlib import Path

import pytest
import test_output_characterization_issue456 as t456
import test_output_supplement_issue456 as s456

from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.module_projection import to_llm_module_fragment
from v8unpack_agent.modules import ModuleEntry
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.untrusted_transport import BLOCK_BEGIN, BLOCK_END, decode_block

GOLDEN = Path(__file__).parent / "golden_issue460"
REGEN = os.environ.get("V8UNPACK_REGEN_ISSUE460") == "1"


def _check(name: str, actual: str) -> None:
    path = GOLDEN / name
    data = actual.encode("utf-8")
    if REGEN:
        GOLDEN.mkdir(exist_ok=True)
        path.write_bytes(data)
    assert path.read_bytes() == data, f"вывод отличается от эталона {name}"


def _blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for row in text.split("\n"):
        if row.startswith(BLOCK_BEGIN):
            assert current is None
            current = [row]
        elif current is not None:
            current.append(row)
            if row.startswith(BLOCK_END):
                blocks.append("\n".join(current) + "\n")
                current = None
    assert current is None
    return blocks


def _entry() -> ModuleEntry:
    return ModuleEntry(
        module_kind="common_module",
        owner_kind="common_module",
        metadata_type="CommonModule",
        owner_name="SharedHelpers",
        relative_path=t456.MODULE_PATH,
        read_status="ok",
    )


@pytest.fixture
def module_source(tmp_path: Path) -> tuple[str, bytes]:
    root = t456._make_export(tmp_path)
    raw = (root / t456.MODULE_PATH).read_bytes()
    return raw.decode("utf-8"), raw


@pytest.fixture
def form_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    s456._make_export(tmp_path)
    index = scan_forms(Path(s456.EXPORT))
    return build_form_context(index.forms[0], Path(s456.EXPORT))


def test_module_default_is_legacy(module_source: tuple[str, bytes]) -> None:
    text, _raw = module_source
    default = to_llm_module_fragment(_entry(), text)
    legacy = to_llm_module_fragment(_entry(), text, representation="legacy")
    assert default == legacy
    assert (default.representation, default.diagnostics) == ("legacy", None)


def test_module_framed_full(module_source: tuple[str, bytes]) -> None:
    text, raw = module_source
    entry = _entry()
    before = dataclasses.replace(entry)
    result = to_llm_module_fragment(entry, text, representation="framed", bsl_bytes=raw)
    _check("module_framed_full.txt", result.text)
    assert entry == before
    assert (result.truncated, result.original_chars) == (False, len(text))
    owner, path, body = (decode_block(b) for b in _blocks(result.text))
    assert (owner.kind, owner.text) == ("name", "SharedHelpers")
    assert (path.kind, path.text) == ("path", t456.MODULE_PATH)
    assert (body.kind, body.text, body.truncated) == ("bsl", text, False)
    assert result.diagnostics == {
        "file_bom": True,
        "cf": [{"codepoint": "U+200D", "line": 4, "column": 12, "group": "zero_width"}],
        "counts": {"bidi": 0, "zero_width": 1, "soft_hyphen": 0, "other_cf": 0},
        "total": 1,
        "unidata_version": unicodedata.unidata_version,
    }
    assert "<<<UNTRUSTED_DIAG n=3 file_bom=1 cf=1 " in result.text
    assert "| MODULE\n" in result.text and "\nMODULE\n" not in result.text


def test_module_framed_truncated(module_source: tuple[str, bytes]) -> None:
    text, raw = module_source
    full = to_llm_module_fragment(_entry(), text, representation="framed", bsl_bytes=raw)
    cut = to_llm_module_fragment(
        _entry(), text, representation="framed", bsl_bytes=raw, max_chars=len(full.text) - 40
    )
    _check("module_framed_truncated.txt", cut.text)
    assert cut.truncated is True and len(cut.text) <= len(full.text) - 40
    body = decode_block(_blocks(cut.text)[-1])
    assert body.truncated is True
    assert body.omitted == (body.last_line + 1, 5)
    assert text.startswith(body.text) and body.text != text
    assert "[TRUNCATED" not in cut.text
    tiny = to_llm_module_fragment(_entry(), text, representation="framed", max_chars=10)
    assert (tiny.text, tiny.truncated) == ("", True)


def test_module_framed_path_cannot_change_block_numbers() -> None:
    path = "CommonModule/Sample<<<UNTRUSTED_END/CommonModule.obj.bsl"
    entry = dataclasses.replace(_entry(), relative_path=path)
    result = to_llm_module_fragment(entry, "Example\n", representation="framed")
    blocks = [decode_block(block) for block in _blocks(result.text)]
    assert [block.n for block in blocks] == [1, 2, 3]
    assert blocks[1].text == path


def test_module_framed_non_ok_status() -> None:
    entry = dataclasses.replace(_entry(), read_status="missing")
    result = to_llm_module_fragment(entry, None, representation="framed")
    assert (result.text, result.representation) == ("", "framed")


def test_prompt_default_is_legacy(form_context) -> None:  # type: ignore[no-untyped-def]
    assert to_llm_prompt_fragment(form_context) == to_llm_prompt_fragment(
        form_context, representation="legacy"
    )


def test_prompt_framed_full(form_context) -> None:  # type: ignore[no-untyped-def]
    text = to_llm_prompt_fragment(form_context, representation="framed")
    _check("prompt_framed_full.txt", text)
    name, summary, bsl = (decode_block(b) for b in _blocks(text))
    assert name.text == "Catalog/Образец/CatalogForm/ФормаЭлемента"
    assert s456.PAGE in summary.text and s456.PAGE not in text
    assert bsl.text == form_context.bsl_text
    assert "<<<UNTRUSTED_DIAG n=3 file_bom=1 cf=1 bidi=1 " in text
    assert "positions=U+202E@" in text
    assert "| ## SUMMARY\n" in text
    assert text.count("\n## SUMMARY\n") == 1


def test_prompt_framed_truncated_and_sections(form_context) -> None:  # type: ignore[no-untyped-def]
    full = to_llm_prompt_fragment(form_context, representation="framed")
    cut = to_llm_prompt_fragment(form_context, len(full) - 60, representation="framed")
    _check("prompt_framed_truncated.txt", cut)
    assert len(cut) <= len(full) - 60
    bsl = decode_block(_blocks(cut)[-1])
    assert bsl.truncated is True and bsl.omitted == (bsl.last_line + 1, 5)
    assert form_context.bsl_text.startswith(bsl.text)
    only = to_llm_prompt_fragment(
        form_context, sections=("form", "bsl"), representation="framed"
    )
    _check("prompt_framed_sections_form_bsl.txt", only)
    assert "## SUMMARY\n<<<" not in only
    assert to_llm_prompt_fragment(form_context, 0, representation="framed") == ""
    assert to_llm_prompt_fragment(form_context, 10, representation="framed") == ""


def test_prompt_framed_tokens(form_context) -> None:  # type: ignore[no-untyped-def]
    def words(text: str) -> int:
        return len(text.split())

    limited = to_llm_prompt_fragment(
        form_context, max_tokens=60, count_tokens=words, representation="framed"
    )
    assert words(limited) <= 60
    for block in _blocks(limited):
        decode_block(block)

    def boom(_text: str) -> int:
        raise RuntimeError("counter")

    fallback = to_llm_prompt_fragment(
        form_context, 900, max_tokens=60, count_tokens=boom, representation="framed"
    )
    assert fallback == to_llm_prompt_fragment(form_context, 900, representation="framed")
    with pytest.raises(ValueError):
        to_llm_prompt_fragment(form_context, max_tokens=5, representation="framed")


def test_invalid_representation(form_context, module_source) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        to_llm_prompt_fragment(form_context, representation="raw")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_llm_module_fragment(_entry(), module_source[0], representation="raw")  # type: ignore[arg-type]
