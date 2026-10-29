"""Исследование сигнала «ничего релевантного» (#408).

Для выборки размеченных запросов владельца (``relevant``/``partial``/``absent``)
снимает признаки сходства локальной модели (#407) на уровне фрагментов и
объектов и сравнивает варианты сигнала по доле ошибок. Модель и корпус
загружаются один раз; снимки ``search_index/2`` обоих уровней строятся в
``--snapshot-dir`` при первом запуске и переиспользуются, пока свежи.

Выгрузка только читается. В вывод попадают только номер запроса, метка и
числа — текст запросов и имена объектов не выводятся. Вывод — JSONL:
строки ``{"row": ...}`` по запросам и итоговая ``{"summary": ..., "end": true}``.
RC: 0 — успех; 1 — сбой (STOP); 2 — ошибка аргументов.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SCRIPT = "measure_relevance_issue408.py"
VERSION = "1"
LABELS = ("relevant", "partial", "absent")
FEATURES = (
    "f_top1", "f_top5", "f_gap", "f_z",
    "o_top1", "o_gap", "o_z",
    "lex_exact", "lex_overlap",
)
_WORD = re.compile(r"\w{4,}")


def _load_queries(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["queries"] if isinstance(data, dict) else data
    out = []
    for item in items:
        text, label = item["text"], item["label"]
        if not isinstance(text, str) or not text.strip() or label not in LABELS:
            raise ValueError("bad query item")
        out.append((text, label))
    if not out:
        raise ValueError("no queries")
    return out


def level_features(
    matrix: Any, norms: Any, rows: Any, query: Sequence[float], np: Any, top_k: int
) -> dict[str, float]:
    """Косинус ко всем записям, лучший фрагмент артефакта, признаки разрыва."""
    q = np.asarray(query, dtype=np.float32)
    scores = (matrix @ q) / (norms * np.linalg.norm(q))
    best = np.full(int(rows.max()) + 1, -np.inf, dtype=np.float64)
    np.maximum.at(best, rows, scores.astype(np.float64))
    best = np.sort(best[np.isfinite(best)])[::-1]
    k = min(top_k, best.size)
    top = best[:k]
    mean_all = float(scores.mean())
    std_all = float(scores.std()) or 1.0
    return {
        "top1": float(top[0]),
        "top5": float(top[: min(5, k)].mean()),
        "gap": float(top[0] - statistics.median(top.tolist())),
        "z": (float(top[0]) - mean_all) / std_all,
    }


def lexical(text: str, catalog: Any, query_cls: Any, top_text: str) -> dict[str, float]:
    exact = catalog.resolve(query_cls(text, mode="exact")).hits
    words = {w.casefold() for w in _WORD.findall(text)}
    found = {w for w in words if w in top_text.casefold()}
    return {"lex_exact": float(bool(exact)),
            "lex_overlap": len(found) / len(words) if words else 0.0}


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Порог по каждому признаку: absent против relevant; partial — отдельно.

    Порог выбирается по минимуму ошибок; оценка — leave-one-out (порог
    подбирается без проверяемого запроса). Также AUC (вероятность, что
    relevant выше absent).
    """
    pos = [r for r in rows if r["label"] == "relevant"]
    neg = [r for r in rows if r["label"] == "absent"]
    part = [r for r in rows if r["label"] == "partial"]
    result: dict[str, Any] = {"n": {"relevant": len(pos), "absent": len(neg),
                                    "partial": len(part)}}
    if not pos or not neg:
        result["features"] = {}
        return result

    def best_threshold(p: list[float], n: list[float]) -> tuple[float, int]:
        candidates = sorted(set(p + n))
        cuts = [candidates[0] - 1e-9] + [
            (a + b) / 2 for a, b in itertools.pairwise(candidates)
        ] + [candidates[-1] + 1e-9]
        best = (cuts[0], len(p) + len(n))
        for cut in cuts:
            errors = sum(v <= cut for v in p) + sum(v > cut for v in n)
            if errors < best[1]:
                best = (cut, errors)
        return best

    features: dict[str, Any] = {}
    for name in FEATURES:
        p = [r[name] for r in pos]
        n = [r[name] for r in neg]
        if any(math.isnan(v) for v in p + n):
            continue
        cut, errors = best_threshold(p, n)
        loo = 0
        for item in pos + neg:
            pp = [r[name] for r in pos if r is not item]
            nn = [r[name] for r in neg if r is not item]
            if not pp or not nn:
                continue
            c, _ = best_threshold(pp, nn)
            loo += (item[name] > c) != (item["label"] == "relevant")
        auc = sum((a > b) + 0.5 * (a == b) for a in p for b in n) / (len(p) * len(n))
        partial_above = sum(r[name] > cut for r in part)
        features[name] = {
            "threshold": round(cut, 4), "train_errors": errors,
            "loo_errors": loo, "loo_error_rate": round(loo / (len(p) + len(n)), 4),
            "auc": round(auc, 4),
            "relevant": [round(min(p), 4), round(statistics.median(p), 4), round(max(p), 4)],
            "absent": [round(min(n), 4), round(statistics.median(n), 4), round(max(n), 4)],
            "partial_above_threshold": partial_above,
        }
    result["features"] = features
    return result


