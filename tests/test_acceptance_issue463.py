"""Итоговая приёмка эпика #455 на фикстуре #456 (#463).

Матрица ADR #457: поверхность × режим × класс содержимого. Классы:
P — обычный текст, C — символы Cf, B — файловый BOM, M — строка вида
служебного маркера, T — усечение, N — имена и пути. Прежний режим
сравнивается с эталонами #456 побайтно, без нормализации; новый —
по свойствам клеток и декодированию каждого блока. Отдельно проверяются
коды завершения в обоих режимах.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pytest
import test_output_characterization_issue456 as t456
from test_cli_json_issue258 import _fatal_outcome

from v8unpack_agent import cli, runner, search_cli
from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.module_projection import to_llm_module_fragment
from v8unpack_agent.modules import ModuleEntry
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_api import collect_search_corpus, search_with_context
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.untrusted_transport import BLOCK_BEGIN, BLOCK_END, decode_block

GOLDEN = Path(__file__).parent / "golden_issue456"
CF = ("\u200b", "\u202e", "\u00ad", "\u200d")
MARKERS = ("// SOURCE TEXT", "## BSL", "MODULE")


def _golden(name: str) -> bytes:
    return (GOLDEN / name).read_bytes()


def _blocks(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] | None = None
    for row in text.split("\n"):
        if row.startswith(BLOCK_BEGIN):
            current = [row]
        elif current is not None:
            current.append(row)
            if row.startswith(BLOCK_END):
                out.append("\n".join(current) + "\n")
                current = None
    return out


def _assert_framed_cells(text: str, canonical: dict[str, str]) -> None:
    """Клетки P, C, M, N нового режима для текстового вывода."""
    decoded = {decode_block(b).kind: decode_block(b) for b in _blocks(text)}
    for kind, value in canonical.items():
        assert decoded[kind].text == value  # P, N
    assert not any(char in text for char in CF)  # C: Cf только экранированными
    for marker in MARKERS:
        if any(marker in value for value in canonical.values()):
            rows = text.split("\n")
            assert any(r.startswith("| ") and marker in r for r in rows)  # M: данные


@pytest.fixture
def export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_utc_now", lambda: t456.FIXED_NOW)
    t456._make_export(tmp_path)
    return Path(t456.EXPORT)


def _module_entry() -> ModuleEntry:
    return ModuleEntry(
        module_kind="common_module",
        owner_kind="common_module",
        metadata_type="CommonModule",
        owner_name="SharedHelpers",
        relative_path=t456.MODULE_PATH,
        read_status="ok",
    )


def test_prompt_fragment_matrix(export: Path) -> None:
    context = build_form_context(scan_forms(export).forms[0], export)
    assert to_llm_prompt_fragment(context).encode("utf-8") == _golden("prompt_fragment_full.txt")
    assert to_llm_prompt_fragment(context, 200).encode("utf-8") == _golden(
        "prompt_fragment_max200.txt"
    )
    framed = to_llm_prompt_fragment(context, representation="framed")
    assert context.bsl_text is not None
    _assert_framed_cells(
        framed,
        {"name": "Catalog/SampleObject/ItemForm/Variant1", "bsl": context.bsl_text},
    )
    assert (
        "<<<UNTRUSTED_DIAG n=3 file_bom=1 cf=3 bidi=1 zero_width=1 soft_hyphen=1 "
        "other_cf=0 positions=U+200B@4:13:zero_width,U+202E@4:15:bidi,U+00AD@4:17:soft_hyphen>>>"
    ) in framed  # B, C
    cut = to_llm_prompt_fragment(context, len(framed) - 40, representation="framed")
    bsl = decode_block(_blocks(cut)[-1])  # T
    assert bsl.truncated and bsl.omitted == (bsl.last_line + 1, 5)
    assert context.bsl_text.startswith(bsl.text)


def test_module_fragment_matrix(export: Path) -> None:
    raw = (export / t456.MODULE_PATH).read_bytes()
    text = raw.decode("utf-8")
    legacy = to_llm_module_fragment(_module_entry(), text)
    assert legacy.text.encode("utf-8") == _golden("module_fragment_full.txt")
    assert to_llm_module_fragment(_module_entry(), text, max_chars=200).text.encode(
        "utf-8"
    ) == _golden("module_fragment_max200.txt")
    framed = to_llm_module_fragment(
        _module_entry(), text, representation="framed", bsl_bytes=raw
    )
    _assert_framed_cells(
        framed.text,
        {"name": "SharedHelpers", "path": t456.MODULE_PATH, "bsl": text},
    )
    assert framed.diagnostics is not None and framed.diagnostics["file_bom"] is True  # B
    cut = to_llm_module_fragment(
        _module_entry(), text, representation="framed", bsl_bytes=raw,
        max_chars=len(framed.text) - 40,
    )
    body = decode_block(_blocks(cut.text)[-1])  # T
    assert cut.truncated and body.truncated and text.startswith(body.text)
    assert body.omitted == (body.last_line + 1, 5)
    assert framed.diagnostics["cf"] == [
        {"codepoint": "U+200D", "line": 4, "column": 12, "group": "zero_width"}
    ]


def test_search_context_matrix(export: Path) -> None:
    fragmentation = fragment_corpus(collect_search_corpus(export))
    legacy = search_with_context(fragmentation, "SharedHelpers", max_chars=-1)
    assert legacy.to_json(include_text=True).encode("utf-8") == _golden(
        "search_context_text.json"
    )
    framed = search_with_context(
        fragmentation, "SharedHelpers", max_chars=-1, representation="framed"
    )
    data = framed.to_dict(include_text=True)
    assert data["schema"] == "search_context/2"
    _assert_framed_cells(framed.text, {"text": legacy.included[0].text})
    diagnostics = data["included"][0]["diagnostics"]
    assert "file_bom" not in diagnostics  # B: BOM снят #324
    assert diagnostics["unidata_version"] == unicodedata.unidata_version
    assert diagnostics["cf"] == [
        {"codepoint": "U+200D", "line": 4, "column": 12, "group": "zero_width"}
    ]
    assert diagnostics["counts"] == {"bidi": 0, "zero_width": 1, "soft_hyphen": 0, "other_cf": 0}
    assert diagnostics["total"] == 1
    assert data["included"][0]["hit"]["artifact"]["source_path"] == t456.MODULE_PATH  # N
    budget = len(framed.text) - 40
    cut = search_with_context(
        fragmentation, "SharedHelpers", max_chars=budget, representation="framed"
    )
    assert cut.status == "truncated" and len(cut.text) <= budget
    (body,) = (decode_block(block) for block in _blocks(cut.text))
    span = cut.included[0].included_span
    assert span is not None and body.truncated
    assert (body.first_line, body.last_line) == (span.start_line, span.end_line)
    assert body.omitted == (span.end_line + 1, 5)
    assert body.text == "".join(legacy.included[0].text.splitlines(keepends=True)[:span.end_line])


def _run(capsys: pytest.CaptureFixture[str], main, argv: list[str]) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    code = main(argv)
    return code, capsys.readouterr().out


def test_cli_surfaces_matrix(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, cli.main, [str(export), "--report-path", "a.json"])
    assert f"exit={code}\n--stdout--\n{out}--stderr--\n".encode() == _golden("agent_run_text.txt")
    assert Path("a.json").read_bytes() == _golden("run_report_schema2.json")
    code_f, out_f = _run(
        capsys, cli.main, [str(export), "--report-path", "b.json", "--representation", "framed"]
    )
    assert code_f == code
    assert Path("b.json").read_bytes() == _golden("run_report_schema2.json")
    assert out_f.splitlines()[0] == out.splitlines()[0]
    build = ["build", str(export), "--snapshot", "s.json", "--embedder", "hashed-bow-demo"]
    assert _run(capsys, search_cli.main, build)[0] == 0
    query = ["query", str(export), "--snapshot", "s.json", "--embedder", "hashed-bow-demo",
             "--query", "SharedHelpers", "--include-text"]
    legacy_code, legacy_out = _run(capsys, search_cli.main, query)
    framed_code, framed_out = _run(capsys, search_cli.main, query + ["--representation", "framed"])
    assert legacy_code == framed_code == 0
    assert json.loads(legacy_out)["context"]["schema"] == "search_context/1"
    context = json.loads(framed_out)["context"]
    assert context["schema"] == "search_context/2"
    (body,) = (decode_block(block) for block in _blocks(context["text"]))
    assert body.text == json.loads(legacy_out)["context"]["included"][0]["text"]
    assert "text" not in context["included"][0]
    assert context["included"][0]["diagnostics"]["total"] == 1
    budget = len(context["text"]) - 40
    cut_code, cut_out = _run(
        capsys, search_cli.main,
        query + ["--representation", "framed", "--max-chars", str(budget)],
    )
    cut = json.loads(cut_out)["context"]
    assert cut_code == 0 and cut["status"] == "truncated"
    (prefix,) = (decode_block(block) for block in _blocks(cut["text"]))
    span = cut["included"][0]["included_span"]
    assert prefix.truncated and prefix.omitted == (span["end_line"] + 1, body.last_line)
    assert body.text.startswith(prefix.text)


@pytest.mark.parametrize("representation", ["legacy", "framed"])
def test_exit_codes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], representation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_utc_now", lambda: t456.FIXED_NOW)
    flag = ["--representation", representation]
    missing = _run(capsys, cli.main, ["absent", "--report-path", "r.json", *flag])[0]
    assert missing == cli.EXIT_BAD_INPUT
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run(capsys, cli.main, [str(empty), "--report-path", "e.json", *flag])[0] == cli.EXIT_OK
    t456._make_export(tmp_path)
    degraded = _run(capsys, cli.main, [t456.EXPORT, "--report-path", "d.json", *flag])[0]
    assert degraded == cli.EXIT_DEGRADED
    assert _run(capsys, search_cli.main, ["query", "absent", "--query", "x", *flag])[0] == 2
    query = ["query", t456.EXPORT, "--query", "SharedHelpers", *flag]
    assert _run(capsys, search_cli.main, query + ["--max-chars", "0"])[0] == 3

    def fail_fragmentation(*_args: object) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(search_cli, "_fragmentation", fail_fragmentation)
    assert _run(capsys, search_cli.main, query)[0] == 4


@pytest.mark.parametrize("representation", ["legacy", "framed"])
def test_agent_error_exit_codes(
    export: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], representation: str,
) -> None:
    flag = ["--representation", representation]
    base = [str(export), "--json", *flag]
    unwritable = ["--report-path", "absent/report.json"]
    assert _run(capsys, cli.main, base + unwritable)[0] == cli.EXIT_WRITE_ERROR == 5
    monkeypatch.setattr(cli, "run_pipeline", lambda _options: _fatal_outcome())
    assert _run(capsys, cli.main, base + ["--report-path", "report.json"])[0] == cli.EXIT_FATAL == 4
    assert _run(capsys, cli.main, base + unwritable)[0] == cli.EXIT_FATAL_AND_WRITE_ERROR == 6


def test_describe_cli_agreed_change(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, cli.main, ["--describe-cli"])
    assert f"exit={code}\n--stdout--\n{out}--stderr--\n".encode() == _golden("describe_cli.txt")
    assert json.loads(out)["schema_version"] == "1.1"


def test_unidata_version_reported_separately() -> None:
    assert isinstance(unicodedata.unidata_version, str)
    assert sys.version_info >= (3, 10)
