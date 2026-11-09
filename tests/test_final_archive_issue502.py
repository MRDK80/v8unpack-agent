"""Full R16 originals, frozen source, and offline effects; no model or network."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from scripts import final_study_issue502 as study
from scripts.score_final_issue502 import honest, summarize

DATA = Path(__file__).resolve().parents[1] / "docs/research/final_acceptance_issue502"
PHASES = [("calibration", 960), ("static", 2160), ("adaptive", 720),
          ("reader", 720), ("deterministic", 36)]
SOURCE = "dc304a733ee5d83c25e94bcc981861966d4a5fd8"
RAW_SHA = {
    "calibration": "1da5d467e8bd4cca74975374ff9ab5e46985af32b7338dd300bccd2ed4627ec2",
    "static": "6dc67089f615e398e2d72ce7ea3be796aed4c6b21df480448eced4273368fbfb",
    "adaptive": "b093e60532246009d75f6fa655e4cf913d45775005c42f45831cd1ffeb29e65b",
    "reader": "5e9e2d3639b8438881b5c93417a8e7890813c42d664d1a3acabe66088f656f98",
    "deterministic": "2432ccec377230d56e05afc35d097426d11c33a396730aaed34074c6de648eeb",
}
PHASE_SHA = {
    "calibration": "3486248a9340cc05442f00a86660d588551582406947ad66c621a39df5418f5e",
    "static": "943aecaa29f1190f5e861811fe404f50c454ce63dc1f14889f00ce63a46b1046",
    "adaptive": "929e7d2608a8a399376e2d30ff3fb192174bfa3c4830639e67f7d505004ca514",
    "reader": "760ed50b206899aec744ef2f79ccd45d6fa0f0eb5b311c719c0fd286dfab7465",
    "deterministic": "ad5d6086dcee3fa3b4d5dc73a11ac58ca2d625647ac8933d5f95eaf7db6db8fd",
}


def archive(phase):
    spec = importlib.util.spec_from_file_location("r16_archive", DATA / "replay_archive.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_archive(DATA, phase)


def test_preregistered_source_zip_and_seal_keep_original_bytes():
    # Future package changes must not rewrite a historical experiment. Its own
    # pinned source is the long-lived CI contract; root correspondence is an
    # operator gate during this acceptance, not a permanent freeze of the repo.
    assert hashlib.sha256((DATA / "source.zip").read_bytes()).hexdigest() == (
        "7a522bdb5ae3e7ddcff0d83ec3078b053b447315277b016170fbc97443f255ec")
    assert hashlib.sha256((DATA / "code-seal.json").read_bytes()).hexdigest() == (
        "e31c69a8e4e70598a01c72d8a60c174a512c6b5d2324fddcad923c024a06af1b")
    assert json.loads((DATA / "code-seal.json").read_text(encoding="utf-8"))["sha"] == SOURCE


@pytest.mark.parametrize(("phase", "expected_rows"), PHASES)
def test_complete_original_phase_plan_runtime_and_independent_scoring(phase, expected_rows):
    records, metadata, runtime = archive(phase)
    assert len(records) == metadata["rows"] == expected_rows
    assert metadata["code_sha"] == SOURCE
    assert metadata["raw_sha256"] == RAW_SHA[phase]
    assert metadata["phase_seal_sha256"] == PHASE_SHA[phase]
    assert runtime["environment"]["unidata"] == "15.0.0"
    summary = summarize(records, study.cases_for(phase))
    assert summary == json.loads((DATA / "results" / phase / "summary.json").read_text(encoding="utf-8"))
    # A real host-boundary defect blocks product integration, not just admission.
    for group in summary["groups"].values():
        for key in ("wrong", "wrong_producer", "source_mutations", "forbidden_output"):
            assert group[key] == 0, (phase, key)
    if phase == "reader":
        assert all(r["effects"]["consumer"]["report_file"] is None for r in records)
        lookup = {case["id"]: case for case in study.cases_for(phase)}
        for record in records:
            if record["result"]["reader_selected"]:
                assert honest(json.loads(record["result"]["proposals"][-1]), lookup[record["row"]["case"]])
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
