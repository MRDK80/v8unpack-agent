"""Preregistered stage-4 budgets and authority invariants, no model or network."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/research/unicode_image_protocol_issue516/protocol.json"


def test_finite_text_and_probe_matrix_counts():
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    scale = len(p["profiles"]) * len(p["tasks"]) * len(p["arms"]) * len(p["seeds"])
    u = p["unicode"]
    assert u["cases_per_task"] == u["clean"] + u["attack_variants"] + u["unsupported"]
    assert len(set(u["attack_kinds"])) == u["attack_variants"]
    assert len(set(u["clean_kinds"])) == u["clean"]
    assert u["planned_trials"] == scale * u["cases_per_task"]
    assert u["unsupported_trials"] == scale * u["unsupported"]
    assert u["max_model_trials"] == u["planned_trials"] - u["unsupported_trials"]
    assert u["max_rpc"] == u["max_model_trials"] * p["generation"]["max_rpc_per_trial"]
    probe = p["decoding_probe"]
    assert probe["max_rpc"] == probe["variants"] * len(probe["hint_modes"]) * len(p["profiles"]) * len(p["seeds"])


def test_image_slots_do_not_grant_unavailable_route_or_text_only_vision():
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    i = p["images"]
    count = 1
    for key in ("formats", "channels", "tasks", "polarities", "profile_slots"):
        count *= len(i[key])
    assert i["planned_slots"] == count * len(p["seeds"]) * len(p["arms"])
    assert i["max_rpc"] == i["planned_slots"] * p["generation"]["max_rpc_per_trial"]
    assert i["profile_slots"].count("vision") == 1
    assert i["missing_route_status"] == "not_exercised"
    assert i["lsb_requires_decoder"]
    assert i["route_mask_sealed_after_issue"] == 518
    assert i["vision_digest"] == "must_seal_before_generation"


def test_old_no_go_and_absent_effects_cannot_become_production_authority():
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert p["status"] == "protocol_not_results"
    assert not any(p["authority"][k] for k in ("raw_writer", "shell", "network", "production_admission"))
    assert not p["thresholds"]["grants_production_default"]
    assert p["thresholds"]["missing_comparator"] == "no_go"
    assert p["thresholds"]["wrong_effects"] == 0
    assert not p["sealing"]["after_response_defense_changes"]
    assert not p["sealing"]["old_archives_mutable"]
    assert p["sealing"]["fix_requires_fresh_series"]
    assert not p["ci"]["models"] and not p["ci"]["network"]
    assert p["ci"]["offline_effect_replay"] and p["ci"]["separate_post_merge"]
    assert p["quota_restriction"] == "cancelled_by_owner_2026-11-10"
    assert p["release"] == "paused"
