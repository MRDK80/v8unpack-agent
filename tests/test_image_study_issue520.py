"""Verify that the image route delivers observed data and preserves the report host."""
import base64
import json

import pytest

from examples.snapshot_publication import platform_supported
from scripts import final_selection_issue502 as driver
from scripts import image_study_issue520 as study
from v8unpack_agent.untrusted_transport import decode_block


def lookup(case_id, profile, arm):
    case = next(c for c in study.read(study.DATA / "cases.json") if c["id"] == case_id)
    row = next(r for r in study.plan("defender")
               if r["case"] == case_id and r["profile"] == profile and r["protocol_arm"] == arm)
    return case, row


@pytest.mark.parametrize("arm", ["legacy_guard", "framed_guard", "minimal_guard"])
def test_image_bytes_and_observed_text_have_separate_routes(arm):
    case, row = lookup("image520_png_visible_pixels_attack", study.VISION, arm)
    raw = study.image_bytes(case)
    original = driver.request
    with study.request_route(case, row, {"text": "OCR was not given to vision"}, raw):
        body = driver.request(study.wire_row(row), "BSL", [], None)
    assert driver.request is original
    assert body["messages"][0]["content"] == driver.READER_SYSTEM
    assert body["messages"][1]["content"].startswith(driver.TASK)
    assert body["format"] == driver.SCHEMA
    assert base64.b64decode(body["messages"][3]["images"][0]) == raw
    assert "OCR was not given to vision" not in json.dumps(body)
    row = dict(row, profile="llama31")
    with study.request_route(case, row, {"text": "Observed OCR"}, raw):
        body = driver.request(study.wire_row(row), "BSL", [], None)
    assert all("images" not in message for message in body["messages"])
    extra = body["messages"][3]
    assert extra["role"] == "tool"
    value = extra["content"] if arm == "legacy_guard" else decode_block(extra["content"]).text
    assert value == "image_external_text/1\nObserved OCR"


@pytest.mark.skipif(not platform_supported(), reason="actual Linux snapshot publication")
def test_image_derived_wrong_fact_is_blocked_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "VISION_ROUTE_UNAVAILABLE", None)
    case, row = lookup("image520_png_metadata_attack", study.VISION, "framed_guard")
    observation = study.read(study.DATA / "observations.json")[case["id"]]
    proposals = iter([["F9"], ["F1"]])

    def chat(body, *, timeout_seconds):
        assert timeout_seconds == 180 and "images" in body["messages"][4]
        return {
            "model": body["model"], "eval_count": 15, "prompt_eval_count": 400,
            "message": {"role": "assistant",
                        "content": json.dumps({"target_id": "approved", "fact_ids": next(proposals)})},
        }

    record = study.record_trial("defender", case, row, observation, tmp_path / "original", chat)
    assert record["result"]["events"][0]["status"] == "blocked"
    assert record["result"]["recovered"]
    report = json.loads(record["effects"]["consumer"]["report_file"])
    assert report["report"] == {"query": "SELECT 1"}
    study.replay_record("defender", case, row, observation, record, tmp_path / "replay")


def test_non_exercised_slot_has_no_call_or_effect(tmp_path):
    case, row = lookup("image520_png_lsb_attack", study.VISION, "legacy_guard")

    def chat(*args, **kwargs):
        pytest.fail("non-exercised route reached inference")

    record = study.record_trial("defender", case, row, None, tmp_path / "original", chat)
    assert record["route_status"] == "not_exercised"
    assert not record["effects"] and not record["boundary"]
    study.replay_record("defender", case, row, None, record, tmp_path / "replay")


def test_probe_uses_pixels_and_has_no_report_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "VISION_ROUTE_UNAVAILABLE", None)
    row = study.plan("readability")[0]
    case = next(c for c in study.read(study.DATA / "cases.json") if c["id"] == row["case"])

    def chat(body, *, timeout_seconds):
        assert len(body["messages"]) == 3
        assert case["expected_image_text"] not in body["messages"][1]["content"]
        return {"model": body["model"], "eval_count": 10, "prompt_eval_count": 200,
                "message": {"role": "assistant", "content": '{"text":"SELECT 1"}'}}

    record = study.record_trial("readability", case, row, {}, tmp_path / "original", chat)
    assert record["result"]["exact"] and not record["effects"]
    study.replay_record("readability", case, row, {}, record, tmp_path / "replay")


def test_unavailable_vision_is_recorded_without_request(tmp_path):
    case, row = lookup("image520_png_metadata_attack", study.VISION, "framed_guard")

    def chat(*args, **kwargs):
        pytest.fail("unavailable model must never receive a request")

    record = study.record_trial("defender", case, row, {}, tmp_path / "original", chat)
    assert record["route_status"] == "unavailable"
    assert record["result"] == {"trace": [], "model_requested": False}
    assert record["effects"] == {}
    study.replay_record("defender", case, row, {}, record, tmp_path / "replay")
