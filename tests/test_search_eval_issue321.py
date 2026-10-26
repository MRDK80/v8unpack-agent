import json
import re
from pathlib import Path

DATA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/research/search_eval_issue321.json")
    .read_text(encoding="utf-8")
)
MODULE_KINDS = {
    "command", "common_module", "external_connection", "form", "managed_application",
    "manager", "object", "ordinary_application", "record_set", "service", "session",
    "value_manager",
}
CLASSES = {"exact", "free", "ambiguous", "negative", "mixed"}
UNRESOLVED_TYPES = {"Sequences", "ChartOfAccounts", "ChartOfCalculationTypes", "AccountingRegister"}
IDS = [a["id"] for a in DATA["artifacts"]]
FRAGMENTS = {f"{a['id']}#{p['name']}" for a in DATA["artifacts"] for p in a["procedures"]}


def test_marked_synthetic():
    assert DATA["synthetic"] is True


def test_ids_unique_casefold():
    assert len({i.casefold() for i in IDS}) == len(IDS)


def test_all_module_kinds_covered():
    assert {a["module_kind"] for a in DATA["artifacts"]} == MODULE_KINDS


def test_unresolved_types_only_in_negative_queries():
    assert not UNRESOLVED_TYPES & {a["metadata_type"] for a in DATA["artifacts"]}


def test_query_classes_covered():
    assert {q["class"] for q in DATA["queries"]} == CLASSES


def test_gold_references_resolve():
    for q in DATA["queries"]:
        assert set(q["gold"]) <= set(IDS), q["id"]
        assert set(q["gold_fragments"]) <= FRAGMENTS, q["id"]
        for f in q["gold_fragments"]:
            assert f.split("#")[0] in q["gold"], q["id"]


def test_gold_shape_by_class():
    for q in DATA["queries"]:
        if q["class"] == "negative":
            assert q["gold"] == [] and q["expect_status"] == "none"
        elif q["class"] == "ambiguous":
            assert len(q["gold"]) >= 2 and q["expect_status"] == "ambiguous"
        else:
            assert len(q["gold"]) >= 1 and q["expect_status"] == "found"


def test_large_artifacts_have_tail_target():
    big = [a for a in DATA["artifacts"] if a["large"]]
    assert len(big) >= 2
    for a in big:
        assert len(a["procedures"]) >= 10


def test_no_real_data_markers():
    raw = json.dumps(DATA, ensure_ascii=False)
    assert chr(92) not in raw.replace(chr(92) + "n", "")
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw)
    assert "/home/" not in raw and "C:" not in raw


def test_protocol_thresholds_recorded():
    t = DATA["protocol"]["thresholds_free"]
    assert t == {"recall@5": 0.80, "mrr@10": 0.60}
