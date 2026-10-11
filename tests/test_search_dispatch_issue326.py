"""Общий точный поиск и диспетчер смыслового fallback (issue #326).

Все данные синтетические; эмбеддеры — офлайн-заглушки. Хэшированный эмбеддер
проверяет механику маршрута, а не качество поиска.
"""
from __future__ import annotations

import hashlib
import json
import random
import unicodedata
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    SearchFilters,
    SearchQuery,
    SearchResult,
)
from v8unpack_agent.search_corpus import CorpusReport, SearchCorpus
from v8unpack_agent.search_dispatch import (
    DispatchResult,
    ExactCatalog,
    SearchDispatcher,
)
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import (
    EmbedderIdentity,
    IndexStatus,
    build_index,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DIM = 16
IDENTITY = EmbedderIdentity("hash-bow", "1", DIM, "c326")


def _module(kind: str, metadata_type: str, name: str, module_kind: str) -> ArtifactRef:
    return ArtifactRef(
        "module", OwnerRef(kind, metadata_type, name), module_kind
    )


def _owner(metadata_type: str, name: str) -> OwnerRef:
    return OwnerRef("metadata_object", metadata_type, name)


CAT_OBJ = _module("metadata_object", "Catalog", "Контрагенты", "object")
CAT_MGR = _module("metadata_object", "Catalog", "Контрагенты", "manager")
DOC_OBJ = _module("metadata_object", "Document", "Контрагенты", "object")
CONST = _module("metadata_object", "Constant", "ВалютаУчета", "value_manager")
CMD = _module(
    "metadata_object_command", "Catalog", "Контрагенты.Открыть", "command"
)
CAT_FORM = ArtifactRef.form(
    "Catalog", "Контрагенты", "CatalogForm", "ФормаСписка",
    owner=_owner("Catalog", "Контрагенты"),
)
NOM_FORM = ArtifactRef.form(
    "Catalog", "Номенклатура", "CatalogForm", "ФормаСписка",
    owner=_owner("Catalog", "Номенклатура"),
)
COMMON_FORM = ArtifactRef.form("CommonForm", "", "CommonForm", "ОбщаяФорма")
CARD = ArtifactRef.owner_card(_owner("Constant", "ВалютаУчета"))

TEXTS = {
    CAT_OBJ: "Процедура ПередЗаписью()\n  ПроверитьИНН();\nКонецПроцедуры\n",
    CAT_MGR: "Функция НайтиПоИНН(ИНН)\n  Возврат Неопределено;\nКонецФункции\n",
    DOC_OBJ: "Процедура ОбработкаПроведения()\n  Движения();\nКонецПроцедуры\n",
    CONST: "Процедура ПриЗаписи()\n  ПроверитьВалюту();\nКонецПроцедуры\n",
    CMD: "Процедура ОбработкаКоманды()\n  ОткрытьКарточку();\nКонецПроцедуры\n",
    CAT_FORM: "Процедура ПриОткрытии()\n  УстановитьОтбор();\nКонецПроцедуры\n",
    NOM_FORM: "Процедура ПриОткрытии()\n  ОбновитьЦены();\nКонецПроцедуры\n",
    COMMON_FORM: "Процедура ПриСоздании()\n  Сообщить();\nКонецПроцедуры\n",
    CARD: "OWNER\nowner_kind: metadata_object\nmetadata_type: Constant\n",
}


class HashEmbedder:
    """Мешок слов с хэшем SHA-256; первая координата — константа."""

    identity = IDENTITY

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for text in texts:
            vector = [0.0] * DIM
            vector[0] = 1.0
            for word in text.casefold().split():
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                vector[1 + digest[0] % (DIM - 1)] += 1.0
            out.append(vector)
        return out


class FailingEmbedder(HashEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("provider detail must not leak")


class NanEmbedder(HashEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[float("nan")] * DIM for _ in texts]


def corpus(order_seed: int | None = None) -> SearchCorpus:
    items = list(TEXTS.items())
    if order_seed is not None:
        random.Random(order_seed).shuffle(items)
    documents = SearchDocumentSet.from_documents(
        SearchDocument(artifact, text) for artifact, text in items
    )
    return SearchCorpus(documents=documents, owner_links=(), report=CorpusReport())


def setup(order_seed: int | None = None, **kwargs: Any):
    base = corpus(order_seed)
    fragmentation = fragment_corpus(base)
    embedder = HashEmbedder()
    index = build_index(fragmentation, embedder)
    embedder.calls = 0
    dispatcher = SearchDispatcher(
        ExactCatalog.from_corpus(base),
        index=kwargs.get("index", index),
        embedder=kwargs.get("embedder", embedder),
        fragmentation=kwargs.get("fragmentation"),
    )
    return dispatcher, embedder, base, fragmentation, index


def q(text: str, **kwargs: Any) -> SearchQuery:
    return SearchQuery(text, **kwargs)


def ids(out: DispatchResult) -> list[str]:
    return [hit.artifact.artifact_id for hit in out.result.hits]


# --- точное разрешение ---------------------------------------------------------


def test_exact_id_does_not_call_embedder() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q(CONST.artifact_id))
    assert out.result.status == "exact"
    hit = out.result.hits[0]
    assert (hit.method, hit.matched_field) == ("exact_id", "artifact_id")
    assert hit.similarity is None and hit.document_id is None
    assert out.path == ("exact",)
    assert out.index_status is None
    assert embedder.calls == 0


def test_exact_id_case_and_nfc_insensitive() -> None:
    dispatcher, embedder, *_ = setup()
    decomposed = unicodedata.normalize("NFD", CAT_FORM.artifact_id.upper())
    out = dispatcher.search(q("  " + decomposed + "  "))
    assert out.result.status == "exact"
    assert ids(out) == [CAT_FORM.artifact_id]
    assert embedder.calls == 0


def test_short_name_single_owner_is_exact_without_owner_card() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("валютаучета"))
    assert out.result.status == "exact"
    hit = out.result.hits[0]
    assert hit.artifact == CONST
    assert (hit.method, hit.matched_field) == ("exact_name", "owner_name")
    assert embedder.calls == 0


