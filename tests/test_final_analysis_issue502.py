"""Descriptive accounting checks use synthetic failure, not new model evidence."""
import json
from pathlib import Path

from docs.research.final_acceptance_issue502.analyze_results import costs, counts
from scripts.final_study_issue502 import empty_attack_record


def test_failed_attacker_keeps_cost_and_denominator_without_inventing_source(tmp_path):
    data = Path(__file__).resolve().parents[1] / "docs/research/final_acceptance_issue502"
    family = json.loads((data / "families.json").read_text(encoding="utf-8"))[0]
    row = {"phase": "adaptive", "profile": "qwen25", "task": family["task"],
           "case": family["id"], "variant": "nonce_probe", "seed": 17,
           "known_nonce": False, "attempt": 1, "nonce_commitment": None}
    record = empty_attack_record(row, tmp_path / "trial", None, "attacker_invalid_json")
    record["attacker"] = {"requested": True, "rpc": {"elapsed": .1, "error": "synthetic_provider_failure"}}
    value = counts([record], {family["id"]: family["seed_case"]})
    assert value["n"] == value["attack_n"] == value["no_report"] == value["source_not_constructed"] == 1
    assert value["gold_supported"] == value["admitted_supported"] == value["correct"] == 0
    assert value["attacker_http_requests"] == 1 and value["defender_http_requests"] == 0
    value = costs([record])["qwen25/attacker"]
    assert value["rpc"] == value["missing_eval_count_responses"] == value["missing_prompt_eval_count_responses"] == 1
    assert value["recorded_rpc_seconds"] == .1
