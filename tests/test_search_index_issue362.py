"""Офлайн-тесты уникальности артефактов в выдаче query_index (#362)."""
from __future__ import annotations

from collections.abc import Sequence

from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchFilters,
    SearchQuery,
)
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_fragments import (
    FragmentationResult,
    FragmentParameters,
    fragment_module,
)
from v8unpack_agent.search_index import (
    SEARCH_INDEX_SCHEMA,
    EmbedderIdentity,
    IndexEntry,
    SearchIndex,
    build_index,
    query_index,
)

H = "0" * 64
IDENTITY = EmbedderIdentity("fake", "1", 2, "c362")


def art(metadata_type: str, name: str) -> ArtifactRef:
    return ArtifactRef("module", OwnerRef("metadata_object", metadata_type, name), "object")


CAT_A = art("Catalog", "Альфа")
CAT_B = art("Catalog", "Бета")
CAT_C = art("Catalog", "Гамма")
DOC_D = art("Document", "Дельта")
DOC_E = art("Document", "Эпсилон")


class QueryEmbedder:
    """Запрос всегда (1, 0): оценка записи (1, y) равна 1/sqrt(1 + y*y)."""

    identity = IDENTITY

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def entry(artifact: ArtifactRef, procedure: str, y: float) -> IndexEntry:
    return IndexEntry(
        f"{artifact.artifact_id}#{procedure}", artifact, procedure, None,
        H, H, H, (1.0, y),
    )


def make_index(entries: Sequence[IndexEntry]) -> SearchIndex:
    return SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, tuple(entries))


def run(index: SearchIndex, top_k: int, filters: SearchFilters | None = None):
    query = SearchQuery(
        "запрос", mode="semantic", top_k=top_k,
        filters=filters if filters is not None else SearchFilters(),
    )
    return query_index(index, query, QueryEmbedder())


def ids(result) -> list[str | None]:
    return [h.document_id for h in result.hits]


def artifacts(result) -> list[str]:
    return [h.artifact.artifact_id for h in result.hits]


MULTI = (
    entry(CAT_A, "П1", 0.0),
    entry(CAT_A, "П2", 0.1),
    entry(CAT_A, "П3", 0.2),
    entry(CAT_B, "П1", 0.5),
    entry(CAT_B, "П2", 0.3),
    entry(CAT_C, "П1", 0.9),
    entry(DOC_D, "П1", 0.05),
    entry(DOC_D, "П2", 0.4),
    entry(DOC_E, "П1", 1.5),
)


def test_regression_real_fragments_no_value_error() -> None:
    src = (
        "Процедура Алфа()\n  Сообщить(1);\nКонецПроцедуры\n\n"
        "Процедура АлфаБета()\n  Сообщить(2);\nКонецПроцедуры\n\n"
        "Процедура Гамма()\n  Сообщить(3);\nКонецПроцедуры\n"
    )
    params = FragmentParameters()
    first = fragment_module(CAT_A, src, parameters=params)
    second = fragment_module(
        DOC_D, "Процедура Бета()\n  Сообщить(4);\nКонецПроцедуры\n", parameters=params
    )
    fr = FragmentationResult(
        params, first.fragments + second.fragments, first.items + second.items
    )
    assert len({f.document.artifact.identity for f in fr.fragments}) == 2
    assert len(fr.fragments) > 2

    class Words:
        identity = IDENTITY

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 1.0 + t.lower().count("алфа")] for t in texts]

    index = build_index(fr, Words())
    for top_k in (2, 5):
        res = query_index(index, SearchQuery("алфа", mode="semantic", top_k=top_k),
                          Words(), fr)
        assert res.status == "semantic"
        names = artifacts(res)
        assert len(names) == len(set(names)) == 2


def test_no_duplicate_artifacts_and_count_equals_top_k() -> None:
    index = make_index(MULTI)
    for top_k in (1, 2, 3, 4, 5):
        res = run(index, top_k)
        assert res.status == "semantic"
        names = artifacts(res)
        assert len(names) == len(set(names)) == top_k


