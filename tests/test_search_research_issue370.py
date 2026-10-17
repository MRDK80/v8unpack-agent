"""Offline mechanics and failure handling for research task #370; no model weights."""
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_search_issue370.py"
spec = importlib.util.spec_from_file_location("research370_candidate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def test_protocol_constants():
    assert runner.SHA == "7124fda57aa1f1b5fbc34fda56082f711c273754"
    assert runner.THRESHOLDS == {"recall@5": .8, "mrr@10": .6}
    assert runner.PROTOCOL_COMMENT == 6234319876


def test_metrics_rank_ten_and_multiple_gold():
    hits = [{"artifact_id": "a" + str(i)} for i in range(10)]
    value = runner.score_query(hits, ["a9"], str.casefold)
    assert value == {"hit@1": 0., "recall@5": 0., "mrr@10": .1, "ndcg@10": 1 / math.log2(11)}
    value = runner.score_query(hits, ["a0", "a9"], str.casefold)
    assert value["recall@5"] == .5
    assert value["mrr@10"] == 1
    assert len(hits) == 10


def test_metrics_empty_and_invalid():
    assert runner.score_query([], ["a"], str.casefold)["recall@5"] == 0
    with pytest.raises(runner.EvalError, match="semantic_metrics_require_gold"):
        runner.score_query([], [], str.casefold)
    with pytest.raises(runner.EvalError, match="duplicate_ranked_artifact"):
        runner.score_query([{"artifact_id": "A"}, {"artifact_id": "a"}], ["a"], str.casefold)


def test_timing_preserves_sample_order():
    assert runner.timing([3., 1., 2.])["seconds"] == [3., 1., 2.]
    assert runner.timing([3., 1., 2.])["median_seconds"] == 2.
    assert runner.timing([3., 1., 2.])["p95_seconds"] == 3.
    with pytest.raises(runner.EvalError, match="missing_timing_samples"):
        runner.timing([])


def fragment(aid, did):
    return SimpleNamespace(document=SimpleNamespace(
        artifact=SimpleNamespace(artifact_id=aid), document_id=did))


def test_bm25_rank_dedup_tie_and_nonfinite():
    frags = [fragment("a", "z"), fragment("a", "b"), fragment("b", "a")]
    ranked = runner.rank_bm25(frags, [1., 1., 1.], str.casefold)
    assert [r["document_id"] for r in ranked] == ["a", "b"]
    with pytest.raises(runner.EvalError, match="invalid_bm25_scores"):
        runner.rank_bm25(frags, [float("nan")] * 3, str.casefold)


def test_public_exception_is_sanitized():
    def broken(*args, **kwargs):
        raise ValueError("PRIVATE_SENTINEL")
    api = SimpleNamespace(search_with_context=broken)
    response, seconds = runner.public_attempt(api, None, {"text": "q"})
    assert response["search_status"] == "error"
    assert response["error"] == {"code": "query_exception"}
    assert seconds >= 0 and "PRIVATE_SENTINEL" not in json.dumps(response)


def test_failed_query_retained_in_means_and_top10():
    artifact = {"id": "a", "form_name": None, "large": False, "module_kind": "object", "owner_kind": "metadata_object"}
    queries = [{"id": "q1", "class": "free", "text": "first", "gold": ["a"], "gold_fragments": []},
               {"id": "q2", "class": "free", "text": "second", "gold": ["a"], "gold_fragments": []}]
    hits = [{"artifact_id": x, "document_id": x, "score": 1., "method": "semantic"}
            for x in ["a"] + ["b" + str(i) for i in range(9)]]
    responses = {"q1": {"search_status": "semantic", "context_status": "built", "dispatch_path": ["semantic"], "error": None, "hits": hits},
                 "q2": runner.failed_response("query_exception")}
    report = runner.summarize_method("issue321", queries, [artifact], responses,
                                    SimpleNamespace(fragments=[]), str.casefold, None)
    assert report["groups"]["class:free"]["n"] == 2
    assert report["groups"]["class:free"]["metrics"]["recall@5"] == .5
    assert len(report["per_query"][0]["top10"]) == 10
    assert report["per_query"][1]["error"]["code"] == "query_exception"


def test_journal_and_plan():
    journal = runner.QueryJournal()
    response = runner.failed_response("bad")
    journal.add("c", "smoke", 0, "q", response, .1)
    assert len(journal.rows) == len(journal.failures) == 1
    plan = runner.planned_attempts({"c": [{"id": "q", "class": "free"}, {"id": "n", "class": "negative"}]}, "model")
    assert len(plan) == 13
    assert ["c", "exact", 0, "n"] in plan


def test_atomic_result_and_existing(tmp_path):
    path = tmp_path / "result.json"
    runner.save_json(path, {"END": "test"})
    before = path.read_bytes()
    with pytest.raises(runner.EvalError, match="output_exists"):
        runner.save_json(path, {"END": "changed"})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".research370-*"))


