import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs/research"
BASE = json.loads((DOCS / "search_eval_issue321.json").read_text(encoding="utf-8"))
HARD = json.loads((DOCS / "search_eval_issue321_hard.json").read_text(encoding="utf-8"))
ARTIFACTS = BASE["artifacts"] + HARD["artifacts"]
IDS = [a["id"] for a in ARTIFACTS]
FRAGMENTS = {f"{a['id']}#{p['name']}": p for a in ARTIFACTS for p in a["procedures"]}
LIMIT = HARD["protocol"]["whole_doc_max_chars"]
CLASSES = {"exact", "free", "ambiguous", "negative", "mixed"}


def split_camel(text):
    return re.sub(r"(?<=[a-z\u0430-\u044f])(?=[A-Z\u0410-\u042f])", " ", text)


def stems(text):
    words = re.findall(r"\w+", split_camel(text).casefold())
    return {w[:5] for w in words if len(w) > 3}


def test_marked_synthetic_and_extends_base():
    assert HARD["synthetic"] is True
    assert HARD["extends"] == "docs/research/search_eval_issue321.json"


def test_ids_unique_casefold_across_sets():
    assert len({i.casefold() for i in IDS}) == len(IDS)


def test_query_ids_unique_and_classes_known():
    ids = [q["id"] for q in BASE["queries"] + HARD["queries"]]
    assert len(set(ids)) == len(ids)
    assert {q["class"] for q in HARD["queries"]} <= CLASSES


def test_gold_references_resolve():
    for q in HARD["queries"]:
        assert set(q["gold"]) <= set(IDS), q["id"]
        assert set(q["gold_fragments"]) <= set(FRAGMENTS), q["id"]
        for f in q["gold_fragments"]:
            assert f.split("#")[0] in q["gold"], q["id"]


def test_gold_shape_by_class():
    for q in HARD["queries"]:
        if q["class"] == "negative":
            assert q["gold"] == [] and q["expect_status"] == "none"
        elif q["class"] == "ambiguous":
            assert len(q["gold"]) >= 2 and q["expect_status"] == "ambiguous"
        else:
            assert len(q["gold"]) >= 1 and q["expect_status"] == "found"


def test_tail_targets_beyond_whole_document_limit():
    tails = [q for q in HARD["queries"] if q.get("tag") == "tail"]
    assert len(tails) >= 2
    for q in tails:
        art = next(a for a in ARTIFACTS if a["id"] == q["gold"][0])
        target = q["gold_fragments"][0].split("#")[1]
        names = [p["name"] for p in art["procedures"]]
        assert names[-1] == target, q["id"]
        before = sum(len(p["text"]) + 1 for p in art["procedures"][:-1])
        assert before > LIMIT, q["id"]


def test_paraphrase_shares_no_stems_with_target_fragment():
    items = [q for q in HARD["queries"] if q.get("tag") == "paraphrase"]
    assert len(items) >= 4
    for q in items:
        frag = FRAGMENTS[q["gold_fragments"][0]]
        target = stems(frag["name"] + " " + frag["text"])
        assert not stems(q["text"]) & target, q["id"]


def test_distractors_present():
    assert len([q for q in HARD["queries"] if q.get("tag") == "distractor"]) >= 2


def test_no_real_data_markers():
    raw = json.dumps(HARD, ensure_ascii=False)
    assert chr(92) not in raw.replace(chr(92) + "n", "")
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw)
    assert "/home/" not in raw and "C:" not in raw