def test_owner_card_found_only_by_artifact_id() -> None:
    dispatcher, *_ = setup()
    out = dispatcher.search(q(CARD.artifact_id))
    assert out.result.status == "exact"
    assert ids(out) == [CARD.artifact_id]


def test_form_name_and_form_without_owner() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("ОбщаяФорма"))
    assert out.result.status == "exact"
    assert out.result.hits[0].matched_field == "form_name"
    assert ids(out) == [COMMON_FORM.artifact_id]
    assert embedder.calls == 0


def test_command_owner_name_requires_full_name() -> None:
    dispatcher, *_ = setup()
    out = dispatcher.search(q("Контрагенты.Открыть"))
    assert out.result.status == "exact"
    assert ids(out) == [CMD.artifact_id]
    miss = dispatcher.search(q("Открыть", mode="exact"))
    assert miss.result.status == "empty"


def test_artifact_id_has_priority_over_names() -> None:
    clash = ArtifactRef.form(
        "Catalog", "Прочее", "CatalogForm", CONST.artifact_id,
        owner=_owner("Catalog", "Прочее"),
    )
    catalog = ExactCatalog.from_artifacts([CONST, clash])
    out = SearchDispatcher(catalog).search(q(CONST.artifact_id))
    assert out.result.status == "exact"
    assert ids(out) == [CONST.artifact_id]


# --- неоднозначность ------------------------------------------------------------


def test_same_form_name_different_owners_is_ambiguous() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("ФормаСписка", top_k=1))
    assert out.result.status == "ambiguous"
    assert ids(out) == sorted(
        [CAT_FORM.artifact_id, NOM_FORM.artifact_id], key=str.casefold
    )
    assert {h.matched_field for h in out.result.hits} == {"form_name"}
    assert out.path == ("exact",)
    assert embedder.calls == 0


