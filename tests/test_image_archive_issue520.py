"""Validate the complete image-study archive and actual request payloads."""
import gzip
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import pytest

from scripts import image_study_issue520 as study

DATA = Path(__file__).resolve().parents[1] / "docs/research/image_study_issue520"


def archive_helper():
    spec = importlib.util.spec_from_file_location("image_archive520", DATA / "replay_archive.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("phase", "count"), [("defender", 720), ("readability", 20)])
def test_archive_retains_every_planned_row_and_unavailable_route(phase, count):
    records, runtime, _ = archive_helper().verify_archive(DATA, phase)
    assert len(records) == count
    assert len(runtime["profiles"]) == 5
    for record in records:
        row = record["protocol_row"]
        study.validate_boundary(record)
        if row["profile"] == study.VISION:
            assert record["route_status"] in {"unavailable", "not_exercised", "unsupported"}
            assert record["result"] == {"trace": [], "model_requested": False}
            assert record["effects"] == {}
        for step in record["result"]["trace"]:
            assert all("images" not in message for message in step["request"]["messages"])
    assert study.score(phase, records) == study.read(DATA / "results" / phase / "summary.json")


@pytest.mark.skipif(sys.platform != "linux" or sys.version_info[:2] != (3, 12)
                    or unicodedata.unidata_version != "15.0.0",
                    reason="recorded Linux snapshot host and Unicode runtime required")
@pytest.mark.parametrize(("phase", "count"), [("defender", 720), ("readability", 20)])
def test_pinned_replay_with_original_model_responses(phase, count):
    assert archive_helper().replay(DATA, phase)["rows"] == count



def test_initial_load_failure_is_preserved_without_model_response():
    revision = study.read(DATA / "route-revision.json")
    raw = gzip.decompress((DATA / "initial-load-failure.jsonl.gz").read_bytes())
    assert study.sha(raw) == revision["initial_record_sha256"]
    record, = map(json.loads, raw.splitlines())
    assert record["result"]["stop_reason"] == "rpc_error"
    step, = record["result"]["trace"]
    assert "error" in step and "response_json" not in step
    assert not record["effects"].get("consumer", {}).get("report_file")
    study.validate_boundary(record)