def test_top_k_counted_after_dedup() -> None:
    res = run(make_index(MULTI), 3)
    assert artifacts(res) == [
        CAT_A.artifact_id, DOC_D.artifact_id, CAT_B.artifact_id,
    ]


def test_top_k_larger_than_artifacts() -> None:
    res = run(make_index(MULTI), 50)
    assert len(res.hits) == 5
    assert len(set(artifacts(res))) == 5


def test_best_fragment_selected() -> None:
    res = run(make_index(MULTI), 5)
    by_artifact = {h.artifact.artifact_id: h for h in res.hits}
    assert by_artifact[CAT_A.artifact_id].document_id == f"{CAT_A.artifact_id}#П1"
    assert by_artifact[CAT_B.artifact_id].document_id == f"{CAT_B.artifact_id}#П2"
    assert by_artifact[DOC_D.artifact_id].document_id == f"{DOC_D.artifact_id}#П1"
    assert by_artifact[CAT_A.artifact_id].similarity == 1.0
    sims = [h.similarity for h in res.hits]
    assert sims == sorted(sims, reverse=True)


def test_equal_fragment_scores_resolved_by_document_id() -> None:
    tied = (entry(CAT_A, "Яблоко", 0.3), entry(CAT_A, "Ананас", 0.3),
            entry(CAT_A, "Груша", 0.3), entry(CAT_B, "П1", 0.7))
    forward = run(make_index(tied), 2)
    backward = run(make_index(tuple(reversed(tied))), 2)
    assert forward == backward
    assert ids(forward)[0] == f"{CAT_A.artifact_id}#Ананас"


def test_equal_artifact_scores_cut_deterministically() -> None:
    tied = (entry(CAT_C, "П1", 0.2), entry(CAT_A, "П1", 0.2),
            entry(CAT_B, "П1", 0.2), entry(CAT_B, "П2", 0.2))
    expected = sorted(f"{a.artifact_id}#П1" for a in (CAT_A, CAT_B, CAT_C))[:2]
    for order in (tied, tuple(reversed(tied))):
        res = run(make_index(order), 2)
        assert sorted(ids(res)) == expected


def test_filters_before_selection_and_respected() -> None:
    index = make_index(MULTI)
    docs = SearchFilters(metadata_types=("Document",))
    res = run(index, 2, docs)
    assert artifacts(res) == [DOC_D.artifact_id, DOC_E.artifact_id]
    assert all(docs.matches(h.artifact) for h in res.hits)
    cats = SearchFilters(metadata_types=("Catalog",))
    res = run(index, 5, cats)
    assert len(res.hits) == 3
    assert all(cats.matches(h.artifact) for h in res.hits)


def test_empty_after_filters() -> None:
    res = run(make_index(MULTI), 5, SearchFilters(metadata_types=("Report",)))
    assert res.status == "empty" and res.hits == ()


def test_index_not_mutated() -> None:
    index = make_index(MULTI)
    before = index.to_json()
    run(index, 3)
    assert index.to_json() == before
    assert index.schema == SEARCH_INDEX_SCHEMA


def test_stale_and_incompatible_still_block_with_top_k_above_one() -> None:
    params = FragmentParameters()
    src = "Процедура А()\nКонецПроцедуры\n\nПроцедура Б()\nКонецПроцедуры\n"
    first = fragment_module(CAT_A, src, parameters=params)
    fr = FragmentationResult(params, first.fragments, first.items)
    index = build_index(fr, QueryEmbedder())
    query = SearchQuery("а", mode="semantic", top_k=5)
    changed_src = src.replace("Б()", "В()")
    changed = fragment_module(CAT_A, changed_src, parameters=params)
    stale = FragmentationResult(params, changed.fragments, changed.items)
    assert index.assess(IDENTITY, stale).freshness == "stale"
    res = query_index(index, query, QueryEmbedder(), stale)
    assert res.status == "error" and res.error is not None
    assert res.error.code == "incompatible_index"

    class Other(QueryEmbedder):
        identity = EmbedderIdentity("fake", "1", 2, "other")

    res = query_index(index, query, Other(), fr)
    assert res.status == "error" and res.error is not None
    assert res.error.code == "incompatible_index"
