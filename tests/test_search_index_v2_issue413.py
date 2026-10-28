"""Формат хранения ``search_index/2`` (#413).

Метаданные JSON и файл векторов рядом. Проверяются: совпадение загруженного
индекса и выдачи с ``search_index/1`` (с numpy и без), выбор float32/float64
без потерь, отказ ``corrupt_snapshot`` на каждом виде повреждения, атомарная
публикация (сбой на любом шаге оставляет прежний снимок читаемым), удаление
устаревших файлов векторов, готовая матрица #406 и вход ``search_cli``.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_cli, search_index
from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchFilters,
    SearchQuery,
    TextSpan,
)
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_index import (
    SEARCH_INDEX_SCHEMA,
    SEARCH_INDEX_SCHEMA_V2,
    EmbedderIdentity,
    IndexEntry,
    SearchIndex,
    SearchIndexError,
    load_index,
    query_index,
    save_index,
)

DIM = 5
IDENTITY = EmbedderIdentity("fake413", "1", DIM, "c413")


class FixedEmbedder:
    identity = IDENTITY

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


def _index(seed: int, *, float32_values: bool) -> SearchIndex:
    rng = random.Random(seed)
    entries = []
    for a in range(12):
        owner = OwnerRef("metadata_object", ("Catalog", "Document")[a % 2], f"Объект{a:02d}")
        artifact = ArtifactRef("module", owner, ("object", "manager")[a % 2])
        for p in range(1 + a % 3):
            if float32_values:
                vector = [float(rng.randint(-4, 4)) / 8 for _ in range(DIM)]
            else:
                vector = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
            if not any(vector):
                vector[0] = 0.5
            span = TextSpan(1 + p * 10, 5 + p * 10) if p % 2 else None
            procedure = f"Процедура{p}" if p else None
            digest = hashlib.sha256(f"{seed}:{a}:{p}".encode()).hexdigest()
            entries.append(IndexEntry(
                f"{artifact.artifact_id}#{p}", artifact, procedure, span,
                digest, digest[::-1], hashlib.sha256(digest.encode()).hexdigest(),
                tuple(vector),
            ))
    return SearchIndex(SEARCH_CORPUS_SCHEMA, "rv413", IDENTITY, tuple(entries))


def _vec_files(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".vec"))


@pytest.fixture(params=["numpy", "stdlib"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "numpy":
        pytest.importorskip("numpy")
    else:
        monkeypatch.setattr(search_index, "_np", None)
    return str(request.param)


@pytest.mark.parametrize("float32_values", [True, False])
def test_roundtrip_equals_v1(
    tmp_path: Path, backend: str, float32_values: bool
) -> None:
    index = _index(1, float32_values=float32_values)
    v1, v2 = tmp_path / "v1.json", tmp_path / "v2.json"
    assert save_index(index, v1) == SEARCH_INDEX_SCHEMA
    assert save_index(index, v2, schema=SEARCH_INDEX_SCHEMA_V2) == SEARCH_INDEX_SCHEMA_V2
    from_v1, from_v2 = load_index(v1), load_index(v2)
    assert from_v2 == index == from_v1
    assert from_v2.schema == SEARCH_INDEX_SCHEMA
    meta = json.loads(v2.read_text(encoding="utf-8"))
    assert meta["schema"] == SEARCH_INDEX_SCHEMA_V2
    assert meta["vectors"]["dtype"] == ("float32" if float32_values else "float64")
    assert len(meta["artifacts"]) == 12
    rng = random.Random(7)
    for _ in range(10):
        vector = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
        for filters in (SearchFilters(), SearchFilters(metadata_types=("Document",))):
            for top_k in (1, 3, 20):
                query = SearchQuery("q", mode="semantic", top_k=top_k, filters=filters)
                embedder = FixedEmbedder(vector)
                expected = query_index(from_v1, query, embedder)
                assert query_index(from_v2, query, embedder) == expected


def test_backends_write_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("numpy")
    index = _index(2, float32_values=False)
    save_index(index, tmp_path / "a.json", schema=SEARCH_INDEX_SCHEMA_V2)
    with monkeypatch.context() as patched:
        patched.setattr(search_index, "_np", None)
        save_index(index, tmp_path / "b.json", schema=SEARCH_INDEX_SCHEMA_V2)
        assert load_index(tmp_path / "a.json") == index
    (vec_a,) = [p for p in tmp_path.iterdir() if p.name.startswith("a.json.")]
    (vec_b,) = [p for p in tmp_path.iterdir() if p.name.startswith("b.json.")]
    assert vec_a.read_bytes() == vec_b.read_bytes()


def test_float32_is_smaller(tmp_path: Path) -> None:
    index = _index(3, float32_values=True)
    save_index(index, tmp_path / "s.json", schema=SEARCH_INDEX_SCHEMA_V2)
    (vec,) = [p for p in tmp_path.iterdir() if p.suffix == ".vec"]
    assert vec.stat().st_size == len(index.entries) * DIM * 4


def test_empty_index(tmp_path: Path, backend: str) -> None:
    index = SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, ())
    path = tmp_path / "s.json"
    save_index(index, path, schema=SEARCH_INDEX_SCHEMA_V2)
    assert load_index(path) == index


def test_prefilled_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    np = pytest.importorskip("numpy")
    index = _index(4, float32_values=False)
    path = tmp_path / "s.json"
    save_index(index, path, schema=SEARCH_INDEX_SCHEMA_V2)
    loaded = load_index(path)
    assert "matrix" in loaded._cache
    expected = search_index._prepared(_index(4, float32_values=False))
    for got, want in zip(loaded._cache["matrix"][:3], expected[:3]):
        assert np.array_equal(got, want)
    assert loaded._cache["matrix"][3] == expected[3]


def _rewrite_meta(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.pop("checksum")
    change(meta)
    text = search_index._dump(meta)
    meta["checksum"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path.write_text(search_index._dump(meta), encoding="utf-8")


def _rewrite_vectors(path: Path, payload: bytes) -> None:
    """Подменить файл векторов с согласованными суммами и именем."""
    meta = json.loads(path.read_text(encoding="utf-8"))
    (path.parent / meta["vectors"]["file"]).unlink()
    digest = hashlib.sha256(payload).hexdigest()
    name = f"{path.name}.{digest[:16]}.vec"
    (path.parent / name).write_bytes(payload)

    def change(m: dict[str, Any]) -> None:
        m["vectors"]["file"] = name
        m["vectors"]["sha256"] = digest

    _rewrite_meta(path, change)


def _redirect(path: Path, rename: Callable[[str], str]) -> None:
    """Сослаться на настоящую копию векторов по чужому имени: её чтение
    прошло бы все проверки сумм, поэтому отказ даёт только проверка имени."""
    name = json.loads(path.read_text(encoding="utf-8"))["vectors"]["file"]
    other = rename(name)
    (path.parent / other).write_bytes((path.parent / name).read_bytes())
    _rewrite_meta(path, lambda m: m["vectors"].update(file=other))


def _vectors(path: Path) -> Path:
    meta = json.loads(path.read_text(encoding="utf-8"))
    return path.parent / meta["vectors"]["file"]


def _float64_payload(values: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(values)}d", *values)


CORRUPTIONS: dict[str, Callable[[Path], None]] = {
    "meta_checksum": lambda p: p.write_text(
        p.read_text(encoding="utf-8").replace("rv413", "rv414"), encoding="utf-8"
    ),
    "meta_not_json": lambda p: p.write_bytes(b"{not json"),
    "extra_key": lambda p: _rewrite_meta(p, lambda m: m.update(extra=1)),
    "missing_vectors_file": lambda p: _vectors(p).unlink(),
    "vector_bytes_changed": lambda p: _vectors(p).write_bytes(
        b"\x00" + _vectors(p).read_bytes()[1:]
    ),
    "truncated_vectors": lambda p: _rewrite_vectors(p, _vectors(p).read_bytes()[:-8]),
    "nan_vector": lambda p: _rewrite_vectors(
        p, _float64_payload([float("nan")] + [1.0] * (_n(p) * DIM - 1))
    ),
    "zero_vector": lambda p: _rewrite_vectors(
        p, _float64_payload([0.0] * DIM + [1.0] * ((_n(p) - 1) * DIM))
    ),
    "traversal_name": lambda p: _redirect(p, lambda name: "../" + name),
    "foreign_name": lambda p: _redirect(p, lambda name: "other.json" + name[6:]),
    "bad_shape": lambda p: _rewrite_meta(
        p, lambda m: m["vectors"].update(shape=[m["vectors"]["shape"][0], DIM + 1])
    ),
    "bad_dtype": lambda p: _rewrite_meta(
        p, lambda m: m["vectors"].update(dtype="float16")
    ),
    "bad_byteorder": lambda p: _rewrite_meta(
        p, lambda m: m["vectors"].update(byteorder="big")
    ),
    "artifact_out_of_range": lambda p: _rewrite_meta(
        p, lambda m: m["entries"][0].update(artifact=len(m["artifacts"]))
    ),
    "artifact_bool": lambda p: _rewrite_meta(
        p, lambda m: m["entries"][0].update(artifact=True)
    ),
    "entry_extra_key": lambda p: _rewrite_meta(
        p, lambda m: m["entries"][0].update(vector=[1.0] * DIM)
    ),
    "duplicate_document_id": lambda p: _rewrite_meta(
        p, lambda m: m["entries"][1].update(document_id=m["entries"][0]["document_id"])
    ),
    "bad_artifact": lambda p: _rewrite_meta(
        p, lambda m: m["artifacts"][0].update(artifact_kind="unknown")
    ),
}


def _n(path: Path) -> int:
    return int(json.loads(path.read_text(encoding="utf-8"))["vectors"]["shape"][0])


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_corruption_rejected(tmp_path: Path, backend: str, name: str) -> None:
    path = tmp_path / "s.json"
    save_index(_index(5, float32_values=False), path, schema=SEARCH_INDEX_SCHEMA_V2)
    CORRUPTIONS[name](path)
    with pytest.raises(SearchIndexError) as err:
        load_index(path)
    assert err.value.code == "corrupt_snapshot"
    assert str(tmp_path) not in str(err.value)


def _failing_replace(fail_on: str) -> Callable[[Any, Any], None]:
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if str(dst).endswith(fail_on):
            raise OSError("disk full")
        real(src, dst)

    return replace


@pytest.mark.parametrize("fail_on", [".vec", "s.json"])
@pytest.mark.parametrize("previous", [SEARCH_INDEX_SCHEMA, SEARCH_INDEX_SCHEMA_V2])
def test_failed_publish_keeps_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_on: str, previous: str
) -> None:
    path = tmp_path / "s.json"
    old = _index(6, float32_values=False)
    save_index(old, path, schema=previous)
    before = sorted(p.name for p in tmp_path.iterdir())
    monkeypatch.setattr(os, "replace", _failing_replace(fail_on))
    with pytest.raises(OSError):
        save_index(_index(7, float32_values=False), path, schema=SEARCH_INDEX_SCHEMA_V2)
    monkeypatch.undo()
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert load_index(path) == old


def test_stale_vectors_removed(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    neighbour = tmp_path / "other.json"
    save_index(_index(8, float32_values=False), neighbour, schema=SEARCH_INDEX_SCHEMA_V2)
    save_index(_index(8, float32_values=False), path, schema=SEARCH_INDEX_SCHEMA_V2)
    save_index(_index(9, float32_values=False), path, schema=SEARCH_INDEX_SCHEMA_V2)
    mine = [n for n in _vec_files(path) if n.startswith("s.json.")]
    assert len(mine) == 1
    assert load_index(path) == _index(9, float32_values=False)
    save_index(_index(9, float32_values=False), path)
    assert [n for n in _vec_files(path) if n.startswith("s.json.")] == []
    assert load_index(neighbour) == _index(8, float32_values=False)


def test_same_content_resave(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    index = _index(10, float32_values=True)
    save_index(index, path, schema=SEARCH_INDEX_SCHEMA_V2)
    first = sorted(p.name for p in tmp_path.iterdir())
    save_index(index, path, schema=SEARCH_INDEX_SCHEMA_V2)
    assert sorted(p.name for p in tmp_path.iterdir()) == first
    assert load_index(path) == index


def test_unknown_schema_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_index(_index(11, float32_values=True), tmp_path / "s.json", schema="search_index/9")
    assert list(tmp_path.iterdir()) == []


FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриОткрытии()\n  ЦенаТовара = 1;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.json": "{}",
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура ОбщаяПроцедура() Экспорт\n  Сумма = 0;\nКонецПроцедуры\n"
    ),
}


def test_cli_build_and_query_v2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "export-413"
    for relative, text in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    out = tmp_path / "snap"
    out.mkdir()
    results = {}
    for fmt in ("1", "2"):
        snapshot = out / f"s{fmt}.json"
        code = search_cli.main([
            "build", str(root), "--snapshot", str(snapshot),
            "--embedder", "hashed-bow-demo", "--snapshot-format", fmt,
        ])
        build = json.loads(capsys.readouterr().out)
        assert code == 0
        assert build["snapshot"]["schema"] == f"search_index/{fmt}"
        code = search_cli.main([
            "query", str(root), "--query", "цена", "--search-mode", "semantic",
            "--snapshot", str(snapshot), "--embedder", "hashed-bow-demo",
        ])
        query = json.loads(capsys.readouterr().out)
        assert code == 0
        assert query["snapshot"]["loaded"] is True
        assert query["snapshot"]["freshness"] == "fresh"
        results[fmt] = query["context"]
    assert results["1"] == results["2"]
    assert str(tmp_path) not in json.dumps(results)


def test_cli_rejects_unknown_format(tmp_path: Path) -> None:
    code = search_cli.main([
        "build", str(tmp_path), "--snapshot", str(tmp_path / "s.json"),
        "--embedder", "hashed-bow-demo", "--snapshot-format", "3",
    ])
    assert code == 2
