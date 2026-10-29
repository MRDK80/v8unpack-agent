"""Исследование размера снимка search_index/1 и вариантов хранения (#399).

Скрипт не меняет runtime и не загружает модель. Два режима:

* ``synthetic`` (по умолчанию) — синтетические снимки разного масштаба и
  размерности, собранные настоящими конструкторами ``IndexEntry``/``SearchIndex``
  и сохранённые настоящим ``save_index``;
* ``snapshot PATH [PATH ...]`` — обезличенный замер существующих снимков
  владельца: только числа (размеры, время, доли), без имён, путей и текста.
  Снимки только читаются; варианты пишутся во временный каталог и удаляются.

Варианты хранения — прототипы для сравнения, а не новый формат: каждый
проверяется обратным чтением и сверкой векторов, но схема search_index/1 не
меняется. Вывод — JSON в stdout (ASCII), первая строка stderr — имя/версия.
Код возврата 0 только при сходимости составляющих с размером снимка и
успешной сверке всех вариантов; иначе 1 (STOP), ошибка аргументов — 2.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import shutil
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

SCRIPT = "measure_snapshot_size_issue399.py"
VERSION = "2"

try:  # numpy — только для вариантов с двоичными векторами
    import numpy as np
except ImportError:  # pragma: no cover - окружение без numpy
    np = None  # type: ignore[assignment]

from v8unpack_agent.search_contract import ArtifactRef, OwnerRef, TextSpan
from v8unpack_agent.search_index import (
    EmbedderIdentity,
    IndexEntry,
    SearchIndex,
    load_index,
    save_index,
)

_SEP = (",", ":")


def import_origin() -> dict[str, str]:
    """Git blob SHA фактически импортированных модулей — без путей."""
    import v8unpack_agent.search_contract as contract_mod
    import v8unpack_agent.search_index as index_mod

    out = {}
    for name, mod in (("search_index", index_mod), ("search_contract", contract_mod)):
        data = Path(str(mod.__file__)).read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        out[name] = blob
    return out


def _dump(value: object) -> str:
    """Та же сериализация, что у снимка: компактно, sort_keys, UTF-8."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=_SEP, allow_nan=False)


def _blen(text: str) -> int:
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------- синтетика

