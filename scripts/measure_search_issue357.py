"""Offline research runner for #357; reconstructed protocol, not runtime acceptance."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "v2"
SCHEMA = "search_research_issue357/1"
SOURCE_SHA = "42e1d27723e88e9048d4d36b0929d127d26328d0"
PROTOCOL_SHA256 = "918f9d11d8a3c68b3e56a8bab0b95d5893ec1f0ac0e693d3ae52842dd67f7a19"
DATA_BLOBS = {
    "docs/research/search_eval_issue321.json": "ea11054274cd0d0f24b9b5d28ada8772297875c2",
    "docs/research/search_eval_issue321_hard.json": "0c99b587554df069d64d90092acc6263bf2dad0c",
}
MODEL_SPECS = (
    {"name": "intfloat/multilingual-e5-small", "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "license": "mit", "dimension": 384, "pooling": "mean", "max_tokens": 512, "query_prefix": "query: ", "document_prefix": "passage: "},
    {"name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42", "license": "apache-2.0", "dimension": 384, "pooling": "mean", "max_tokens": 128, "query_prefix": "", "document_prefix": ""},
    {"name": "BAAI/bge-m3", "revision": "5617a9f61b028005a4858fdac845db406aefb181", "license": "mit", "dimension": 1024, "pooling": "cls", "max_tokens": 8192, "query_prefix": "", "document_prefix": ""},
)
MODEL_FILE_HASHES = {'614241f622f53c4eeff9890bdc4f31cfecc418b3': {'config.json': '69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959', 'sentence_bert_config.json': '948201d8329907aae938fa62f9ceeed53f5694dacc2b87b9f3b78b37ee986529', '1_Pooling/config.json': '987f7a67a38fa564c849bb5d277c52ab9088a84368fc0be31a354125aebb12a0', 'tokenizer_config.json': 'a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b', 'modules.json': 'c6e29747481e8b5dd2b58401966aeac910de39092f90cda9a704b1545f902b04', 'README.md': '0038de97aee16258cecbad7ffda4b4febd6953e747a00e0ddbc8e6ed241e9c1c'}, 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42': {'config.json': '6300193cb75e01cf80c96decef7187dfb33094d97cc1490b7ead6ff134476e4e', 'sentence_bert_config.json': '70f4448f31320443fe3557cacea5abf2dcc4915dda8c80646bec9f3bb0aa5a1f', 'config_sentence_transformers.json': 'b8c64b5cece00d8424b4896ea75b512b6008576088497609dfeb6bd63e6d36b8', '1_Pooling/config.json': '4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23', 'tokenizer_config.json': '5036ea374ffedd706e3bef33e2e0d6953cb868ef8a490e76e32ba0faa37a6b9b', 'modules.json': '8f4b264b80206c830bebbdcae377e137925650a433b689343a63bdc9b3145460', 'README.md': '1e98ea05b0de579fcaad3d625b62ea55647142ed674d5f5ebf1440e4bbbb6f23'}, '5617a9f61b028005a4858fdac845db406aefb181': {'config.json': '26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa', 'sentence_bert_config.json': 'eb9b44b13c0f52a3b3685c3b1cbdea1ba8b04bea123b98f61610048940776eb1', 'config_sentence_transformers.json': '1eef72430e7194a1e59680e635aed81ffa083f05668dbc5bb1c56c04c0999c38', '1_Pooling/config.json': 'e54c164a07274f2eb45bb724f54a79d1efcc90c41573887cd9a29aeee0597352', 'tokenizer_config.json': 'a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4', 'modules.json': '84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf', 'README.md': '0b81ccf9134e5874d620a86e6905062ea999e779c34eb1a7e65eaeb7fe00e450'}}
REPRESENTATIONS = ("whole", "fragments", "card_plus_fragments")
CLASSES = {"exact", "ambiguous", "free", "mixed", "negative"}

class ResearchError(ValueError):
    """Safe reason codes only."""


def fail(reason: str) -> None:
    raise ResearchError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def identity(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def canonical_id(old: str) -> str:
    prefix = "configuration:-:Configuration:"
    return "configuration:::" + old[len(prefix):] if old.startswith(prefix) else old


def split_names(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    result = []
    for i, char in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if i and char.isupper() and (prev.islower() or prev.isdigit() or (prev.isupper() and nxt.islower())):
            result.append(" ")
        result.append(char)
    return "".join(result)


def terms(text: str) -> list[str]:
    return [word[:5] for word in re.findall(r"\w+", split_names(text).casefold()) if len(word) > 3]


def validate_sets(sets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(sets) != 2 or any(d.get("synthetic") is not True or d.get("schema") != "search_eval/1" for d in sets):
        fail("invalid_dataset_schema")
    if sets[1].get("extends") != "docs/research/search_eval_issue321.json":
        fail("invalid_dataset_extension")
    protocol = sets[0].get("protocol", {})
    if protocol.get("thresholds_free") != {"recall@5": 0.80, "mrr@10": 0.60}:
        fail("thresholds_changed")
    if protocol.get("top_k") != {"mrr": 10, "ndcg": 10, "recall": 5}:
        fail("top_k_changed")
    if sets[1].get("protocol", {}).get("whole_doc_max_chars") != 2000:
        fail("character_limit_changed")
    artifacts = [a for d in sets for a in d.get("artifacts", [])]
    queries = [q for d in sets for q in d.get("queries", [])]
    if not artifacts or not queries:
        fail("empty_dataset")
    seen = set()
    fragment_ids = set()
    for artifact in artifacts:
        required = {"id", "owner_kind", "metadata_type", "owner_name", "module_kind", "form_name", "synonym", "large", "procedures"}
        if not required <= artifact.keys() or not isinstance(artifact["id"], str) or not artifact["id"]:
            fail("invalid_artifact")
        key = identity(canonical_id(artifact["id"]))
        if key in seen:
            fail("duplicate_artifact")
        seen.add(key)
        if not isinstance(artifact["large"], bool) or not isinstance(artifact["procedures"], list) or not artifact["procedures"]:
            fail("invalid_procedures")
        names = set()
        for procedure in artifact["procedures"]:
            if not isinstance(procedure, dict) or not isinstance(procedure.get("name"), str) or not procedure["name"].isidentifier():
                fail("invalid_procedure_name")
            if not isinstance(procedure.get("text"), str) or not procedure["text"].strip():
                fail("invalid_procedure_text")
            name_key = identity(procedure["name"])
            if name_key in names:
                fail("duplicate_procedure")
            names.add(name_key)
            fragment_ids.add(artifact["id"] + "#" + procedure["name"])
    ids = {a["id"] for a in artifacts}
    query_ids = set()
    for query in queries:
        if not isinstance(query.get("id"), str) or not query["id"] or query["id"] in query_ids:
            fail("duplicate_or_invalid_query_id")
        query_ids.add(query["id"])
        if query.get("class") not in CLASSES or not isinstance(query.get("text"), str) or not query["text"].strip():
            fail("invalid_query")
        if not isinstance(query.get("gold"), list) or not isinstance(query.get("gold_fragments"), list):
            fail("invalid_gold_shape")
        if not set(query["gold"]) <= ids or not set(query["gold_fragments"]) <= fragment_ids:
            fail("unresolved_gold")
        if any(f.split("#", 1)[0] not in query["gold"] for f in query["gold_fragments"]):
            fail("fragment_outside_gold")
        if len(query["gold"]) != len(set(query["gold"])):
            fail("duplicate_gold")
        cls = query["class"]
        if cls == "negative":
            if query["gold"] or query["gold_fragments"] or query.get("expect_status") != "none":
                fail("invalid_negative")
        elif cls == "ambiguous":
            if len(query["gold"]) < 2 or query.get("expect_status") != "ambiguous":
                fail("invalid_ambiguous")
        elif not query["gold"] or query.get("expect_status") != "found":
            fail("invalid_positive")
    return artifacts, queries


def load_data(repo: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    repo = repo.resolve()
    if not repo.is_dir():
        fail("repository_missing")
    sets = []
    hashes = {}
    for name, expected in DATA_BLOBS.items():
        proc = subprocess.run(["git", "--no-pager", "-C", str(repo), "show", SOURCE_SHA + ":" + name], stdin=subprocess.DEVNULL, capture_output=True, check=False)
        if proc.returncode:
            fail("source_sha_or_dataset_unavailable")
        raw = proc.stdout
        if git_blob(raw) != expected:
            fail("dataset_blob_mismatch")
        hashes[name] = {"blob_sha": expected, "sha256": digest(raw), "bytes": len(raw)}
        sets.append(json.loads(raw))
    artifacts, queries = validate_sets(sets)
    return artifacts, queries, hashes


@dataclass(frozen=True)
class Document:
    artifact_id: str
    document_id: str
    text: str
    original_chars: int


def documents(artifacts: list[dict[str, Any]], representation: str) -> list[Document]:
    if representation not in REPRESENTATIONS:
        fail("unknown_representation")
    result = []
    for artifact in artifacts:
        aid = canonical_id(artifact["id"])
        kind = "FORM" if artifact["form_name"] else "MODULE"
        header = " / ".join([kind] + [str(artifact[k]) if artifact[k] is not None else "-" for k in ("module_kind", "owner_kind", "metadata_type", "owner_name", "synonym", "form_name")])
        candidates = []
        if representation == "whole":
            candidates.append((aid, header + "\n" + "\n".join(p["text"] for p in artifact["procedures"])))
        else:
            for procedure in artifact["procedures"]:
                candidates.append((aid + "#" + procedure["name"], header + " / " + procedure["name"] + "\n" + procedure["text"]))
            if representation == "card_plus_fragments":
                candidates.append((aid + "#@card", header))
        for did, text in candidates:
            prepared = split_names(text)
            result.append(Document(aid, did, prepared[:2000], len(prepared)))
    if len({identity(d.document_id) for d in result}) != len(result):
        fail("duplicate_document")
    return sorted(result, key=lambda d: (identity(d.artifact_id), d.artifact_id, d.document_id))


class BM25:
    """Fixed reconstructed BM25, no fitting against gold labels."""
    def __init__(self, docs: list[Document]) -> None:
        if not docs:
            fail("empty_documents")
        self.counts = [Counter(terms(d.text)) for d in docs]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = statistics.mean(self.lengths)
        self.n = len(docs)
        self.df = Counter(term for counts in self.counts for term in counts)

    def scores(self, query: str) -> list[float]:
        query_terms = set(terms(query))
        scores = []
        for counts, length in zip(self.counts, self.lengths):
            score = 0.0
            for term in sorted(query_terms):
                tf = counts[term]
                if tf and self.average:
                    frequency = self.df[term]
                    idf = math.log(1 + (self.n - frequency + 0.5) / (frequency + 0.5))
                    score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / self.average))
            scores.append(score)
        return scores


def rank(docs: list[Document], scores: list[float]) -> list[dict[str, Any]]:
    if len(scores) != len(docs) or any(not math.isfinite(float(v)) for v in scores):
        fail("invalid_scores")
    best: dict[str, tuple[float, str]] = {}
    for doc, score in zip(docs, scores):
        value = (float(score), doc.document_id)
        previous = best.get(doc.artifact_id)
        if previous is None or value[0] > previous[0] or (value[0] == previous[0] and value[1] < previous[1]):
            best[doc.artifact_id] = value
    ordered = sorted(best, key=lambda aid: (-best[aid][0], identity(aid), aid))
    return [{"artifact_id": aid, "document_id": best[aid][1], "score": best[aid][0]} for aid in ordered[:10]]


def metrics(ranking: list[dict[str, Any]], gold: list[str]) -> dict[str, float]:
    relevant = {canonical_id(x) for x in gold}
    if not relevant:
        fail("semantic_metrics_require_gold")
    ids = [x["artifact_id"] for x in ranking]
    if len(ids) != len(set(ids)):
        fail("duplicate_ranked_artifact")
    relevance = [int(x in relevant) for x in ids[:10]]
    first = next((i + 1 for i, value in enumerate(relevance) if value), None)
    dcg = sum(value / math.log2(i + 2) for i, value in enumerate(relevance))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(10, len(relevant))))
    return {"hit@1": float(bool(relevance and relevance[0])), "recall@5": len(set(ids[:5]) & relevant) / len(relevant), "mrr@10": 1 / first if first else 0.0, "ndcg@10": dcg / ideal}


def latency(samples: list[float]) -> dict[str, Any]:
    if not samples:
        fail("missing_timing_samples")
    ordered = sorted(samples)
    return {"n": len(samples), "seconds": samples, "median_seconds": statistics.median(samples), "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1]}


def summarize(queries: list[dict[str, Any]], rankings: dict[str, list[dict[str, Any]]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = {a["id"]: a for a in artifacts}
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in ("free", "mixed", "free+mixed", "forms", "large", "tag:base", "tag:paraphrase", "tag:tail", "tag:distractor")}
    for kind in sorted({a["module_kind"] for a in artifacts}):
        groups["module_kind:" + kind] = []
    groups["owner_kind:common_command"] = []
    groups["owner_kind:common_form"] = []
    per_query = []
    for q in queries:
        if q["class"] not in ("free", "mixed"):
            continue
        value = metrics(rankings[q["id"]], q["gold"])
        per_query.append({"id": q["id"], "class": q["class"], "tag": q.get("tag", "base"), "gold": [canonical_id(g) for g in q["gold"]], "metrics": value, "top10": rankings[q["id"]]})
        selected = [q["class"], "free+mixed", "tag:" + q.get("tag", "base")]
        gold_artifacts = [lookup[g] for g in q["gold"]]
        if any(a["form_name"] for a in gold_artifacts):
            selected.append("forms")
        if any(a["large"] for a in gold_artifacts):
            selected.append("large")
        selected.extend("module_kind:" + kind for kind in {a["module_kind"] for a in gold_artifacts})
        selected.extend("owner_kind:" + kind for kind in {a["owner_kind"] for a in gold_artifacts} if "owner_kind:" + kind in groups)
        for group in selected:
            groups.setdefault(group, []).append(value)
    aggregated = {name: {"n": len(rows), "metrics": {metric: statistics.mean(row[metric] for row in rows) for metric in ("hit@1", "recall@5", "mrr@10", "ndcg@10")} if rows else None} for name, rows in groups.items()}
    free = aggregated["free"]["metrics"]
    thresholds = {"recall@5": 0.80, "mrr@10": 0.60}
    negative = [{"id": q["id"], "top1_score": rankings[q["id"]][0]["score"]} for q in queries if q["class"] == "negative"]
    return {"groups": aggregated, "free_thresholds": thresholds, "free_thresholds_passed": bool(free and all(free[k] >= v for k, v in thresholds.items())), "per_query": per_query, "negative_top1": negative, "negative_refusal_threshold": None}


def document_metadata(docs: list[Document]) -> dict[str, Any]:
    return {"count": len(docs), "input_sha256": digest(canonical_json([{ "id": d.document_id, "text": d.text} for d in docs]).encode()), "character_truncations": [{"document_id": d.document_id, "original_chars": d.original_chars, "kept_chars": len(d.text)} for d in docs if d.original_chars > len(d.text)]}


def baseline_run(artifacts: list[dict[str, Any]], queries: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"method": "reconstructed_bm25", "k1": 1.5, "b": 0.75, "representations": {}}
    for representation in REPRESENTATIONS:
        docs = documents(artifacts, representation)
        builds = []
        query_times = []
        rankings = {}
        expected_ids = None
        BM25(docs).scores(queries[0]["text"])
        for repeat in range(3):
            start = time.perf_counter()
            engine = BM25(docs)
            builds.append(time.perf_counter() - start)
            current = {}
            for query in queries:
                start = time.perf_counter()
                current[query["id"]] = rank(docs, engine.scores(query["text"]))
                query_times.append(time.perf_counter() - start)
            ids = {qid: [row["artifact_id"] for row in rows] for qid, rows in current.items()}
            if expected_ids is not None and ids != expected_ids:
                fail("baseline_not_reproducible")
            expected_ids = ids
            if repeat == 0:
                rankings = current
        result["representations"][representation] = {"documents": document_metadata(docs), "build_timing": latency(builds), "query_timing": latency(query_times), **summarize(queries, rankings, artifacts)}
    return result


def exact_contract_analysis(artifacts: list[dict[str, Any]], queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for query in queries:
        if query["class"] not in ("exact", "ambiguous", "negative"):
            continue
        key = identity(query["text"])
        by_id = [canonical_id(a["id"]) for a in artifacts if identity(canonical_id(a["id"])) == key]
        by_name = [canonical_id(a["id"]) for a in artifacts if any(isinstance(a[f], str) and identity(a[f]) == key for f in ("owner_name", "form_name"))]
        found = sorted(set(by_id or by_name), key=lambda x: (identity(x), x))
        gold = sorted({canonical_id(g) for g in query["gold"]}, key=lambda x: (identity(x), x))
        rows.append({"id": query["id"], "class": query["class"], "gold": gold, "contract_candidates": found, "label_matches_contract": found == gold, "runtime_executed": False})
    return rows


def atomic_json(out: Path, report: dict[str, Any]) -> None:
    if out.exists() or out.is_symlink():
        fail("output_exists")
    if not out.parent.is_dir():
        fail("output_directory_missing")
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()
    temp = None
    try:
        fd, name = tempfile.mkstemp(prefix=".research357-", dir=out.parent)
        temp = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, out)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print("RESULT " + out.name + " bytes=" + str(len(raw)) + " sha256=" + digest(raw))


def model_snapshot(spec: dict[str, Any]) -> Path:
    home = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    cache = Path(os.environ.get("HF_HUB_CACHE", str(home / "hub")))
    snap = cache / ("models--" + spec["name"].replace("/", "--")) / "snapshots" / spec["revision"]
    if not snap.is_dir():
        fail("model_snapshot_missing")
    for name, expected in MODEL_FILE_HASHES[spec["revision"]].items():
        path = snap / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            fail("model_metadata_changed")
    config = json.loads((snap / "sentence_bert_config.json").read_text(encoding="utf-8"))
    pool = json.loads((snap / "1_Pooling" / "config.json").read_text(encoding="utf-8"))
    if config.get("max_seq_length") != spec["max_tokens"] or pool.get("word_embedding_dimension") != spec["dimension"]:
        fail("model_config_changed")
    for mode in ("mean", "cls", "max", "mean_sqrt_len", "weightedmean", "lasttoken"):
        key = {"cls": "pooling_mode_cls_token", "mean": "pooling_mode_mean_tokens", "max": "pooling_mode_max_tokens", "mean_sqrt_len": "pooling_mode_mean_sqrt_len_tokens", "weightedmean": "pooling_mode_weightedmean_tokens", "lasttoken": "pooling_mode_lasttoken"}[mode]
        if bool(pool.get(key, False)) != (mode == spec["pooling"]):
            fail("model_pooling_changed")
    return snap


def model_worker(repo: Path, model_number: int) -> dict[str, Any]:
    if not 0 <= model_number < len(MODEL_SPECS):
        fail("invalid_model_number")
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["MKL_NUM_THREADS"] = "4"
    import socket
    def deny_network(*args: Any, **kwargs: Any) -> Any:
        raise ResearchError("network_forbidden")
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.manual_seed(0)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    spec = MODEL_SPECS[model_number]
    snap = model_snapshot(spec)
    artifacts, queries, _ = load_data(repo)
    start = time.perf_counter()
    model = SentenceTransformer(str(snap), device="cpu", local_files_only=True, trust_remote_code=False)
    model.float()
    model.eval()
    load_seconds = time.perf_counter() - start
    if model.max_seq_length != spec["max_tokens"] or model.get_sentence_embedding_dimension() != spec["dimension"]:
        fail("loaded_model_config_mismatch")
    snapshot_files = []
    for path in sorted(snap.rglob("*")):
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1048576), b""):
                    h.update(chunk)
            snapshot_files.append({"name": path.relative_to(snap).as_posix(), "bytes": path.stat().st_size, "sha256": h.hexdigest()})
    result = {"method": "local_dense_embedding", "model": spec, "device": "cpu", "dtype": "float32", "batch_size": 4, "threads": 4, "inter_op_threads": 1, "seed": 0, "normalize_embeddings": True, "load_seconds": load_seconds, "parameter_count": sum(p.numel() for p in model.parameters()), "snapshot_files": snapshot_files, "representations": {}}
    def encode(texts: list[str]) -> Any:
        values = model.encode(texts, batch_size=4, normalize_embeddings=True, prompt="", show_progress_bar=False, convert_to_numpy=True)
        if values.shape != (len(texts), spec["dimension"]) or not np.isfinite(values).all():
            fail("invalid_model_vectors")
        if not np.allclose(np.linalg.norm(values, axis=1), 1.0, atol=0.00001):
            fail("non_unit_model_vectors")
        return values
    for representation in REPRESENTATIONS:
        docs = documents(artifacts, representation)
        texts = [spec["document_prefix"] + d.text for d in docs]
        qtexts = [spec["query_prefix"] + q["text"] for q in queries]
        def token_truncations(ids: list[str], values: list[str]) -> list[dict[str, Any]]:
            counts = model.tokenizer(values, truncation=False, padding=False, add_special_tokens=True, return_length=True)["length"]
            return [{"id": did, "tokens_before_truncation": int(count), "max_tokens": spec["max_tokens"]} for did, count in zip(ids, counts) if count > spec["max_tokens"]]
        truncations = {"documents": token_truncations([d.document_id for d in docs], texts), "queries": token_truncations([q["id"] for q in queries], qtexts)}
        encode(texts[:1])
        encode(qtexts[:1])
        builds = []
        query_times = []
        rankings = {}
        previous = None
        for repeat in range(3):
            start = time.perf_counter()
            vectors = encode(texts)
            builds.append(time.perf_counter() - start)
            current = {}
            for query, qtext in zip(queries, qtexts):
                start = time.perf_counter()
                qvector = encode([qtext])[0]
                current[query["id"]] = rank(docs, (vectors @ qvector).tolist())
                query_times.append(time.perf_counter() - start)
            ids = {qid: [v["artifact_id"] for v in rows] for qid, rows in current.items()}
            if previous is not None and ids != previous:
                fail("model_rankings_not_reproducible")
            previous = ids
            if repeat == 0:
                rankings = current
        result["representations"][representation] = {"documents": document_metadata(docs), "token_truncations": truncations, "build_timing": latency(builds), "query_timing": latency(query_times), **summarize(queries, rankings, artifacts)}
    return result


def run_models(repo: Path) -> list[dict[str, Any]]:
    if not Path("/proc/self/status").is_file():
        fail("linux_rss_watchdog_required")
    results = []
    deadline = time.monotonic() + 2700
    with tempfile.TemporaryDirectory(prefix="research357-") as name:
        directory = Path(name)
        for number in range(len(MODEL_SPECS)):
            out = directory / ("model" + str(number) + ".json")
            args = [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo), "--worker", str(number), "--out", str(out), "--protocol", PROTOCOL_SHA256]
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            started = time.monotonic()
            peak = 0
            try:
                while proc.poll() is None:
                    if time.monotonic() - started > 900 or time.monotonic() > deadline:
                        fail("model_wall_time_limit")
                    try:
                        status = Path("/proc") / str(proc.pid) / "status"
                        text = status.read_text(encoding="utf-8")
                    except FileNotFoundError:
                        text = ""
                    values = [int(line.split()[1]) * 1024 for line in text.splitlines() if line.startswith(("VmRSS:", "VmHWM:"))]
                    peak = max([peak] + values)
                    if peak > 10 * 1024 ** 3:
                        fail("model_rss_limit")
                    time.sleep(0.05)
                if proc.returncode or not out.is_file():
                    fail("model_worker_failed")
                data = json.loads(out.read_text(encoding="utf-8"))
                if data.get("status") != "worker_ok" or data.get("END") != "research357_worker":
                    fail("invalid_worker_result")
                results.append(dict(data["result"], observed_peak_rss_bytes=peak))
            finally:
                if proc.poll() is None:
                    import signal
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proc.wait()
    return results


def main(argv: list[str] | None = None) -> int:
    print(Path(__file__).name + " " + VERSION, flush=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline-only", action="store_true")
    mode.add_argument("--run-models", action="store_true")
    mode.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--protocol")
    args = parser.parse_args(argv)
    try:
        if args.out.exists() or args.out.is_symlink():
            fail("output_exists")
        if not args.out.parent.is_dir():
            fail("output_directory_missing")
        if (args.run_models or args.worker is not None) and args.protocol != PROTOCOL_SHA256:
            fail("model_run_requires_explicit_protocol_flag")
        if args.worker is not None:
            report = {"status": "worker_ok", "result": model_worker(args.repo, args.worker), "END": "research357_worker"}
        else:
            artifacts, queries, hashes = load_data(args.repo)
            baseline = baseline_run(artifacts, queries)
            versions = {}
            for package in ("sentence-transformers", "transformers", "torch", "numpy", "huggingface-hub"):
                try:
                    versions[package] = importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:
                    versions[package] = None
            report = {"schema": SCHEMA, "status": "baseline_only" if args.baseline_only else "model_evaluation_complete", "source_sha": SOURCE_SHA, "protocol_sha256": PROTOCOL_SHA256, "reconstruction": True, "dataset_hashes": hashes, "corpus_artifacts": len(artifacts), "query_counts": dict(Counter(q["class"] for q in queries)), "environment": {"python": sys.version.split()[0], "packages": versions}, "id_mapping": [{"old": a["id"], "canonical": canonical_id(a["id"])} for a in artifacts if a["id"] != canonical_id(a["id"])], "exact_contract_analysis": exact_contract_analysis(artifacts, queries), "baseline": baseline, "models": run_models(args.repo) if args.run_models else [], "END": "research357"}
        atomic_json(args.out, report)
        print("RC=0")
        return 0
    except Exception as exc:  # noqa: BLE001 -- sanitize local dependency and filesystem errors
        reason = str(exc) if isinstance(exc, ResearchError) else type(exc).__name__
        error = {"schema": SCHEMA, "status": "error", "reason": reason, "source_sha": SOURCE_SHA, "END": "research357"}
        try:
            if not args.out.exists() and args.out.parent.is_dir():
                atomic_json(args.out, error)
        except OSError:
            pass
        print("STOP: " + reason)
        print("RC=1")
        return 1


if __name__ == "__main__":
    sys.exit(main())
