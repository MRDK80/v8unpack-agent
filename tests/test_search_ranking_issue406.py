"""Быстрое смысловое ранжирование без изменения результата (#406).

Путь с подготовленной матрицей numpy сравнивается с прежним поэлементным
путём (numpy отключается в модуле) на индексах с равенствами оценок,
фильтрами и разными ``top_k``: ``SearchResult`` должен совпадать целиком,
включая оценки. Отдельно — однократная подготовка: сигнатура свежести,
``assess`` и матрица не пересчитываются повторными запросами, а кэш не
влияет на равенство и снимок. Жёстких таймингов нет.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_index
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchFilters,
    SearchQuery,
    SearchResult,
)
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import (
    EmbedderIdentity,
    IndexEntry,
    SearchIndex,
    build_index,
    load_index,
    query_index,
    save_index,
)

np = pytest.importorskip("numpy")

H = "0" * 64
DIM = 6
IDENTITY = EmbedderIdentity("fake406", "1", DIM, "c406")
TYPES = ("Catalog", "Document", "Report")


class FixedEmbedder:
    """Возвращает заданный вектор запроса."""

    identity = IDENTITY

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


def _artifact(number: int) -> ArtifactRef:
    owner = OwnerRef("metadata_object", TYPES[number % len(TYPES)], f"Объект{number:03d}")
    return ArtifactRef("module", owner, ("object", "manager")[number % 2])


def _index(seed: int, artifacts: int, per_artifact: int, levels: int) -> SearchIndex:
    """Векторы из малого набора значений: много точных равенств оценок."""
    rng = random.Random(seed)
    entries = []
    for a in range(artifacts):
        artifact = _artifact(a)
        for p in range(rng.randint(1, per_artifact)):
            vector = [float(rng.randint(-levels, levels)) for _ in range(DIM)]
            if not any(vector):
                vector[0] = 1.0
            entries.append(IndexEntry(
                f"{artifact.artifact_id}#П{p:02d}", artifact, f"П{p:02d}", None,
                H, H, H, tuple(vector),
            ))
    rng.shuffle(entries)
    return SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, tuple(entries))


def _both(
    monkeypatch: pytest.MonkeyPatch, index: SearchIndex, query: SearchQuery, embedder: Any
) -> tuple[SearchResult, SearchResult]:
    fast = query_index(index, query, embedder)
    with monkeypatch.context() as patched:
        patched.setattr(search_index, "_np", None)
        slow = query_index(index, query, embedder)
    return fast, slow


FILTERS = (
    SearchFilters(),
    SearchFilters(metadata_types=("Document",)),
    SearchFilters(metadata_types=("Catalog", "Report")),
    SearchFilters(module_kinds=("manager",)),
    SearchFilters(metadata_types=("Absent",)),
)


@pytest.mark.parametrize("seed", range(6))
def test_matrix_path_identical_to_python_path(
    monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    index = _index(seed, artifacts=40, per_artifact=6, levels=1 + seed % 3)
    rng = random.Random(1000 + seed)
    for _ in range(15):
        vector = [float(rng.randint(-2, 2)) for _ in range(DIM)]
        if not any(vector):
            vector[1] = 1.0
        for filters in FILTERS:
            for top_k in (1, 2, 3, 7, 40, 100):
                query = SearchQuery("запрос", mode="semantic", top_k=top_k, filters=filters)
                fast, slow = _both(monkeypatch, index, query, FixedEmbedder(vector))
                assert fast == slow, (seed, vector, filters, top_k)
                assert fast.to_dict() == slow.to_dict()


def test_float_vectors_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(406)
    entries = []
    for a in range(60):
        artifact = _artifact(a)
        for p in range(4):
            vector = tuple(rng.gauss(0.0, 1.0) for _ in range(DIM))
            entries.append(IndexEntry(
                f"{artifact.artifact_id}#П{p}", artifact, f"П{p}", None, H, H, H, vector,
            ))
    index = SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, tuple(entries))
    for _ in range(30):
        vector = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
        for top_k in (1, 5, 60):
            query = SearchQuery("q", mode="semantic", top_k=top_k)
            fast, slow = _both(monkeypatch, index, query, FixedEmbedder(vector))
            assert fast == slow


def test_errors_and_empty_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    index = _index(7, artifacts=5, per_artifact=2, levels=1)
    empty_index = SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, ())
    cases = [
        (index, SearchQuery("q", mode="semantic"), FixedEmbedder([0.0] * DIM)),
        (index, SearchQuery("q", mode="semantic"), FixedEmbedder([1.0] * (DIM - 1))),
        (index, SearchQuery("q", mode="exact"), FixedEmbedder([1.0] * DIM)),
        (empty_index, SearchQuery("q", mode="semantic"), FixedEmbedder([1.0] * DIM)),
    ]
    for idx, query, embedder in cases:
        fast, slow = _both(monkeypatch, idx, query, embedder)
        assert fast == slow


def test_without_numpy_previous_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_index, "_np", None)
    index = _index(3, artifacts=10, per_artifact=3, levels=2)
    query = SearchQuery("q", mode="semantic", top_k=4)
    result = query_index(index, query, FixedEmbedder([1.0, 0, 0, 0, 0, 1.0]))
    assert result.status == "semantic"
    assert "matrix" not in index._cache


FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n"
        "Процедура ПриЗаписи()\n  Остаток = 2;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриОткрытии()\n  ЦенаТовара = 1;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.json": "{}",
    "Document/Заказ/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриСозданииНаСервере()\n  СуммаЗаказа = 0;\nКонецПроцедуры\n"
    ),
    "Document/Заказ/Form/ФормаЭлемента/Form.json": "{}",
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура ОбщаяПроцедура() Экспорт\n  Цена = 0;\nКонецПроцедуры\n"
    ),
}


@pytest.fixture
def fragmentation(tmp_path: Path) -> Any:
    root = tmp_path / "export-406"
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return fragment_corpus(collect_search_corpus(root))


def test_demo_embedder_context_identical(
    monkeypatch: pytest.MonkeyPatch, fragmentation: Any
) -> None:
    embedder = HashedBagOfWordsDemoEmbedder()
    index = build_index(fragmentation, embedder)
    for text in ("цена товара", "сумма заказа", "запись", "нет совпадений"):
        for top_k in (1, 2, 5):
            kwargs: dict[str, Any] = {
                "search_mode": "semantic", "top_k": top_k, "index": index,
                "embedder": embedder, "max_chars": -1,
            }
            fast = search_with_context(fragmentation, text, **kwargs)
            with monkeypatch.context() as patched:
                patched.setattr(search_index, "_np", None)
                slow = search_with_context(fragmentation, text, **kwargs)
            assert fast.to_dict(include_text=True) == slow.to_dict(include_text=True)


def test_preparation_happens_once(
    monkeypatch: pytest.MonkeyPatch, fragmentation: Any
) -> None:
    embedder = HashedBagOfWordsDemoEmbedder()
    index = build_index(fragmentation, embedder)
    calls = {"signature": 0, "assess": 0, "matrix": 0}
    original_signature = search_index._compute_signature
    original_assess = SearchIndex._assess
    original_asarray = np.asarray

    def signature(result: Any) -> Any:
        calls["signature"] += 1
        return original_signature(result)

    def assess(self: Any, *args: Any) -> Any:
        calls["assess"] += 1
        return original_assess(self, *args)

    def asarray(value: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            calls["matrix"] += 1
        return original_asarray(value, *args, **kwargs)

    monkeypatch.setattr(search_index, "_compute_signature", signature)
    monkeypatch.setattr(SearchIndex, "_assess", assess)
    monkeypatch.setattr(np, "asarray", asarray)
    for text in ("цена", "заказ", "запись") * 3:
        context = search_with_context(
            fragmentation, text, search_mode="semantic", index=index,
            embedder=embedder, max_chars=-1,
        )
        assert context.dispatch.result.status == "semantic"
        assert context.dispatch.index_status is not None
        assert context.dispatch.index_status.freshness == "fresh"
    assert calls == {"signature": 1, "assess": 1, "matrix": 1}


def test_cache_invisible_to_equality_and_snapshot(
    tmp_path: Path, fragmentation: Any
) -> None:
    embedder = HashedBagOfWordsDemoEmbedder()
    warm = build_index(fragmentation, embedder)
    cold = build_index(fragmentation, embedder)
    query = SearchQuery("цена", mode="semantic")
    query_index(warm, query, embedder, fragmentation)
    assert warm._cache and not cold._cache
    assert warm == cold
    assert warm.to_json() == cold.to_json()
    assert "_cache" not in repr(warm)
    save_index(warm, tmp_path / "warm.json")
    save_index(cold, tmp_path / "cold.json")
    assert (tmp_path / "warm.json").read_bytes() == (tmp_path / "cold.json").read_bytes()
    assert load_index(tmp_path / "warm.json") == warm


def test_stale_still_blocks_after_cached_fresh(fragmentation: Any, tmp_path: Path) -> None:
    embedder = HashedBagOfWordsDemoEmbedder()
    index = build_index(fragmentation, embedder)
    query = SearchQuery("цена", mode="semantic")
    assert query_index(index, query, embedder, fragmentation).status == "semantic"
    root = tmp_path / "changed"
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("Цена", "Стоимость").encode("utf-8"))
    changed = fragment_corpus(collect_search_corpus(root))
    assert index.assess(embedder.identity, changed).freshness == "stale"
    result = query_index(index, query, embedder, changed)
    assert result.status == "error" and result.error is not None
    assert result.error.code == "incompatible_index"
    other = EmbedderIdentity("other", "1", 256, "x")
    assert index.assess(other, fragmentation).compatibility == "incompatible"
    assert index.assess(embedder.identity, fragmentation).freshness == "fresh"