def _vector(rng: random.Random, dim: int, profile: str) -> tuple[float, ...]:
    """Профили: sparse — разреженный мешок слов, dense — плотный float32."""
    if profile == "sparse":
        vec = [0.0] * dim
        for _ in range(max(3, dim // 16)):
            vec[rng.randrange(dim)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return tuple(v / norm for v in vec)
    raw = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw))
    if np is not None:  # значения, прошедшие через float32, как у моделей
        return tuple(float(v) for v in np.asarray(raw, dtype=np.float32) / np.float32(norm))
    return tuple(v / norm for v in raw)


def synthetic_index(n: int, dim: int, profile: str, seed: int,
                    per_artifact: int = 20) -> SearchIndex:
    rng = random.Random(seed)
    kinds = ("object", "manager", "form", "common_module")
    entries: list[IndexEntry] = []
    artifact: ArtifactRef | None = None
    for i in range(n):
        if i % per_artifact == 0:
            a = i // per_artifact
            owner = OwnerRef("metadata_object", "Catalog", f"Item{a:06d}")
            kind = kinds[a % len(kinds)]
            artifact = ArtifactRef(
                "module", owner=owner, module_kind=kind,  # type: ignore[arg-type]
                source_path=f"Catalogs/Item{a:06d}/Ext/{kind}.bsl",
            )
        assert artifact is not None
        start = 1 + (i % per_artifact) * 40
        digest = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
        entries.append(IndexEntry(
            document_id=f"doc:{i:08d}", artifact=artifact,
            procedure=f"Proc{i % per_artifact:03d}",
            span=TextSpan(start, start + 30),
            text_sha256=digest,
            search_sha256=hashlib.sha256(digest.encode()).hexdigest(),
            source_sha256=hashlib.sha256(f"src:{i // per_artifact}".encode()).hexdigest(),
            vector=_vector(rng, dim, profile),
        ))
    identity = EmbedderIdentity("synthetic", "1", dim, "cfg")
    return SearchIndex("search_corpus/1", "synthetic-repr", identity, tuple(entries))


# ---------------------------------------------------------- составляющие

def composition(index: SearchIndex, snapshot_bytes: int) -> dict[str, Any]:
    """Байты по составляющим; сумма обязана совпасть с размером файла."""
    parts = {"vector": 0, "artifact": 0, "hashes": 0, "ids_coords": 0,
             "json_overhead": 0, "header": 0}
    group = {"vector": "vector", "artifact": "artifact",
             "text_sha256": "hashes", "search_sha256": "hashes",
             "source_sha256": "hashes", "document_id": "ids_coords",
             "procedure": "ids_coords", "span": "ids_coords"}
    for entry in index.entries:
        data = entry.to_dict()
        fields = sorted(data)
        # {"k":v,"k":v}: скобки и запятые — накладные расходы JSON
        parts["json_overhead"] += 2 + (len(fields) - 1)
        for key in fields:
            parts[group[key]] += _blen(f'"{key}":' + _dump(data[key]))
    n = len(index.entries)
    parts["json_overhead"] += 2 + max(n - 1, 0)  # [ ... ] и запятые списка
    entries_sum = sum(parts.values())
    # независимая сериализация массива записей: сверка разбивки по полям
    entries_bytes = _blen(_dump([e.to_dict() for e in index.entries]))
    # заголовок = снимок без массива entries (схемы, эмбеддер, checksum)
    parts["header"] = _blen(index.to_json()) - entries_bytes
    total = sum(parts.values())
    return {
        "fragments": n, "snapshot_bytes": snapshot_bytes,
        "components_sum": total, "entries_split_ok": entries_sum == entries_bytes,
        "matches": entries_sum == entries_bytes and total == snapshot_bytes,
        "bytes": parts,
        "per_fragment": {k: round(v / n, 1) if n else None for k, v in parts.items()},
        "share": {k: round(v / total, 4) if total else None for k, v in parts.items()},
    }


# -------------------------------------------------------------- варианты

def _meta(index: SearchIndex, artifact_dict: bool) -> dict[str, Any]:
    head: dict[str, Any] = {"schema": index.schema, "corpus_schema": index.corpus_schema,
            "representation_version": index.representation_version,
            "embedder": index.embedder.to_dict()}
    if not artifact_dict:
        head["entries"] = [{k: v for k, v in e.to_dict().items() if k != "vector"}
                           for e in index.entries]
        return head
    arts: dict[str, int] = {}
    table: list[dict[str, Any]] = []
    rows = []
    for e in index.entries:
        key = e.artifact.identity
        if key not in arts:
            arts[key] = len(table)
            table.append(e.artifact.to_dict())
        row = {k: v for k, v in e.to_dict().items() if k not in ("vector", "artifact")}
        row["a"] = arts[key]
        rows.append(row)
    head["artifacts"] = table
    head["entries"] = rows
    return head


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "wb") as h:
        h.write(data)
        h.flush()
        os.fsync(h.fileno())
    os.replace(tmp, path)


