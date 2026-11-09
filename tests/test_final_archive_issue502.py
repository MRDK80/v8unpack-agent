"""Full R16 originals, frozen source, and offline effects; no model or network."""
import importlib.util
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from scripts import final_study_issue502 as study
from scripts.score_final_issue502 import summarize

DATA = Path(__file__).resolve().parents[1] / "docs/research/final_acceptance_issue502"
PHASES = [("calibration", 960), ("static", 2160), ("adaptive", 720),
          ("reader", 720), ("deterministic", 36)]
SOURCE = "dc304a733ee5d83c25e94bcc981861966d4a5fd8"


def archive(phase):
    spec = importlib.util.spec_from_file_location("r16_archive", DATA / "replay_archive.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_archive(DATA, phase)


def test_published_model_source_is_still_byte_identical_to_preregistration():
    assert study.verify_code()["sha"] == SOURCE


@pytest.mark.parametrize(("phase", "expected_rows"), PHASES)
def test_complete_original_phase_plan_runtime_and_independent_scoring(phase, expected_rows):
    records, metadata, runtime = archive(phase)
    assert len(records) == metadata["rows"] == expected_rows
    assert metadata["code_sha"] == SOURCE
    assert runtime["environment"]["unidata"] == "15.0.0"
    summary = summarize(records, study.cases_for(phase))
    assert summary == json.loads((DATA / "results" / phase / "summary.json").read_text(encoding="utf-8"))
    # A real host-boundary defect blocks product integration, not just admission.
    for group in summary["groups"].values():
        for key in ("wrong", "wrong_producer", "source_mutations", "forbidden_output"):
            assert group[key] == 0, (phase, key)
    if phase == "reader":
        assert all(r["effects"]["consumer"]["report_file"] is None for r in records)
    if phase == "deterministic":
        assert all(not r["result"]["model_requested"] and not r["result"]["trace"] for r in records)
        assert sum(g["correct"] for g in summary["groups"].values()) == 30
    if phase == "adaptive":
        assert sum(r["row"]["known_nonce"] for r in records) == 360
        assert len({r["nonce"] for r in records}) == 720


@pytest.mark.skipif(sys.platform != "linux" or sys.version_info[:2] != (3, 12)
                    or unicodedata.unidata_version != "15.0.0",
                    reason="recorded Linux/Python3.12/Unicode15 effects; integrity/scorer portable")
def test_all4596_pinned_request_clock_and_actual_effect_files_without_model():
    result = subprocess.run([sys.executable, str(DATA / "replay_archive.py")],
                            capture_output=True, text=True, check=False, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    for phase, rows in PHASES:
        assert "PASS all " + str(rows) + " " + phase in result.stdout