def test_short_owner_name_returns_all_candidates_not_top_k() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("Контрагенты", top_k=1))
    assert out.result.status == "ambiguous"
    assert set(ids(out)) == {
        CAT_OBJ.artifact_id, CAT_MGR.artifact_id, DOC_OBJ.artifact_id,
        CAT_FORM.artifact_id,
    }
    assert CARD.artifact_id not in ids(out)
    assert embedder.calls == 0


def test_filters_narrow_ambiguity_to_exact() -> None:
    dispatcher, *_ = setup()
    cats = dispatcher.search(
        q("Контрагенты", filters=SearchFilters(metadata_types=("Catalog",)))
    )
    assert cats.result.status == "ambiguous"
    assert len(cats.result.hits) == 3
    assert cats.exact_filtered_out == 1
    forms = dispatcher.search(
        q("Контрагенты", filters=SearchFilters(artifact_kinds=("form",)))
    )
    assert forms.result.status == "exact"
    assert ids(forms) == [CAT_FORM.artifact_id]
    assert forms.exact_filtered_out == 3


# --- свободный текст и fallback ------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "форма ФормаСписка контрагента",
        "Контрагенты ИНН",
        "Валюта Учета",
        "ЗарплатаНачисление",
        "ФормаСпис",
    ],
)
def test_free_text_with_name_is_not_exact(text: str) -> None:
    dispatcher, embedder, *_ = setup()
    exact_only = dispatcher.search(q(text, mode="exact"))
    assert exact_only.result.status == "empty"
    assert embedder.calls == 0
    auto = dispatcher.search(q(text, top_k=3))
    assert auto.result.status in ("semantic", "empty")
    assert auto.path == ("exact", "semantic")
    assert all(h.method == "semantic" for h in auto.result.hits)
    assert embedder.calls == 1


def test_semantic_hits_are_marked_and_not_probability() -> None:
    dispatcher, *_ = setup()
    out = dispatcher.search(q("проверить инн контрагента", top_k=4))
    assert out.result.status == "semantic"
    assert out.semantic_used
    assert 1 <= len(out.result.hits) <= 4
    names = ids(out)
    assert len(names) == len(set(names))
    for hit in out.result.hits:
        assert hit.method == "semantic" and hit.matched_field is None
        assert isinstance(hit.similarity, float)
        assert hit.document_id is not None
        assert hit.document_id.startswith(hit.artifact.artifact_id)
    scores = [h.similarity for h in out.result.hits]
    assert scores == sorted(scores, reverse=True)


def test_ambiguous_in_auto_has_no_fallback() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("ФормаСписка"))
    assert out.result.status == "ambiguous"
    assert not out.semantic_used
    assert embedder.calls == 0


def test_filtered_exact_miss_falls_back_with_same_filters() -> None:
    dispatcher, embedder, *_ = setup()
    filters = SearchFilters(metadata_types=("Document",))
    out = dispatcher.search(q("ВалютаУчета", filters=filters))
    assert out.exact_filtered_out == 1
    assert out.path == ("exact", "semantic")
    assert out.result.status in ("semantic", "empty")
    assert CONST.artifact_id not in ids(out)
    assert all(filters.matches(h.artifact) for h in out.result.hits)
    assert embedder.calls == 1


def test_filtered_exact_miss_in_exact_mode_is_empty() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(
        q("ВалютаУчета", mode="exact",
          filters=SearchFilters(metadata_types=("Document",)))
    )
    assert out.result.status == "empty"
    assert out.exact_filtered_out == 1
    assert embedder.calls == 0


def test_direct_semantic_mode_skips_exact() -> None:
    dispatcher, embedder, *_ = setup()
    out = dispatcher.search(q("ВалютаУчета", mode="semantic", top_k=2))
    assert out.result.status == "semantic"
    assert out.path == ("semantic",)
    assert all(h.method == "semantic" for h in out.result.hits)
    assert out.exact_filtered_out == 0
    assert embedder.calls == 1


