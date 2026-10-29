"""Исследование двухуровневого слияния (#423).

Режим ``measure``: для размеченных запросов владельца (формат #408) сравнивает
выдачу только по фрагментам и двухуровневое слияние RRF (#407) на готовых
снимках ``search_index/2`` обоих уровней. В stdout — JSONL только с числами;
в ``--review`` пишется локальный файл разметки: для запросов с меткой
``relevant`` — объединение топ-5 обоих режимов с пустой отметкой. Файл остаётся
у владельца.

Режим ``score``: по отмеченному файлу (``relevant``: ``y``/``n``) считает
recall@k и MRR@10 обоих режимов на запросах с хотя бы одним отмеченным
релевантным попаданием (полнота — относительно объединения топ-5 режимов).

Выгрузка только читается. RC: 0 — успех; 1 — сбой (STOP); 2 — аргументы.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT = "measure_two_level_issue423.py"
VERSION = "1"
TOP_K = 5
DEPTH = 10
LABELS = ("relevant", "partial", "absent")


def _queries(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["queries"] if isinstance(data, dict) else data
    out = [(i["text"], i["label"]) for i in items]
    if not out or any(label not in LABELS for _, label in out):
        raise ValueError("bad queries")
    return out


def rank(ids: list[str], key: str) -> int | None:
    """1-based ранг или None."""
    return ids.index(key) + 1 if key in ids else None


def scores(marks: dict[str, bool], runs: list[list[str]], k: int) -> dict[str, Any]:
    """recall@k и MRR@DEPTH по отметкам для списка выдач одного режима.

    ``marks`` — отметки ``"запрос|артефакт" -> релевантен``; выдачи — список
    идентификаторов артефактов по запросам (индекс = номер запроса - 1).
    """
    recalls, rr = [], []
    for number, ids in enumerate(runs, start=1):
        gold = {key.split("|", 1)[1] for key, ok in marks.items()
                if ok and key.split("|", 1)[0] == str(number)}
        if not gold:
            continue
        recalls.append(len(gold & set(ids[:k])) / len(gold))
        first = next((i for i, a in enumerate(ids[:DEPTH], start=1) if a in gold), None)
        rr.append(1.0 / first if first else 0.0)
    if not recalls:
        return {"queries": 0}
    return {"queries": len(recalls), f"recall@{k}": round(statistics.mean(recalls), 4),
            f"mrr@{DEPTH}": round(statistics.mean(rr), 4)}


def _measure(args: argparse.Namespace) -> int:
    from v8unpack_agent import search_index as si
    from v8unpack_agent.search_api import collect_search_corpus
    from v8unpack_agent.search_contract import SearchQuery
    from v8unpack_agent.search_fragments import fragment_corpus
    from v8unpack_agent.search_local_model import SentenceTransformerEmbedder
    from v8unpack_agent.search_relevance import MemoEmbedder

    queries = _queries(args.queries)
    fragmentation = fragment_corpus(collect_search_corpus(args.export_root))
    objects = si.object_level(fragmentation)
    frag_index = si.load_index(args.snapshot_dir / "fragments.json")
    obj_index = si.load_index(args.snapshot_dir / "objects.json")
    embedder = MemoEmbedder(SentenceTransformerEmbedder(
        args.model, model_name=args.model_name, model_revision=args.model_revision,
        precision=args.precision))
    for index, target in ((frag_index, fragmentation), (obj_index, objects)):
        status = index.assess(embedder.identity, target)
        if status.freshness != "fresh" or status.compatibility != "compatible":
            raise RuntimeError("snapshot not fresh")
    sizes: dict[str, int] = {}
    for fragment in fragmentation.fragments:
        key = fragment.document.artifact.artifact_id
        sizes[key] = sizes.get(key, 0) + 1

    review: list[dict[str, Any]] = []
    rows = []
    for number, (text, label) in enumerate(queries, start=1):
        wide = SearchQuery(text, mode="semantic", top_k=max(DEPTH, 200))
        frag = [str(h.artifact.artifact_id)
                for h in si.query_index(frag_index, wide, embedder, fragmentation).hits]
        obj = [str(h.artifact.artifact_id)
               for h in si.query_index(obj_index, wide, embedder, objects).hits]
        two = [str(h.artifact.artifact_id) for h in si.query_two_level(
            frag_index, obj_index, SearchQuery(text, mode="semantic", top_k=DEPTH),
            embedder, fragmentation).hits]
        f5, t5 = frag[:TOP_K], two[:TOP_K]
        top = f5[0] if f5 else None
        row: dict[str, Any] = {
            "query": number, "label": label,
            "top1_same": bool(f5 and t5 and f5[0] == t5[0]),
            "top5_overlap": len(set(f5) & set(t5)),
            "frag_top1_rank_in_two": rank(two, top) if top else None,
            "frag_top1_rank_on_objects": rank(obj, top) if top else None,
            "frag_top1_fragments": sizes.get(top, 0) if top else None,
            "lost_from_two_top5": [sizes.get(a, 0) for a in f5 if a not in t5],
            "added_in_two_top5": [sizes.get(a, 0) for a in t5 if a not in f5],
        }
        rows.append(row)
        print(json.dumps({"row": row}, sort_keys=True), flush=True)
        for artifact in dict.fromkeys(f5 + t5) if label == "relevant" else ():
            review.append({"query": number, "label": label, "text": text,
                           "artifact": artifact, "fragment_rank": rank(f5, artifact),
                           "two_level_rank": rank(t5, artifact), "relevant": ""})
        rows[-1]["_frag"], rows[-1]["_two"] = frag[:DEPTH], two[:DEPTH]

    runs = {"queries": [{"frag": r.pop("_frag"), "two": r.pop("_two")} for r in rows]}
    args.review.write_text(json.dumps({"runs": runs, "review": review}, ensure_ascii=False,
                                      indent=1), encoding="utf-8")
    rel = [r for r in rows if r["label"] == "relevant"]
    lost = [s for r in rows for s in r["lost_from_two_top5"]]
    added = [s for r in rows for s in r["added_in_two_top5"]]
    summary = {
        "script": f"{SCRIPT} v{VERSION}", "queries": len(rows),
        "fragments": len(fragmentation.fragments), "objects": len(objects.fragments),
        "top1_same": sum(r["top1_same"] for r in rows),
        "top1_same_relevant": sum(r["top1_same"] for r in rel), "relevant": len(rel),
        "frag_top1_not_in_two_top5": sum(1 for r in rows
                                         if (r["frag_top1_rank_in_two"] or 99) > TOP_K),
        "mean_top5_overlap": round(statistics.mean(r["top5_overlap"] for r in rows), 2),
        "lost_fragments_median": statistics.median(lost) if lost else None,
        "added_fragments_median": statistics.median(added) if added else None,
        "review_items": len(review),
    }
    print(json.dumps({"summary": summary, "end": True}, sort_keys=True), flush=True)
    return 0


def _score(args: argparse.Namespace) -> int:
    data = json.loads(args.review.read_text(encoding="utf-8"))
    marks: dict[str, bool] = {}
    unmarked = 0
    for item in data["review"]:
        value = str(item.get("relevant", "")).strip().lower()
        if value not in ("y", "n"):
            unmarked += 1
            continue
        marks[f"{item['query']}|{item['artifact']}"] = value == "y"
    runs = data["runs"]["queries"]
    result = {"marked": len(marks), "unmarked": unmarked,
              "fragments": scores(marks, [r["frag"] for r in runs], TOP_K),
              "two_level": scores(marks, [r["two"] for r in runs], TOP_K), "end": True}
    print(json.dumps(result, sort_keys=True))
    return 0 if unmarked == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SCRIPT)
    sub = parser.add_subparsers(dest="action", required=True)
    m = sub.add_parser("measure")
    m.add_argument("export_root", type=Path)
    m.add_argument("--queries", type=Path, required=True)
    m.add_argument("--snapshot-dir", type=Path, required=True)
    m.add_argument("--model", type=Path, required=True)
    m.add_argument("--model-name", required=True)
    m.add_argument("--model-revision", default=None)
    m.add_argument("--precision", choices=("fp32", "fp16"), default="fp16")
    m.add_argument("--review", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(argv)
    print(f"{SCRIPT} v{VERSION}", file=sys.stderr)
    try:
        if args.action == "measure":
            if args.review.exists():
                print("STOP: --review already exists", file=sys.stderr)
                return 2
            return _measure(args)
        return _score(args)
    except Exception as exc:  # noqa: BLE001 - обезличенный STOP
        print(f"STOP: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
