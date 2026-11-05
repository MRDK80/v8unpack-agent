"""Флаг ``--representation`` в CLI (#461, решения 1, 5, 6, 8 ADR #457).

Без флага вывод совпадает с эталонами #456 (их проверяют тесты #456). Здесь —
режим ``framed`` на фикстуре #456: отчёт не меняется, envelope ``--json``
отличается только значением ``prompt_chars``, ``search_cli query`` выдаёт
``search_context/2`` с обратимыми блоками, коды завершения прежние.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_output_characterization_issue456 as t456

from v8unpack_agent import _cli_catalog, cli, runner, search_cli
from v8unpack_agent.untrusted_transport import BLOCK_BEGIN, BLOCK_END, decode_block

GOLDEN = Path(__file__).parent / "golden_issue456"


@pytest.fixture
def export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_utc_now", lambda: t456.FIXED_NOW)
    t456._make_export(tmp_path)
    return Path(t456.EXPORT)


def _run(capsys: pytest.CaptureFixture[str], main, argv: list[str]) -> tuple[int, str, str]:  # type: ignore[no-untyped-def]
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_agent_run_framed_report_unchanged(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    legacy = _run(capsys, cli.main, [str(export), "--report-path", "legacy.json"])
    framed = _run(
        capsys, cli.main,
        [str(export), "--report-path", "framed.json", "--representation", "framed"],
    )
    assert framed[0] == legacy[0]
    assert Path("framed.json").read_bytes() == Path("legacy.json").read_bytes()
    assert Path("legacy.json").read_bytes() == (GOLDEN / "run_report_schema2.json").read_bytes()
    legacy_lines = legacy[1].splitlines()
    framed_lines = framed[1].splitlines()
    assert [i for i, (a, b) in enumerate(zip(legacy_lines, framed_lines)) if a != b] == [1]
    assert framed_lines[1].startswith("промпт-символов: ")
    assert int(framed_lines[1].split(": ")[1]) > int(legacy_lines[1].split(": ")[1])


def test_agent_run_framed_json_envelope(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code_l, out_l, _ = _run(capsys, cli.main, [str(export), "--report-path", "a.json", "--json"])
    code_f, out_f, _ = _run(
        capsys, cli.main,
        [str(export), "--report-path", "b.json", "--json", "--representation", "framed"],
    )
    assert code_l == code_f
    legacy, framed = json.loads(out_l), json.loads(out_f)
    assert set(legacy) == set(framed)
    legacy["data"]["report"]["file_name"] = framed["data"]["report"]["file_name"]
    prompt = framed["data"].pop("prompt_chars")
    assert prompt > legacy["data"].pop("prompt_chars")
    assert legacy == framed


def test_search_cli_query_framed(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build = ["build", str(export), "--snapshot", "snap.json", "--embedder", "hashed-bow-demo"]
    assert _run(capsys, search_cli.main, build)[0] == 0
    base = ["query", str(export), "--snapshot", "snap.json", "--embedder", "hashed-bow-demo",
            "--query", "SharedHelpers", "--include-text"]
    code_l, out_l, _ = _run(capsys, search_cli.main, base)
    code_f, out_f, _ = _run(capsys, search_cli.main, base + ["--representation", "framed"])
    assert code_l == code_f == 0
    assert out_l.encode("utf-8") == (GOLDEN / "search_cli_query_text.txt").read_bytes().split(
        b"--stdout--\n", 1
    )[1].split(b"--stderr--", 1)[0]
    context = json.loads(out_f)["context"]
    assert context["schema"] == "search_context/2"
    rows = context["text"].split("\n")
    begin = next(i for i, row in enumerate(rows) if row.startswith(BLOCK_BEGIN))
    end = next(i for i, row in enumerate(rows) if row.startswith(BLOCK_END))
    decoded = decode_block("\n".join(rows[begin:end + 1]) + "\n")
    legacy_context = json.loads(out_l)["context"]
    assert decoded.text == legacy_context["included"][0]["text"]
    assert context["included"][0]["diagnostics"]["total"] == 1


def test_invalid_representation_is_usage_error(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_report = _run(capsys, cli.main, [str(export)])[0]
    bad_value = _run(
        capsys, cli.main, [str(export), "--report-path", "r.json", "--representation", "raw"]
    )[0]
    assert bad_value == missing_report
    with pytest.raises(ValueError):
        runner.RunOptions(export_root=export, representation="raw")  # type: ignore[arg-type]


def test_catalog_lists_flag_additively() -> None:
    catalog = _cli_catalog.build_catalog()
    assert catalog["schema_version"] == "1.1"
    by_name = {c["name"]: c for c in catalog["commands"]}
    for name in ("v8unpack-agent-run", "search_cli query"):
        names = [p["name"] for p in by_name[name]["parameters"]]
        assert names.count("--representation") == 1
    assert "--representation" not in [p["name"] for p in by_name["search_cli build"]["parameters"]]