def main(argv: list[str] | None = None) -> int:
    print(f"{SCRIPT} v{VERSION}", file=sys.stderr)
    parser = argparse.ArgumentParser(prog=SCRIPT)
    parser.add_argument("export_root", type=Path)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-revision", default=None)
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp16")
    parser.add_argument("--mode", choices=("config", "external"), default="config")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)
    for path in (args.export_root, args.snapshot_dir, args.model):
        if not path.is_dir():
            print("STOP: export_root, --snapshot-dir and --model must be directories",
                  file=sys.stderr)
            return 2
    if not args.queries.is_file() or args.top_k < 1:
        print("STOP: bad --queries or --top-k", file=sys.stderr)
        return 2
    try:
        return _run(args)
    except Exception as exc:  # noqa: BLE001 - обезличенный STOP
        print(f"STOP: {type(exc).__name__}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    import numpy as np

    from v8unpack_agent import search_index as si
    from v8unpack_agent.search_api import (
        build_object_snapshot,
        build_search_snapshot,
        collect_search_corpus,
    )
    from v8unpack_agent.search_contract import SearchQuery
    from v8unpack_agent.search_dispatch import ExactCatalog
    from v8unpack_agent.search_fragments import fragment_corpus
    from v8unpack_agent.search_local_model import SentenceTransformerEmbedder

    queries = _load_queries(args.queries)
    fragmentation = fragment_corpus(collect_search_corpus(args.export_root, mode=args.mode))
    objects = si.object_level(fragmentation)

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    embedder = SentenceTransformerEmbedder(
        args.model, model_name=args.model_name, model_revision=args.model_revision,
        precision=args.precision, cache_dir=args.snapshot_dir / "cache", progress=say,
    )
    paths = (args.snapshot_dir / "fragments.json", args.snapshot_dir / "objects.json")
    indexes = []
    for path, frag, build in ((paths[0], fragmentation, build_search_snapshot),
                              (paths[1], fragmentation, build_object_snapshot)):
        index = None
        if path.exists():
            try:
                index = si.load_index(path)
                target = objects if build is build_object_snapshot else frag
                if not index.assess(embedder.identity, target).queryable:
                    index = None
            except si.SearchIndexError:
                index = None
        if index is None:
            say(f"build: {path.name}")
            index = build(frag, embedder, path, schema=si.SEARCH_INDEX_SCHEMA_V2)
        status = index.assess(embedder.identity,
                              objects if build is build_object_snapshot else frag)
        if status.freshness != "fresh" or status.compatibility != "compatible":
            raise RuntimeError("snapshot not fresh")
        indexes.append(index)
    prepared = [si._prepared(index) for index in indexes]
    catalog = ExactCatalog.from_fragmentation(fragmentation)
    texts = {f.document.document_id: f.search_text for f in fragmentation.fragments}

    rows = []
    for number, (text, label) in enumerate(queries, start=1):
        vector = embedder.embed([text])[0]
        f = level_features(*prepared[0][:3], vector, np, args.top_k)
        o = level_features(*prepared[1][:3], vector, np, args.top_k)
        top = si.query_index(indexes[0], SearchQuery(text, mode="semantic", top_k=1),
                             _Fixed(embedder.identity, vector), fragmentation)
        top_text = texts.get(str(top.hits[0].document_id), "") if top.hits else ""
        row = {"query": number, "label": label,
               "f_top1": f["top1"], "f_top5": f["top5"], "f_gap": f["gap"], "f_z": f["z"],
               "o_top1": o["top1"], "o_gap": o["gap"], "o_z": o["z"]}
        row.update(lexical(text, catalog, SearchQuery, top_text))
        rows.append({k: round(v, 5) if isinstance(v, float) else v for k, v in row.items()})
        print(json.dumps({"row": rows[-1]}, ensure_ascii=True), flush=True)
    summary = {
        "script": f"{SCRIPT} v{VERSION}", "queries": len(rows),
        "fragments": len(fragmentation.fragments), "objects": len(objects.fragments),
        "embedder": embedder.identity.to_dict(), "precision": args.precision,
        "top_k": args.top_k, "evaluation": evaluate(rows),
    }
    print(json.dumps({"summary": summary, "end": True}, ensure_ascii=True), flush=True)
    return 0


class _Fixed:
    """Эмбеддер с готовым вектором запроса (модель уже вызвана)."""

    def __init__(self, identity: Any, vector: Sequence[float]) -> None:
        self.identity = identity
        self._vector = list(vector)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


if __name__ == "__main__":
    sys.exit(main())
