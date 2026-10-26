"""Source-context contract checks for #327; synthetic inputs only."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from v8unpack_agent.search_context import build_search_context
from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchDocument,
    SearchHit,
    SearchQuery,
    SearchResult,
    TextSpan,
)
from v8unpack_agent.search_dispatch import DispatchResult
from v8unpack_agent.search_fragments import (
    FragmentationResult,
    FragmentParameters,
    SearchFragment,
    fragment_module,
)

ARTIFACT = ArtifactRef(
    "module", OwnerRef("metadata_object", "Catalog", "Demo"),
    "object", source_path="Catalog/Demo/Catalog.obj.bsl",
)


def exact(artifact: ArtifactRef = ARTIFACT) -> DispatchResult:
    query = SearchQuery(artifact.artifact_id, mode="exact")
    hit = SearchHit(artifact, "exact_id", matched_field="artifact_id")
    return DispatchResult(SearchResult.exact(query, hit), ("exact",))


def test_full_source_and_default_serialization() -> None:
    source = "Procedure Run()\nMessage(1);\nEndProcedure\n"
    fragments = fragment_module(ARTIFACT, source)
    before = fragments.to_json(include_text=True)
    context = build_search_context(exact(), fragments, max_chars=-1)
    assert context.status == "built"
    assert context.search_kind == "exact"
    assert "".join(b.text for b in context.included) == source
    assert context.included[0].included_span == TextSpan(1, 3)
    assert context.included[0].source_document_ids == (
        fragments.fragments[0].document.document_id,
    )
    assert "text" not in context.to_dict()
    assert "text" not in context.to_dict()["included"][0]
    assert source in context.text
    assert json.loads(context.to_json(include_text=True))["text"] == context.text
    assert fragments.to_json(include_text=True) == before


@pytest.mark.parametrize("limit", [0, 1, 250, 800, 1200, 1800, 3000, 6000])
def test_character_bounds(limit: int) -> None:
    fragments = fragment_module(ARTIFACT, "value = 1;\n" * 300)
    context = build_search_context(exact(), fragments, max_chars=limit)
    assert len(context.text) <= limit
    for block in context.included:
        span = block.included_span
        assert span is not None
        assert block.text == "value = 1;\n" * (
            span.end_line - span.start_line + 1
        )
        original_ids = {f.document.document_id for f in fragments.fragments}
        assert set(block.source_document_ids) <= original_ids


def test_overlapping_windows_are_not_repeated() -> None:
    source = "value = 1;\n" * 50
    fragments = fragment_module(
        ARTIFACT, source,
        parameters=FragmentParameters(max_chars=80, overlap_lines=3),
    )
    context = build_search_context(exact(), fragments, max_chars=-1)
    assert context.status == "built"
    assert "".join(b.text for b in context.included) == source
    assert context.included[0].included_span == TextSpan(1, 50)


@pytest.mark.parametrize("value", [True, -1, 1.5, None])
def test_bad_token_counts_fail_closed(value: object) -> None:
    fragments = fragment_module(ARTIFACT, "value = 1;\n")
    context = build_search_context(
        exact(), fragments, max_chars=-1, max_tokens=100000,
        count_tokens=lambda _: value,
    )
    assert context.status == "token_count_failed"
    assert context.text == ""
    assert context.included == ()


def test_token_exception_is_not_exposed() -> None:
    def broken(_: str) -> int:
        raise RuntimeError("private exception detail")
    context = build_search_context(
        exact(), fragment_module(ARTIFACT, "value = 1;\n"),
        max_chars=-1, max_tokens=100, count_tokens=broken,
    )
    assert context.status == "token_count_failed"
    assert "private exception detail" not in context.to_json(include_text=True)


def test_token_budget_covers_metadata() -> None:
    context = build_search_context(
        exact(), fragment_module(ARTIFACT, "value = 1;\n" * 200),
        max_chars=-1, max_tokens=2000, count_tokens=len,
    )
    assert len(context.text) <= 2000


def test_ambiguous_remains_ambiguous_and_top_k_does_not_select() -> None:
    other = replace(ARTIFACT, module_kind="manager")
    hits = tuple(SearchHit(a, "exact_name", matched_field="owner_name")
                 for a in (ARTIFACT, other))
    query = SearchQuery("Demo", top_k=1)
    dispatch = DispatchResult(SearchResult.ambiguous(query, hits), ("exact",))
    context = build_search_context(dispatch, None, max_chars=-1)
    assert context.search_kind == "ambiguous"
    assert context.candidates_total == context.candidates_shown == 2
    assert all(block.candidate_only for block in context.included)
    assert "alternatives" in context.text


def test_semantic_uses_hit_document_only() -> None:
    fragments = fragment_module(
        ARTIFACT, "value = 1;\n" * 30,
        parameters=FragmentParameters(max_chars=50, overlap_lines=0),
    )
    document = fragments.fragments[-1].document
    hit = SearchHit(
        ARTIFACT, "semantic", document_id=document.document_id, similarity=0.7
    )
    result = SearchResult.semantic(SearchQuery("anything", mode="semantic"), [hit])
    dispatch = DispatchResult(result, ("semantic",))
    context = build_search_context(dispatch, fragments, max_chars=-1)
    assert "".join(b.text for b in context.included) == document.text
    assert context.included[0].included_span == document.span
    assert "not a probability" in context.text
    with pytest.raises(ValueError, match="requires fragmentation"):
        build_search_context(dispatch, None, max_chars=-1)


def test_missing_source_is_not_empty_search() -> None:
    context = build_search_context(exact(), None, max_chars=-1)
    assert context.search_kind == "exact"
    assert context.status == "truncated"
    assert context.omitted[0].reason == "source_missing"


@pytest.mark.parametrize("failed", [False, True])
def test_empty_and_error_remain_distinct(failed: bool) -> None:
    query = SearchQuery("none", mode="semantic")
    result = (SearchResult.failed(query, "index_not_ready") if failed
              else SearchResult.empty(query))
    dispatch = DispatchResult(result, ("semantic",))
    context = build_search_context(dispatch, None, max_chars=-1)
    assert context.search_kind == ("error" if failed else "empty")
    assert context.included == ()
    assert context.status == "built"


def test_overlap_conflict_fails_closed_for_artifact() -> None:
    a = SearchDocument(ARTIFACT, "a\nb\n", span=TextSpan(1, 2))
    b = SearchDocument(ARTIFACT, "WRONG\nc\n", span=TextSpan(2, 3))
    fragments = FragmentationResult(FragmentParameters(), (
        SearchFragment(a, "projection", "window"),
        SearchFragment(b, "projection", "window"),
    ), ())
    context = build_search_context(exact(), fragments, max_chars=-1)
    assert context.included == ()
    assert context.omitted[0].reason == "source_mismatch"
    assert "WRONG" not in context.text


def test_source_projection_not_used() -> None:
    source = fragment_module(ARTIFACT, "value = 1;\n")
    fragments = replace(source, fragments=tuple(
        replace(f, search_text="NOT THE SOURCE") for f in source.fragments
    ))
    context = build_search_context(exact(), fragments, max_chars=-1)
    assert "NOT THE SOURCE" not in context.text
    assert context.included[0].text == "value = 1;\n"


def test_no_span_is_atomic() -> None:
    document = SearchDocument(ARTIFACT, "source\n" * 1000)
    fragments = FragmentationResult(FragmentParameters(), (
        SearchFragment(document, "projection", "window"),
    ), ())
    context = build_search_context(exact(), fragments, max_chars=2000)
    assert context.included == ()
    assert context.omitted[0].reason == "no_span_atomic"


@pytest.mark.parametrize("limit", [True, -2, 1.5])
def test_invalid_character_budget(limit: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_search_context(exact(), None, max_chars=limit)