def _variant_binary(index: SearchIndex, base: Path, dtype: str,
                    artifact_dict: bool, gz: bool) -> tuple[Callable[[], None], Callable[[], Any]]:
    """Метаданные JSON (+gzip) и отдельный файл векторов с общей контрольной суммой."""
    assert np is not None
    meta_path = base.with_suffix(".meta.json" + (".gz" if gz else ""))
    vec_path = base.with_suffix(f".{dtype}.bin")

    def save() -> None:
        mat = np.asarray([e.vector for e in index.entries], dtype=dtype).reshape(
            len(index.entries), index.embedder.dimension)
        vec = mat.tobytes(order="C")
        head = _meta(index, artifact_dict)
        head["vectors"] = {"dtype": dtype, "shape": list(mat.shape),
                           "sha256": hashlib.sha256(vec).hexdigest()}
        text = _dump(head)
        head["checksum"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raw = _dump(head).encode("utf-8")
        _write_atomic(vec_path, vec)
        _write_atomic(meta_path, gzip.compress(raw, 6) if gz else raw)

    def load() -> Any:
        raw = meta_path.read_bytes()
        head = json.loads((gzip.decompress(raw) if gz else raw).decode("utf-8"))
        checksum = head.pop("checksum")
        if hashlib.sha256(_dump(head).encode("utf-8")).hexdigest() != checksum:
            raise ValueError("meta checksum")
        vec = vec_path.read_bytes()
        info = head["vectors"]
        if hashlib.sha256(vec).hexdigest() != info["sha256"]:
            raise ValueError("vector checksum")
        mat = np.frombuffer(vec, dtype=info["dtype"]).reshape(info["shape"])
        if mat.size and (not np.isfinite(mat).all() or not np.any(mat, axis=1).all()):
            raise ValueError("invalid vector")
        return head, mat

    return save, load


def _files(base: Path) -> list[Path]:
    return sorted(p for p in base.parent.iterdir() if p.name.startswith(base.name + "."))


def _timed(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return round(time.perf_counter() - t0, 3), out


def _peak(fn: Callable[[], Any]) -> tuple[int, Any]:
    tracemalloc.start()
    try:
        out = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak, out


def variants(index: SearchIndex, workdir: Path, memory: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    ref = None
    if np is not None:
        ref = np.asarray([e.vector for e in index.entries], dtype=np.float64).reshape(
            len(index.entries), index.embedder.dimension)

    # V0: текущий формат — настоящие save_index/load_index
    base = workdir / "v0"
    snap = base.with_suffix(".json")
    t_save, _ = _timed(lambda: save_index(index, snap))
    t_load, loaded = _timed(lambda: load_index(snap))
    ok = loaded.entries == index.entries
    row: dict[str, Any] = {"variant": "v0_json_current", "bytes": snap.stat().st_size,
                           "save_s": t_save, "load_s": t_load, "roundtrip_ok": ok}
    if memory:
        row["load_peak_bytes"], _ = _peak(lambda: load_index(snap))
    results.append(row)
    snap_bytes = snap.read_bytes()

    t_gz, gz = _timed(lambda: gzip.compress(snap_bytes, 6))
    t_ungz, back = _timed(lambda: gzip.decompress(gz))
    results.append({"variant": "v1_json_gzip6", "bytes": len(gz), "save_s": t_gz,
                    "load_s": t_ungz, "load_note": "decompress only, + v0 parse",
                    "roundtrip_ok": back == snap_bytes})
    snap.unlink()

    if np is None:
        results.append({"variant": "binary_variants", "skipped": "numpy unavailable"})
        return results
    specs = [("v2_split_f32", "float32", False, False),
             ("v3_split_f16", "float16", False, False),
             ("v4_split_f32_artdict", "float32", True, False),
             ("v5_split_f16_artdict", "float16", True, False),
             ("v6_split_f16_artdict_gzip", "float16", True, True)]
    for name, dtype, adict, gzflag in specs:
        vbase = workdir / name
        save, load = _variant_binary(index, vbase, dtype, adict, gzflag)
        t_save, _ = _timed(save)
        t_load, (head, mat) = _timed(load)
        size = sum(p.stat().st_size for p in _files(vbase))
        assert ref is not None
        err = float(np.abs(mat.astype(np.float64) - ref).max()) if len(ref) else 0.0
        ok = len(head["entries"]) == len(index.entries) and mat.shape == ref.shape
        row = {"variant": name, "bytes": size, "save_s": t_save, "load_s": t_load,
               "max_abs_vector_error": err, "roundtrip_ok": ok}
        if memory:
            row["load_peak_bytes"], _ = _peak(load)
        results.append(row)
        for p in _files(vbase):
            p.unlink()
    return results


def ranking_agreement(index: SearchIndex, seed: int, queries: int = 20,
                      top_k: int = 10) -> dict[str, Any] | None:
    """Совпадение top_k по косинусу для float32 и float16 относительно float64."""
    if np is None or not index.entries:
        return None
    ref = np.asarray([e.vector for e in index.entries], dtype=np.float64)
    rng = np.random.default_rng(seed)
    qs = rng.standard_normal((queries, ref.shape[1]))
    out: dict[str, Any] = {"queries": queries, "top_k": top_k}
    norms = np.linalg.norm(ref, axis=1)

    def top(mat: Any) -> list[list[int]]:
        res = []
        for q in qs:
            s = (mat @ q) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q))
            res.append(list(np.lexsort((np.arange(len(s)), -s))[:top_k]))
        return res

    base = top(ref)
    for dtype in ("float32", "float16"):
        cand = top(ref.astype(dtype).astype(np.float64))
        out[dtype] = {"identical_order": sum(a == b for a, b in zip(base, cand)),
                      "same_set": sum(set(a) == set(b) for a, b in zip(base, cand))}
    out["zero_norm_rows"] = int((norms == 0).sum())
    return out


# ----------------------------------------------------------------- режимы

def _emit(row: dict[str, Any]) -> None:
    """Строка JSONL сразу в stdout: обрыв не теряет готовые строки."""
    print(json.dumps({"row": row}, ensure_ascii=True), flush=True)


def _scale(text: str) -> tuple[int, int, str]:
    """N:DIM:PROFILE; ошибка формата — ошибка аргументов (RC 2)."""
    try:
        n_s, dim_s, profile = text.split(":")
        n, dim = int(n_s), int(dim_s)
    except ValueError:
        raise argparse.ArgumentTypeError("expected N:DIM:PROFILE") from None
    if n < 1 or dim < 1 or profile not in ("sparse", "dense"):
        raise argparse.ArgumentTypeError("N, DIM >= 1; PROFILE sparse|dense")
    return n, dim, profile


def run_synthetic(args: argparse.Namespace, workdir: Path) -> dict[str, Any]:
    rows = []
    for n, dim, profile in args.scale:
        index = synthetic_index(n, dim, profile, args.seed)
        snap = workdir / "comp.json"
        save_index(index, snap)
        comp = composition(index, snap.stat().st_size)
        snap.unlink()
        row = {"fragments": n, "dimension": dim, "profile": profile,
               "composition": comp,
               "variants": variants(index, workdir, args.memory)}
        if args.ranking:
            row["ranking"] = ranking_agreement(index, args.seed)
        del index
        rows.append(row)
        _emit(row)
        print(f"done n={n} dim={dim} profile={profile}", file=sys.stderr)
    return {"mode": "synthetic", "seed": args.seed, "rows": rows}


def run_snapshot(args: argparse.Namespace, workdir: Path) -> dict[str, Any]:
    rows = []
    for label, path in zip("ABCDEFGH", args.paths):
        src = Path(path)
        size = src.stat().st_size
        t_load, index = _timed(partial(load_index, src))
        comp = composition(index, size)
        row = {"label": label, "fragments": len(index.entries),
               "dimension": index.embedder.dimension,
               "artifacts": len({e.artifact.identity for e in index.entries}),
               "load_s_current": t_load, "composition": comp,
               "variants": variants(index, workdir, args.memory)}
        if args.ranking:
            row["ranking"] = ranking_agreement(index, args.seed)
        del index
        rows.append(row)
        _emit(row)
        print(f"done {label}", file=sys.stderr)
    return {"mode": "snapshot", "rows": rows}


def _check(report: dict[str, Any]) -> list[str]:
    problems = []
    for row in report["rows"]:
        tag = row.get("label") or f"{row['fragments']}:{row['dimension']}"
        if not row["composition"]["matches"]:
            problems.append(f"composition_mismatch:{tag}")
        for v in row["variants"]:
            if v.get("roundtrip_ok") is False:
                problems.append(f"roundtrip_failed:{tag}:{v['variant']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    print(f"{SCRIPT} v{VERSION}", file=sys.stderr)
    origin = import_origin()
    print("import_blobs " + json.dumps(origin, sort_keys=True), file=sys.stderr)
    parser = argparse.ArgumentParser(prog=SCRIPT)
    parser.add_argument("--seed", type=int, default=399)
    parser.add_argument("--memory", action="store_true", help="пик памяти при загрузке")
    parser.add_argument("--ranking", action="store_true", help="совпадение top-k float32/16")
    sub = parser.add_subparsers(dest="mode")
    syn = sub.add_parser("synthetic")
    syn.add_argument("--scale", nargs="+", type=_scale,
                     default=["1600:256:sparse", "1600:1024:dense", "10000:256:sparse",
                              "10000:1024:dense"],
                     help="N:DIM:PROFILE (profile: sparse|dense)")
    snap = sub.add_parser("snapshot")
    snap.add_argument("paths", nargs="+")
    parser.set_defaults(mode="synthetic",
                        scale=[_scale(x) for x in syn.get_default("scale")])
    args = parser.parse_args(argv)
    if args.mode == "synthetic" and args.scale and isinstance(args.scale[0], str):
        args.scale = [_scale(x) for x in args.scale]
    if args.mode == "snapshot" and len(args.paths) > 8:
        print("STOP: too many snapshots", file=sys.stderr)
        return 2
    if args.mode == "snapshot" and not all(Path(p).is_file() for p in args.paths):
        print("STOP: snapshot not found", file=sys.stderr)
        return 2
    workdir = Path(tempfile.mkdtemp(prefix="issue399_"))
    try:
        if args.mode == "snapshot":
            report = run_snapshot(args, workdir)
        else:
            report = run_synthetic(args, workdir)
        problems = _check(report)
        summary = {"script": f"{SCRIPT} v{VERSION}", "mode": report["mode"],
                   "seed": args.seed, "rows": len(report["rows"]),
                   "numpy": None if np is None else np.__version__,
                   "import_blobs": origin,
                   "status": "error" if problems else "ok", "problems": problems,
                   "end": True}
        print(json.dumps({"summary": summary}, ensure_ascii=True), flush=True)
        return 1 if problems else 0
    except Exception as exc:  # noqa: BLE001 - обезличенный STOP без данных
        print(f"STOP: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