def test_atomic_race_preserves_competitor(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    def racing_link(src, dst):
        Path(dst).write_bytes(b"competitor")
        raise FileExistsError()
    monkeypatch.setattr(runner.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        runner.save_json(path, {"END": "test"})
    assert path.read_bytes() == b"competitor"
    assert not list(tmp_path.glob(".research370-*"))


def test_atomic_write_error_cleanup(tmp_path, monkeypatch):
    def failure(fd):
        raise OSError("synthetic")
    monkeypatch.setattr(runner.os, "fsync", failure)
    with pytest.raises(OSError):
        runner.save_json(tmp_path / "result.json", {"END": "test"})
    assert list(tmp_path.iterdir()) == []


def test_protocol_flag_blocks_model_before_execution(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("execution must not start")
    monkeypatch.setattr(runner, "execute_isolated", forbidden)
    assert runner.main(["--repo", str(tmp_path), "--out", str(tmp_path / "r.json"), "--run-models"]) == 2
    assert not (tmp_path / "r.json").exists()


def test_main_diagnostic_failure(tmp_path, monkeypatch):
    def failure(*args):
        raise RuntimeError("PRIVATE_SENTINEL")
    monkeypatch.setattr(runner, "execute_isolated", failure)
    out = tmp_path / "result.json"
    assert runner.main(["--repo", str(tmp_path), "--out", str(out), "--baseline-only"]) == 1
    report = json.loads(out.read_bytes())
    assert report["error"] == "RuntimeError" and report["rc"] == 1
    assert report["END"] == "search_quality_issue370/1"
    assert "PRIVATE_SENTINEL" not in out.read_text(encoding="utf-8")


def test_monitor_child_error():
    rc, _ = runner.monitor([sys.executable, "-c", "raise SystemExit(7)"], os.environ.copy(), wall_limit=10.)
    assert rc == 7


def test_monitor_timeout_cleanup(tmp_path):
    marker = tmp_path / "late"
    code = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_bytes(b'late')"
    with pytest.raises(runner.EvalError, match="worker_wall_time_limit"):
        runner.monitor([sys.executable, "-c", code], os.environ.copy(), wall_limit=.1)
    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.skipif(not Path("/proc/self/status").is_file(), reason="RSS watchdog is Linux-only")
def test_monitor_rss_cleanup(tmp_path):
    marker = tmp_path / "late"
    code = "import time,pathlib;x=bytearray(16*1024*1024);time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").write_bytes(b'late')"
    with pytest.raises(runner.EvalError, match="worker_rss_limit"):
        runner.monitor([sys.executable, "-c", code], os.environ.copy(), wall_limit=10., rss_limit=1)
    time.sleep(1.1)
    assert not marker.exists()


def test_source_guard(tmp_path, monkeypatch):
    p = tmp_path / "x.py"
    p.write_bytes(b"x = 1\n")
    monkeypatch.setattr(runner, "SOURCE_HASHES", {"x.py": hashlib.sha256(p.read_bytes()).hexdigest()})
    runner.verify_source_tree(tmp_path)
    p.write_bytes(b"x = 2\n")
    with pytest.raises(runner.EvalError, match="runtime_source_mismatch"):
        runner.verify_source_tree(tmp_path)


def test_real_dataset_adapter_and_public_smoke(tmp_path):
    # This test uses real project constructors, not a replacement model or validator.
    from v8unpack_agent.modules import ModuleEntry, ModuleIndex
    from v8unpack_agent.search_api import (
        HashedBagOfWordsDemoEmbedder,
        build_search_snapshot,
        search_with_context,
    )
    from v8unpack_agent.search_contract import (
        ArtifactRef,
        OwnerLink,
        OwnerRef,
        SearchDocument,
        SearchDocumentSet,
        identity_key,
    )
    from v8unpack_agent.search_corpus import CorpusReport, SearchCorpus
    from v8unpack_agent.search_fragments import (
        FragmentParameters,
        fragment_corpus,
        verify_fragmentation,
    )
    from v8unpack_agent.search_index import load_index
    api = SimpleNamespace(ModuleEntry=ModuleEntry, ModuleIndex=ModuleIndex, HashedBagOfWordsDemoEmbedder=HashedBagOfWordsDemoEmbedder, build_search_snapshot=build_search_snapshot, search_with_context=search_with_context, ArtifactRef=ArtifactRef, OwnerRef=OwnerRef, OwnerLink=OwnerLink, SearchDocument=SearchDocument, SearchDocumentSet=SearchDocumentSet, identity_key=identity_key, SearchCorpus=SearchCorpus, CorpusReport=CorpusReport, FragmentParameters=FragmentParameters, fragment_corpus=fragment_corpus, verify_fragmentation=verify_fragmentation, load_index=load_index)
    datasets, before = runner.inputs(ROOT)
    old = [datasets["search_eval_issue321.json"], datasets["search_eval_issue321_hard.json"]]
    corpora = [(old[0]["artifacts"] + old[1]["artifacts"], old[0]["queries"] + old[1]["queries"], 90),
               (datasets["search_eval_issue329_paraphrase.json"]["artifacts"], datasets["search_eval_issue329_paraphrase.json"]["queries"], 140)]
    for index, (artifacts, queries, expected_count) in enumerate(corpora):
        corpus, _ = runner.make_corpus(artifacts, api)
        fragments = fragment_corpus(corpus, parameters=FragmentParameters(max_chars=2000, overlap_lines=3))
        verify_fragmentation(fragments, {doc.artifact.artifact_id: doc.text for doc in corpus.documents.documents})
        assert len(fragments.fragments) == expected_count
        journal = runner.QueryJournal()
        result = runner.smoke_corpus(str(index), queries, fragments, tmp_path, api, journal)
        assert result["queries_attempted"] == len(queries)
        assert not journal.failures
    assert runner.inputs(ROOT)[1] == before


def test_result_file_is_original_measurement():
    path = ROOT / "docs" / "research" / "search_quality_issue370_results.json"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == "e8dcdbb07df887e7374fa8f9bf6cd778bf20532b2b2753690aeae3f7e46cfe02"
    data = json.loads(raw)
    assert data["rc"] == data["worker_rc"] == data["worker"]["rc"] == 0
    for corpus in data["worker"]["corpora"].values():
        for method in ("lexical_baseline", "bge_m3"):
            result = corpus[method]
            for row in result["per_query"]:
                assert len(row["top10"]) == 10
                metrics = runner.score_query(row["top10"], row["gold"], str.casefold)
                assert metrics == pytest.approx(row["metrics"])
            timing_key = "query_timing" if method == "bge_m3" else "query_total_timing"
            times = result[timing_key]
            assert len(times["seconds"]) == times["n"] == 3 * corpus["queries"]



def test_baseline_continues_after_query_exception():
    class Engine:
        def __init__(self, docs):
            pass
        def scores(self, text):
            if text == "bad":
                raise RuntimeError("PRIVATE_SENTINEL")
            return [1.]
    historical = SimpleNamespace(BM25=Engine, Document=lambda *args: args)
    artifact = {"id": "a", "form_name": None, "large": False, "module_kind": "object", "owner_kind": "metadata_object"}
    f = fragment("a", "a#p")
    f.search_text = "source"
    f.procedure = None
    f.document.text = "source"
    queries = [{"id": str(i), "class": "free", "text": text, "gold": ["a"], "gold_fragments": []}
               for i, text in enumerate(("good", "bad", "later"))]
    journal = runner.QueryJournal()
    result = runner.evaluate_bm25("issue321", queries, [artifact], SimpleNamespace(fragments=[f]), historical, None, str.casefold, journal)
    assert len(journal.rows) == 9 and len(journal.failures) == 3
    assert len(result["per_query"]) == 3
    assert result["groups"]["class:free"]["metrics"]["recall@5"] == pytest.approx(2/3)
    assert all(r["response"]["error"] is None for r in journal.rows if r["query_id"] == "2")


def test_missing_output_directory(tmp_path, monkeypatch):
    assert runner.main(["--repo", str(tmp_path), "--out", str(tmp_path / "missing" / "r.json"), "--smoke-only"]) == 2


def test_worker_preserves_unexecuted_queries(tmp_path, monkeypatch):
    def fake_perform(root, report, mode, journal):
        report["planned_attempts"] = [["c", "smoke", 0, "q"]]
        raise RuntimeError("PRIVATE_SENTINEL")
    monkeypatch.setattr(runner, "verify_source_tree", lambda root: None)
    monkeypatch.setattr(runner, "perform", fake_perform)
    old_connect = runner.socket.socket.connect
    old_connect_ex = runner.socket.socket.connect_ex
    old_create = runner.socket.create_connection
    old_path = list(sys.path)
    old_bytecode = sys.dont_write_bytecode
    try:
        assert runner.run_worker(tmp_path, tmp_path / "r.json", [], "smoke") == 1
    finally:
        runner.socket.socket.connect = old_connect
        runner.socket.socket.connect_ex = old_connect_ex
        runner.socket.create_connection = old_create
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
    report = json.loads((tmp_path / "r.json").read_bytes())
    assert report["not_run"] == [["c", "smoke", 0, "q"]]
    assert report["error"] == "RuntimeError"
    assert report["quality_measured"] is False


def test_interruption_does_not_publish_partial(tmp_path, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(runner, "execute_isolated", interrupt)
    path = tmp_path / "r.json"
    with pytest.raises(KeyboardInterrupt):
        runner.main(["--repo", str(tmp_path), "--out", str(path), "--smoke-only"])
    assert not path.exists()


def test_report_lists_observed_failures():
    report = (ROOT / "docs" / "research" / "search_quality_issue329.md").read_text(encoding="utf-8")
    for value in ("0.4875", "0.7500", "0.3917", "7/8", "unconfirmed", "#371", "#372"):
        assert value in report