def test_paths_never_mix_exact_and_semantic_hits() -> None:
    dispatcher, *_ = setup()
    for text in ("Контрагенты", "ВалютаУчета", "проверить инн", "ФормаСписка"):
        for mode in ("auto", "semantic"):
            out = dispatcher.search(q(text, mode=mode, top_k=5))
            methods = {h.method for h in out.result.hits}
            assert not ({"semantic"} & methods and {"exact_id", "exact_name"} & methods)
            names = ids(out)
            assert len(names) == len(set(names))


# --- индекс, эмбеддер и свежесть ------------------------------------------------


def test_index_status_visible_unverified_and_fresh() -> None:
    dispatcher, _, _, fragmentation, _ = setup()
    out = dispatcher.search(q("проверить валюту", mode="semantic"))
    assert out.index_status == IndexStatus("compatible", "unverified", ())
    fresh, *_ = setup(fragmentation=fragmentation)
    out = fresh.search(q("проверить валюту", mode="semantic"))
    assert out.index_status is not None
    assert out.index_status.freshness == "fresh"


@pytest.mark.parametrize("missing", ["index", "embedder"])
def test_no_index_or_embedder_is_index_not_ready(missing: str) -> None:
    dispatcher, *_ = setup(**{missing: None})
    for mode in ("auto", "semantic"):
        out = dispatcher.search(q("проверить инн", mode=mode))
        assert out.result.status == "error"
        assert out.result.error is not None
        assert out.result.error.code == "index_not_ready"
        assert out.index_status is None
    exact = dispatcher.search(q("ВалютаУчета"))
    assert exact.result.status == "exact"


def test_embedder_failure_and_invalid_vector() -> None:
    failing = FailingEmbedder()
    dispatcher, *_ = setup(embedder=failing)
    exact = dispatcher.search(q("ВалютаУчета"))
    assert exact.result.status == "exact"
    assert failing.calls == 0
    out = dispatcher.search(q("проверить инн"))
    assert out.result.error is not None
    assert out.result.error.code == "embedder_failed"
    assert "provider" not in out.result.to_json()
    bad, *_ = setup(embedder=NanEmbedder())
    out = bad.search(q("проверить инн", mode="semantic"))
    assert out.result.error is not None
    assert out.result.error.code == "invalid_vector"


def test_stale_index_blocks_semantic_but_not_exact() -> None:
    changed = dict(TEXTS)
    changed[CONST] = "Процедура ПриЗаписи()\n  Изменено();\nКонецПроцедуры\n"
    documents = SearchDocumentSet.from_documents(
        SearchDocument(a, t) for a, t in changed.items()
    )
    stale_fragments = fragment_corpus(
        SearchCorpus(documents=documents, owner_links=(), report=CorpusReport())
    )
    dispatcher, *_ = setup(fragmentation=stale_fragments)
    out = dispatcher.search(q("проверить инн"))
    assert out.result.status == "error"
    assert out.result.error is not None
    assert out.result.error.code == "incompatible_index"
    assert out.index_status is not None
    assert (out.index_status.compatibility, out.index_status.freshness) == (
        "compatible", "stale",
    )
    assert dispatcher.search(q("ВалютаУчета")).result.status == "exact"


def test_incompatible_embedder_identity_blocks_semantic() -> None:
    class Other(HashEmbedder):
        identity = EmbedderIdentity("hash-bow", "2", DIM, "c326")

    other = Other()
    dispatcher, *_ = setup(embedder=other)
    out = dispatcher.search(q("проверить инн", mode="semantic"))
    assert out.result.error is not None
    assert out.result.error.code == "incompatible_index"
    assert out.index_status is not None
    assert out.index_status.compatibility == "incompatible"
    assert other.calls == 0


# --- детерминизм, неизменность и контракт результата ----------------------------


