from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT_ROOT / "examples" / "unindexed_forms_report.py"
EXPECTED_COUNTS = {
    "no_legacy_json": 1,
    "no_tabular_no_widgets": 1,
    "tabular_field_empty_attr_map": 1,
    "tabular_field_bsl_source_mismatch": 1,
    "tabular_field_programmatic_no_defs": 1,
    "tabular_field_platform_dynamic": 1,
}
EXPECTED_JSON_COUNTS = {
    "no_legacy_json": 1,
    "no_owner_object": 0,
    "no_tabular_no_widgets": 1,
    "tabular_field_bsl_source_mismatch": 1,
    "tabular_field_empty_attr_map": 1,
    "tabular_field_no_uuid_hits": 0,
    "tabular_field_platform_dynamic": 1,
    "tabular_field_programmatic_no_defs": 1,
    "unknown": 0,
}


def _run_example(*args: str) -> str:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE), *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return completed.stdout


def _reason_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in EXPECTED_COUNTS:
        match = re.search(
            rf"(?m)^\s*{re.escape(reason)}\s+(\d+)\s*$", output
        )
        assert match is not None, f"reason is absent from report: {reason}"
        counts[reason] = int(match.group(1))
    return counts


def _json_payload(output: str) -> tuple[dict[str, Any], str]:
    stripped = output.lstrip()
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert isinstance(payload, dict)
    return payload, stripped[end:].strip()


def test_synthetic_reason_distribution_is_complete_and_deterministic() -> None:
    first = _reason_counts(_run_example())
    second = _reason_counts(_run_example())
    assert first == second == EXPECTED_COUNTS
    assert sum(first.values()) == 6


def test_json_runs_are_deterministic_and_include_zero_counts() -> None:
    first, _ = _json_payload(_run_example("--json", "--runs", "2"))
    second, _ = _json_payload(_run_example("--json", "--runs", "2"))
    assert first["unindexed_reason"] == EXPECTED_JSON_COUNTS
    assert second == first
    assert first["cohort"]["forms_total"] == sum(EXPECTED_COUNTS.values())
    assert first["aggregate_signature"] == second["aggregate_signature"]
    serialized = json.dumps(first, ensure_ascii=False)
    assert '"/' not in serialized
    assert re.search(r'"[A-Za-z]:\\', serialized) is None


def test_example_uses_public_package_marker() -> None:
    source = EXAMPLE.read_text(encoding="utf-8")
    assert "PLATFORM_DYNAMIC_SOURCE_MARKER" in source
    assert "UUID_SKD_SOURCE" not in source
    assert "_PLATFORM_DYNAMIC_SOURCE_NAMES" not in source
