"""Контракт документации issue #248: layout сериализованного отчёта."""

from __future__ import annotations

import json
import re
from pathlib import Path

from v8unpack_agent.run_report import (
    PostRunReport,
    RunSummary,
    write_post_run_report,
)

DOCS_FILE = Path(__file__).resolve().parents[1] / "docs" / "run_report.md"
SECTION_HEADING = "### Пример сериализованного файла"
TOP_LEVEL_KEYS = {
    "fatal_error",
    "objects",
    "run",
    "schema_version",
    "summary",
}


def _docs_json_block() -> str:
    text = DOCS_FILE.read_text(encoding="utf-8")
    start = text.find(SECTION_HEADING)
    assert start != -1, "раздел с примером сериализованного файла не найден"
    match = re.search(r"```json\n(.*?)```", text[start:], re.DOTALL)
    assert match is not None, "fenced json-блок не найден"
    return match.group(1)


def _docs_payload() -> dict[str, object]:
    payload = json.loads(_docs_json_block())
    assert isinstance(payload, dict)
    return payload


def _synthetic_report() -> PostRunReport:
    return PostRunReport(
        schema_version=1,
        completed=True,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        summary=RunSummary(
            found=0,
            complete=0,
            partial=0,
            failed=0,
            excluded=0,
        ),
        objects=(),
    )


def test_docs_json_block_is_parseable() -> None:
    assert _docs_payload()["schema_version"] == 1


def test_docs_top_level_keys_match_contract() -> None:
    assert set(_docs_payload()) == TOP_LEVEL_KEYS


def test_run_metadata_is_nested() -> None:
    payload = _docs_payload()
    run = payload["run"]
    assert isinstance(run, dict)
    assert run["completed"] is True
    assert "completed" not in payload
    assert "started_at" not in payload
    assert "finished_at" not in payload


def test_summary_invariants_hold() -> None:
    summary = _docs_payload()["summary"]
    assert isinstance(summary, dict)
    assert summary["found"] == (
        summary["complete"] + summary["partial"] + summary["failed"]
    )
    assert summary["discovered"] == summary["found"] + summary["excluded"]


def test_docs_example_matches_model_serialization() -> None:
    assert _docs_payload() == _synthetic_report().to_dict()


def test_docs_example_matches_writer_output(tmp_path: Path) -> None:
    target = tmp_path / "post-run.json"
    write_post_run_report(_synthetic_report(), target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == _docs_payload()


def test_docs_example_is_impersonal() -> None:
    block = _docs_json_block()
    assert "\\" not in block
    assert ":/" not in block
    for line in block.splitlines():
        assert not line.strip().startswith("/")