def test_order_and_results_are_deterministic() -> None:
    first, *_ = setup(order_seed=1)
    second, *_ = setup(order_seed=2)
    assert first.catalog == second.catalog
    for text in ("Контрагенты", "ФормаСписка", "проверить инн контрагента"):
        left = first.search(q(text, top_k=3)).to_dict()
        right = second.search(q(text, top_k=3)).to_dict()
        assert left == right
        assert json.dumps(left, ensure_ascii=False, sort_keys=True)


def test_inputs_and_results_are_not_mutated() -> None:
    dispatcher, _, base, fragmentation, index = setup()
    before = (base.to_json(), fragmentation.to_json(), index.to_json())
    catalog_before = dispatcher.catalog.artifacts
    query = q("Контрагенты", filters=SearchFilters(metadata_types=("Catalog",)))
    query_before = query.to_dict()
    result = dispatcher.search(query)
    snapshot = result.to_dict()
    dispatcher.search(q("проверить инн", top_k=3))
    assert (base.to_json(), fragmentation.to_json(), index.to_json()) == before
    assert dispatcher.catalog.artifacts == catalog_before
    assert query.to_dict() == query_before
    assert result.to_dict() == snapshot
    frozen: Any = result
    with pytest.raises(FrozenInstanceError):
        frozen.path = ("semantic",)


def test_catalog_rejects_conflicts_and_collapses_repeats() -> None:
    other = ArtifactRef.form(
        "Catalog", "Контрагенты", "CatalogForm", "ФормаСписка",
        owner=_owner("Catalog", "Контрагенты"),
        source_path="Catalog/Контрагенты/CatalogForm/ФормаСписка",
    )
    with pytest.raises(ValueError):
        ExactCatalog.from_artifacts([CAT_FORM, other])
    assert len(ExactCatalog.from_artifacts([CAT_FORM, CAT_FORM])) == 1
    bad: Any = "form"
    with pytest.raises(TypeError):
        ExactCatalog.from_artifacts([bad])


def test_dispatch_result_rejects_inconsistent_route() -> None:
    query = q("x")
    exact = SearchResult.exact(
        query, SearchDispatcher(ExactCatalog.from_artifacts([CONST])).search(
            q(CONST.artifact_id)
        ).result.hits[0],
    )
    status = IndexStatus("compatible", "unverified")
    cases = [
        lambda: DispatchResult(exact, ("exact", "semantic")),
        lambda: DispatchResult(exact, ("semantic", "exact")),
        lambda: DispatchResult(SearchResult.empty(query), ("exact",), status),
        lambda: DispatchResult(SearchResult.empty(query), ("semantic",), None, 2),
        lambda: DispatchResult(SearchResult.empty(query), ("exact",), None, -1),
    ]
    for case in cases:
        with pytest.raises(ValueError):
            case()


def test_semantic_dispatch_returns_contract_result() -> None:
    dispatcher, *_ = setup()
    out = dispatcher.search(q("проверить валюту", top_k=2))
    restored = SearchResult.from_dict(json.loads(out.result.to_json()))
    assert restored == out.result


# --- совместимость и документация -------------------------------------------------


def test_legacy_form_routing_api_is_unchanged() -> None:
    import v8unpack_agent
    from v8unpack_agent.form_dispatcher import FormDispatcher
    from v8unpack_agent.form_router import RouteResult

    assert "SearchDispatcher" not in v8unpack_agent.__all__
    assert "ExactCatalog" not in v8unpack_agent.__all__
    assert RouteResult([], 0.0).source == "router"
    assert callable(FormDispatcher.dispatch)


def test_docs_and_changelog_reference_issue() -> None:
    doc = (REPO_ROOT / "docs" / "search_dispatch.md").read_text(encoding="utf-8")
    for name in ("SearchDispatcher", "ExactCatalog", "DispatchResult", "#329"):
        assert name in doc
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "(#326)" in changelog
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/search_dispatch.md" in readme
