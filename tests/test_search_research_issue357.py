"""Mechanical offline tests for the #357 runner candidate; no model quality claims."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "measure_search_issue357.py"
SPEC = importlib.util.spec_from_file_location("research357_candidate", RUNNER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("runner_unavailable")
r = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r
SPEC.loader.exec_module(r)


def fixture():
    artifacts = []
    for name, text in (("A", "контрагент налоговый номер"), ("B", "склад товар остатки")):
        artifacts.append({"id": "metadata_object:Catalog:" + name + ":object", "owner_kind": "metadata_object", "metadata_type": "Catalog", "owner_name": name, "module_kind": "object", "form_name": None, "synonym": name, "large": False, "procedures": [{"name": "ПриЗаписи", "text": text}]})
    a, b = [v["id"] for v in artifacts]
    queries = [
        {"id": "f", "class": "free", "text": "контрагент налоговый номер", "gold": [a], "gold_fragments": [a + "#ПриЗаписи"], "expect_status": "found"},
        {"id": "m", "class": "mixed", "text": "склад остатки", "gold": [b], "gold_fragments": [], "expect_status": "found"},
        {"id": "e", "class": "exact", "text": "A", "gold": [a], "gold_fragments": [], "expect_status": "found"},
        {"id": "a", "class": "ambiguous", "text": "same", "gold": [a, b], "gold_fragments": [], "expect_status": "ambiguous"},
        {"id": "n", "class": "negative", "text": "missing", "gold": [], "gold_fragments": [], "expect_status": "none"},
    ]
    return [{"synthetic": True, "schema": "search_eval/1", "protocol": {"thresholds_free": {"recall@5": .8, "mrr@10": .6}, "top_k": {"mrr": 10, "ndcg": 10, "recall": 5}}, "artifacts": artifacts, "queries": queries}, {"synthetic": True, "schema": "search_eval/1", "extends": "docs/research/search_eval_issue321.json", "protocol": {"whole_doc_max_chars": 2000}, "artifacts": [], "queries": []}]


class MechanicalTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.artifacts, self.queries = r.validate_sets(self.data)

    def test_validation_and_nonmutation(self):
        before = copy.deepcopy(self.data)
        r.validate_sets(self.data)
        r.baseline_run(self.artifacts, self.queries)
        self.assertEqual(before, self.data)

    def test_validation_error_branches(self):
        mutations = [
            lambda d: d[0].update(synthetic=False),
            lambda d: d[1].update(extends="wrong"),
            lambda d: d[0]["protocol"].update(thresholds_free={}),
            lambda d: d[0]["protocol"].update(top_k={}),
            lambda d: d[1]["protocol"].update(whole_doc_max_chars=1),
            lambda d: d[0].update(artifacts=[]),
            lambda d: d[0]["artifacts"].append(copy.deepcopy(d[0]["artifacts"][0])),
            lambda d: d[0]["artifacts"][0].pop("synonym"),
            lambda d: d[0]["artifacts"][0].update(large="yes"),
            lambda d: d[0]["artifacts"][0]["procedures"][0].update(name="bad name"),
            lambda d: d[0]["artifacts"][0]["procedures"][0].update(text=" "),
            lambda d: d[0]["artifacts"][0]["procedures"].append(copy.deepcopy(d[0]["artifacts"][0]["procedures"][0])),
            lambda d: d[0]["queries"].append(copy.deepcopy(d[0]["queries"][0])),
            lambda d: d[0]["queries"][0].update(text=""),
            lambda d: d[0]["queries"][0].update(gold=None),
            lambda d: d[0]["queries"][0].update(gold=["missing"]),
            lambda d: d[0]["queries"][0].update(gold_fragments=[d[0]["artifacts"][1]["id"] + "#ПриЗаписи"]),
            lambda d: d[0]["queries"][0]["gold"].append(d[0]["queries"][0]["gold"][0]),
            lambda d: d[0]["queries"][-1].update(expect_status="found"),
            lambda d: d[0]["queries"][-2].update(gold=[d[0]["artifacts"][0]["id"]]),
            lambda d: d[0]["queries"][0].update(expect_status="none"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                data = copy.deepcopy(self.data)
                mutate(data)
                with self.assertRaises(r.ResearchError):
                    r.validate_sets(data)

    def test_representation_and_truncation(self):
        self.artifacts[0]["procedures"][0]["text"] = "x" * 3000
        sizes = {name: len(r.documents(self.artifacts, name)) for name in r.REPRESENTATIONS}
        self.assertEqual(sizes, {"whole": 2, "fragments": 2, "card_plus_fragments": 4})
        docs = r.documents(self.artifacts, "whole")
        self.assertTrue(r.document_metadata(docs)["character_truncations"])
        self.assertTrue(all(len(d.text) <= 2000 for d in docs))
        with self.assertRaises(r.ResearchError):
            r.documents(self.artifacts, "unknown")
        self.artifacts.append(self.artifacts[0])
        with self.assertRaises(r.ResearchError):
            r.documents(self.artifacts, "whole")

    def test_identifiers_and_unicode(self):
        self.assertEqual(r.canonical_id("configuration:-:Configuration:session"), "configuration:::session")
        self.assertEqual(r.split_names("HTTPЗапрос РеализацияТоваров"), "HTTP Запрос Реализация Товаров")
        self.assertEqual(r.identity("Е\u0308Ж"), r.identity("Ёж"))
        self.assertEqual(r.split_names("a\r\nb\rc"), "a\nb\nc")

    def test_bm25_empty_and_repeated_query_terms(self):
        with self.assertRaises(r.ResearchError):
            r.BM25([])
        engine = r.BM25([r.Document("a", "a", "!!!", 3)])
        self.assertEqual(engine.scores("word"), [0.0])
        engine = r.BM25(r.documents(self.artifacts, "whole"))
        self.assertEqual(engine.scores("контрагент"), engine.scores("контрагент контрагент"))
        self.assertGreater(engine.scores("контрагент")[0], engine.scores("контрагент")[1])

    def test_aggregation_ties_and_validation(self):
        docs = [r.Document("b", "b#z", "x", 1), r.Document("a", "a#z", "x", 1), r.Document("a", "a#a", "x", 1)]
        rows = r.rank(docs, [1.0, 1.0, 1.0])
        self.assertEqual([v["artifact_id"] for v in rows], ["a", "b"])
        self.assertEqual(rows[0]["document_id"], "a#a")
        for scores in ([1.0], [1.0, math.nan, 1.0]):
            with self.assertRaises(r.ResearchError):
                r.rank(docs, scores)

    def test_metric_math(self):
        rows = [{"artifact_id": x} for x in ("x", "a", "b")]
        values = r.metrics(rows, ["a", "b"])
        self.assertEqual(values["hit@1"], 0.0)
        self.assertEqual(values["recall@5"], 1.0)
        self.assertEqual(values["mrr@10"], .5)
        expected = (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(values["ndcg@10"], expected)
        self.assertEqual(r.metrics([], ["a"])["mrr@10"], 0.0)
        self.assertEqual(r.metrics([{"artifact_id": "a"}], ["a", "b"])["recall@5"], .5)
        with self.assertRaises(r.ResearchError):
            r.metrics(rows, [])
        with self.assertRaises(r.ResearchError):
            r.metrics(rows + [rows[0]], ["a"])

    def test_groups_and_timing(self):
        result = r.baseline_run(self.artifacts, self.queries)
        for value in result["representations"].values():
            self.assertEqual(value["groups"]["free"]["n"], 1)
            self.assertEqual(value["groups"]["free+mixed"]["n"], 2)
            self.assertIsNone(value["groups"]["forms"]["metrics"])
            self.assertIsNone(value["negative_refusal_threshold"])
            self.assertEqual(value["query_timing"]["n"], 15)
        self.assertEqual(r.latency([1.0, 2.0, 3.0])["p95_seconds"], 3.0)
        with self.assertRaises(r.ResearchError):
            r.latency([])

    def test_exact_contract_is_not_runtime(self):
        rows = r.exact_contract_analysis(self.artifacts, self.queries)
        self.assertTrue(all(not row["runtime_executed"] for row in rows))
        self.assertTrue(next(row for row in rows if row["id"] == "e")["label_matches_contract"])

    def test_atomic_and_finally(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            out = directory / "result.json"
            with redirect_stdout(io.StringIO()):
                r.atomic_json(out, {"END": "test"})
            self.assertEqual(json.loads(out.read_text())["END"], "test")
            with self.assertRaises(r.ResearchError):
                r.atomic_json(out, {})
            with patch.object(r.os, "link", side_effect=OSError("private")), self.assertRaises(OSError):
                r.atomic_json(directory / "failure.json", {})
            self.assertFalse(list(directory.glob(".research357-*")))
            with self.assertRaises(r.ResearchError):
                r.atomic_json(directory / "missing" / "out.json", {})

    def test_actual_loader_without_substitute_validator(self):
        with tempfile.TemporaryDirectory() as name:
            raw = [json.dumps(d, ensure_ascii=False).encode() for d in self.data]
            blobs = {str(i): r.git_blob(value) for i, value in enumerate(raw)}
            responses = [subprocess.CompletedProcess([], 0, value, b"") for value in raw]
            with patch.object(r, "DATA_BLOBS", blobs), patch.object(r.subprocess, "run", side_effect=responses):
                artifacts, queries, hashes = r.load_data(Path(name))
            self.assertEqual(len(artifacts), 2)
            self.assertEqual(len(queries), 5)
            self.assertEqual(len(hashes), 2)
            with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"private")), self.assertRaises(r.ResearchError):
                r.load_data(Path(name))
            with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"{}", b"")), self.assertRaises(r.ResearchError):
                r.load_data(Path(name))
        with self.assertRaises(r.ResearchError):
            r.load_data(Path(name) / "missing")

    def test_model_metadata_guard_without_loading_weights(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            spec = r.MODEL_SPECS[0]
            snap = directory / ("models--" + spec["name"].replace("/", "--")) / "snapshots" / spec["revision"]
            with patch.dict(os.environ, {"HF_HUB_CACHE": str(directory)}):
                with self.assertRaises(r.ResearchError):
                    r.model_snapshot(spec)
                snap.mkdir(parents=True)
                with self.assertRaises(r.ResearchError):
                    r.model_snapshot(spec)
                (snap / "1_Pooling").mkdir()
                (snap / "sentence_bert_config.json").write_text('{"max_seq_length":512}')
                (snap / "1_Pooling/config.json").write_text('{"word_embedding_dimension":384,"pooling_mode_mean_tokens":true}')
                hashes = {path.relative_to(snap).as_posix(): r.digest(path.read_bytes()) for path in snap.rglob("*") if path.is_file()}
                with patch.object(r, "MODEL_FILE_HASHES", {spec["revision"]: hashes}):
                    self.assertEqual(r.model_snapshot(spec), snap)
                    with self.assertRaises(r.ResearchError):
                        r.model_snapshot(dict(spec, max_tokens=128))
                    with self.assertRaises(r.ResearchError):
                        r.model_snapshot(dict(spec, pooling="cls"))
        with self.assertRaises(r.ResearchError):
            r.model_worker(Path("."), -1)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only /proc and process-group watchdog")
    def test_watchdog_error_and_cleanup(self):
        proc = Mock()
        proc.pid = 123456
        proc.poll.return_value = None
        with patch.object(r.subprocess, "Popen", return_value=proc), patch.object(r.time, "monotonic", side_effect=[0.0, 0.0, 901.0]), patch.object(r.os, "killpg") as kill:
            with self.assertRaisesRegex(r.ResearchError, "model_wall_time_limit"):
                r.run_models(Path("."))
            kill.assert_called_once()
            proc.wait.assert_called_once()

    def test_main_no_model_import_and_error_paths(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            out = directory / "result.json"
            with redirect_stdout(io.StringIO()), patch.object(r, "load_data", return_value=(self.artifacts, self.queries, {})):
                rc = r.main(["--repo", name, "--out", str(out), "--baseline-only"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.read_text())["status"], "baseline_only")
            self.assertNotIn("sentence_transformers", sys.modules)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(r.main(["--repo", name, "--out", str(out), "--baseline-only"]), 1)
                self.assertEqual(r.main(["--repo", name, "--out", str(directory / "model.json"), "--run-models"]), 1)
            capture = io.StringIO()
            with redirect_stdout(capture), patch.object(r, "load_data", side_effect=OSError("private path")):
                self.assertEqual(r.main(["--repo", name, "--out", str(directory / "error.json"), "--baseline-only"]), 1)
            self.assertNotIn("private path", capture.getvalue())
            self.assertEqual(json.loads((directory / "error.json").read_text())["reason"], "OSError")


    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only /proc and process-group watchdog")
    def test_watchdog_rss_limit(self):
        proc = Mock()
        proc.pid = 123456
        proc.poll.return_value = None
        with patch.object(r.subprocess, "Popen", return_value=proc), patch.object(r.time, "monotonic", return_value=0.0), patch.object(r.Path, "read_text", return_value="VmRSS: 12000000 kB\n"), patch.object(r.os, "killpg") as kill:
            with self.assertRaisesRegex(r.ResearchError, "model_rss_limit"):
                r.run_models(Path("."))
            kill.assert_called_once()
            proc.wait.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only /proc and process-group watchdog")
    def test_watchdog_worker_failure_and_success(self):
        failed = Mock()
        failed.poll.return_value = 1
        failed.returncode = 1
        with patch.object(r.subprocess, "Popen", return_value=failed), self.assertRaisesRegex(r.ResearchError, "model_worker_failed"):
            r.run_models(Path("."))
        def child(args, **kwargs):
            path = Path(args[args.index("--out") + 1])
            path.write_text(json.dumps({"status": "worker_ok", "END": "research357_worker", "result": {"mechanical_fixture": True}}))
            child_proc = Mock()
            child_proc.poll.return_value = 0
            child_proc.returncode = 0
            return child_proc
        with patch.object(r.subprocess, "Popen", side_effect=child):
            results = r.run_models(Path("."))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(value["mechanical_fixture"] for value in results))

    def test_guarded_worker_without_model_execution(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name) / "worker.json"
            with redirect_stdout(io.StringIO()), patch.object(r, "model_worker", return_value={"mechanical_fixture": True}) as worker:
                rc = r.main(["--repo", name, "--out", str(out), "--worker", "0", "--protocol", r.PROTOCOL_SHA256])
            self.assertEqual(rc, 0)
            worker.assert_called_once()
            self.assertEqual(json.loads(out.read_text())["status"], "worker_ok")


    def test_saved_research_artifacts_integrity_and_privacy(self):
        root = Path(__file__).resolve().parents[1]
        results_path = root / "docs" / "research" / "search_embedding_issue357_results.json"
        report_path = root / "docs" / "research" / "search_embedding_issue357.md"
        raw = results_path.read_bytes()
        self.assertEqual(r.digest(raw), "e56e3add2b869e5697baaa2a8c88c216fea3f07e98dd409c6ae9087bcf9899cb")
        payload = json.loads(raw)
        self.assertEqual(payload["schema"], r.SCHEMA)
        self.assertEqual(payload["status"], "model_evaluation_complete")
        self.assertEqual(payload["END"], "research357")
        self.assertEqual(len(payload["models"]), 3)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("BAAI/bge-m3", report)
        self.assertIn("5617a9f61b028005a4858fdac845db406aefb181", report)
        for text in (raw.decode("utf-8"), report):
            for marker in ("/" + "home/", "/" + "Users/", "C:" + chr(92)):
                self.assertNotIn(marker, text)


if __name__ == "__main__":
    print(Path(__file__).name + " v3", flush=True)
    unittest.main()
