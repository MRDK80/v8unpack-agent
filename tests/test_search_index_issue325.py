"""Офлайн-тесты общего индекса и снимка (#325)."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchFilters,
    SearchQuery,
)
from v8unpack_agent.search_fragments import (
    FragmentationResult,
    FragmentParameters,
    fragment_module,
)
from v8unpack_agent.search_index import (
    EmbedderIdentity,
    SearchIndexError,
    build_index,
    load_index,
    query_index,
    save_index,
)

CAT = ArtifactRef("module", OwnerRef("metadata_object", "Catalog", "Товары"), "object")
DOC = ArtifactRef("module", OwnerRef("metadata_object", "Document", "Заказ"), "object")
SRC_A = "Процедура Алфа()\n  Сообщить(1);\nКонецПроцедуры\n"
SRC_B = "Процедура Бета()\n  Сообщить(2);\nКонецПроцедуры\n"
WORDS = ("алфа", "бета", "гамма", "сообщить")


class Fake:
    def __init__(self, dim: int = 4, config: str = "c1", mode: str = "ok") -> None:
        self.identity = EmbedderIdentity("fake", "1", dim, config)
        self.mode = mode

    def embed(self, texts):
        if self.mode == "raise":
            raise RuntimeError("secret-path")
        if self.mode == "short":
            return [[1.0]] * len(texts)
        if self.mode == "nan":
            return [[math.nan] * self.identity.dimension for _ in texts]
        if self.mode == "bool":
            return [[True] * self.identity.dimension for _ in texts]
        if self.mode == "count":
            return []
        if self.mode == "same":
            return [[1.0] * self.identity.dimension for _ in texts]
        return [[1.0 + t.count(w) for w in WORDS][: self.identity.dimension] for t in texts]


def frag(src_a: str = SRC_A, **kw: int) -> FragmentationResult:
    params = FragmentParameters(**kw)
    first = fragment_module(CAT, src_a, parameters=params)
    second = fragment_module(DOC, SRC_B, parameters=params)
    return FragmentationResult(
        params, first.fragments + second.fragments, first.items + second.items)


def test_roundtrip_preserves_query_result(tmp_path: Path) -> None:
    emb, fr = Fake(), frag()
    index = build_index(fr, emb)
    path = tmp_path / "snap.json"
    save_index(index, path)
    loaded = load_index(path)
    assert loaded == index
    q = SearchQuery("алфа", mode="semantic", top_k=3)
    assert query_index(loaded, q, emb, fr) == query_index(index, q, emb, fr)
    assert query_index(loaded, q, emb, fr).status == "semantic"


def test_snapshot_has_no_source_text(tmp_path: Path) -> None:
    index = build_index(frag(), Fake())
    save_index(index, tmp_path / "s.json")
    raw = (tmp_path / "s.json").read_text(encoding="utf-8")
    assert "Сообщить" not in raw and "Процедура Алфа" not in raw
    entry = json.loads(raw)["entries"][0]
    assert {"text_sha256", "search_sha256", "source_sha256"} <= set(entry)


@pytest.mark.parametrize("mode", ["short", "nan", "bool", "count"])
def test_invalid_vectors_rejected(mode: str) -> None:
    with pytest.raises(SearchIndexError) as err:
        build_index(frag(), Fake(mode=mode))
    assert err.value.code == "invalid_vector"


def test_embedder_failure_is_safe() -> None:
    with pytest.raises(SearchIndexError) as err:
        build_index(frag(), Fake(mode="raise"))
    assert err.value.code == "embedder_failed"
    assert "secret" not in str(err.value)


def test_query_vector_errors_are_results() -> None:
    fr, emb = frag(), Fake()
    index = build_index(fr, emb)
    q = SearchQuery("алфа", mode="semantic")
    for mode, code in (("short", "invalid_vector"), ("raise", "embedder_failed")):
        res = query_index(index, q, Fake(mode=mode), fr)
        assert res.status == "error" and res.error is not None and res.error.code == code


def test_corrupt_snapshot_rejected(tmp_path: Path) -> None:
    index = build_index(frag(), Fake())
    path = tmp_path / "s.json"
    save_index(index, path)
    good = path.read_bytes()
    data = json.loads(good)
    data["entries"][0]["vector"][0] = 99.0
    cases = [good[:50], b"", b"\xff\xfe", json.dumps(data).encode("utf-8"), b"[]"]
    for blob in cases:
        path.write_bytes(blob)
        with pytest.raises(SearchIndexError) as err:
            load_index(path)
        assert err.value.code == "corrupt_snapshot"
    with pytest.raises(SearchIndexError):
        load_index(tmp_path / "missing.json")


def test_embedder_change_detected_and_blocks() -> None:
    fr = frag()
    index = build_index(fr, Fake())
    for other in (Fake(config="c2"), Fake(dim=3)):
        status = index.assess(other.identity, fr)
        assert status.compatibility == "incompatible"
        res = query_index(index, SearchQuery("алфа", mode="semantic"), other, fr)
        assert res.status == "error" and res.error is not None
        assert res.error.code == "incompatible_index"


def test_representation_change_detected() -> None:
    emb = Fake()
    index = build_index(frag(), emb)
    status = index.assess(emb.identity, frag(max_chars=40))
    assert status.compatibility == "incompatible"
    assert "representation_version" in status.reasons


def test_schema_change_detected(tmp_path: Path) -> None:
    index = build_index(frag(), Fake())
    path = tmp_path / "s.json"
    save_index(index, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["corpus_schema"] = "search_corpus/0"
    data.pop("checksum")
    body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_index(path)
    assert loaded.assess(Fake().identity).compatibility == "incompatible"


def test_unverified_without_corpus_allows_queries() -> None:
    emb = Fake()
    index = build_index(frag(), emb)
    status = index.assess(emb.identity)
    assert (status.compatibility, status.freshness) == ("compatible", "unverified")
    assert query_index(index, SearchQuery("алфа", mode="semantic"), emb).status == "semantic"


def test_fresh_and_stale() -> None:
    emb = Fake()
    index = build_index(frag(), emb)
    assert index.assess(emb.identity, frag()).freshness == "fresh"
    changed = frag(SRC_A.replace("1", "7"))
    assert index.assess(emb.identity, changed).freshness == "stale"
    res = query_index(index, SearchQuery("алфа", mode="semantic"), emb, changed)
    assert res.status == "error" and res.error is not None
    assert res.error.code == "incompatible_index"


def test_filters_applied_before_top_k() -> None:
    emb, fr = Fake(), frag()
    index = build_index(fr, emb)
    top = query_index(index, SearchQuery("алфа", mode="semantic", top_k=1), emb, fr)
    assert top.hits[0].artifact == CAT
    flt = SearchFilters(metadata_types=("Document",))
    q = SearchQuery("алфа", mode="semantic", top_k=1, filters=flt)
    assert [h.artifact for h in query_index(index, q, emb, fr).hits] == [DOC]
    none = SearchFilters(metadata_types=("Report",))
    q2 = SearchQuery("алфа", mode="semantic", filters=none)
    assert query_index(index, q2, emb, fr).status == "empty"


def test_ties_are_deterministic() -> None:
    emb, fr = Fake(mode="same"), frag()
    index = build_index(fr, emb)
    q = SearchQuery("x", mode="semantic", top_k=5)
    ids = [h.document_id for h in query_index(index, q, emb, fr).hits]
    assert ids == sorted(ids)
    assert ids == [h.document_id for h in query_index(index, q, emb, fr).hits]


def test_atomic_write_keeps_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    emb = Fake()
    path = tmp_path / "s.json"
    save_index(build_index(frag(), emb), path)
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_index(build_index(frag(SRC_A + "\n"), emb), path)
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]
