"""Контракт общего поиска: документ, запрос, результат, связь (issue #322).

Все данные синтетические. Абсолютных путей и литеральных разделителей
Windows в тестах нет: обратная косая черта собирается через ``chr(92)``.
"""

from __future__ import annotations

import json
import random
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.drift_checker import form_key
from v8unpack_agent.modules import ModuleEntry, ModuleIndex
from v8unpack_agent.search_contract import (
    SEARCH_CONTRACT_SCHEMA,
    ArtifactRef,
    OwnerLink,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    SearchError,
    SearchFilters,
    SearchHit,
    SearchQuery,
    SearchResult,
    TextSpan,
    encode_id_segment,
    identity_key,
    validate_relative_source_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH = REPO_ROOT / "docs" / "research"
BS = chr(92)


def entry(**overrides: Any) -> ModuleEntry:
    fields: dict[str, Any] = {
        "module_kind": "object",
        "owner_kind": "metadata_object",
        "metadata_type": "Catalog",
        "owner_name": "Контрагенты",
        "relative_path": "Catalog/Контрагенты/Catalog.obj.bsl",
        "read_status": "ok",
    }
    fields.update(overrides)
    return ModuleEntry(**fields)


def module(**overrides: Any) -> ArtifactRef:
    return ArtifactRef.from_module_entry(entry(**overrides))


def catalog_owner(name: str = "Контрагенты") -> OwnerRef:
    return OwnerRef("metadata_object", "Catalog", name)


def form(name: str = "ФормаСписка", obj: str = "Контрагенты") -> ArtifactRef:
    return ArtifactRef.form(
        "Catalog", obj, "CatalogForm", name, owner=catalog_owner(obj)
    )


def semantic_hit(artifact: ArtifactRef, score: Any, suffix: str = "") -> SearchHit:
    return SearchHit(
        artifact=artifact,
        method="semantic",
        document_id=artifact.artifact_id + suffix,
        similarity=score,
    )


def exact_hit(artifact: ArtifactRef) -> SearchHit:
    return SearchHit(artifact=artifact, method="exact_id", matched_field="artifact_id")


# --- идентичность модулей и согласованность с #203 ---------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "module_kind": "manager",
            "relative_path": "Catalog/Контрагенты/Catalog.mgr.bsl",
        },
        {
            "module_kind": "session",
            "owner_kind": "configuration",
            "metadata_type": None,
            "owner_name": None,
            "relative_path": "Configuration.seance.bsl",
        },
        {
            "module_kind": "command",
            "owner_kind": "metadata_object_command",
            "owner_name": "Контрагенты.ОткрытьКарточку",
            "relative_path": (
                "Catalog/Контрагенты/CatalogCommand/ОткрытьКарточку/"
                "CatalogCommand.obj.bsl"
            ),
        },
    ],
)
def test_module_artifact_id_equals_module_entry_id(fields: dict[str, Any]) -> None:
    item = entry(**fields)
    artifact = ArtifactRef.from_module_entry(item)
    assert artifact.artifact_id == item.module_id
    assert artifact.source_path == item.relative_path
    assert artifact.module_kind == item.module_kind


def test_configuration_id_has_empty_segments() -> None:
    artifact = module(
        module_kind="session",
        owner_kind="configuration",
        metadata_type=None,
        owner_name=None,
        relative_path="Configuration.seance.bsl",
    )
    assert artifact.artifact_id == "configuration:::session"


def _eval_artifacts() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name in ("search_eval_issue321.json", "search_eval_issue321_hard.json"):
        data = json.loads((RESEARCH / name).read_text(encoding="utf-8"))
        items.extend(data["artifacts"])
    return items


def test_eval_module_and_form_ids_match_contract() -> None:
    checked = 0
    for item in _eval_artifacts():
        if item["owner_kind"] == "configuration":
            continue
        if item["id"].startswith("form:"):
            parts = item["id"][len("form:") :].split("/")
            artifact = ArtifactRef.form(*parts)
        else:
            artifact = ArtifactRef(
                artifact_kind="module",
                owner=OwnerRef(
                    item["owner_kind"], item["metadata_type"], item["owner_name"]
                ),
                module_kind=item["module_kind"],
            )
        assert artifact.artifact_id == item["id"]
        checked += 1
    assert checked > 0


def test_eval_configuration_ids_differ_from_canonical_form() -> None:
    """Расхождение #321 зафиксировано: канон — ``ModuleEntry.module_id``."""
    for item in _eval_artifacts():
        if item["owner_kind"] != "configuration":
            continue
        canonical = ArtifactRef(
            artifact_kind="module",
            owner=OwnerRef("configuration", None, None),
            module_kind=item["module_kind"],
        ).artifact_id
        assert canonical == f"configuration:::{item['module_kind']}"
        assert item["id"] != canonical


def test_same_short_name_different_owners_has_different_identity() -> None:
    artifacts = [
        module(),
        module(
            metadata_type="Document",
            relative_path="Document/Контрагенты/Document.obj.bsl",
        ),
        form("ФормаСписка", "Контрагенты"),
        form("ФормаСписка", "Номенклатура"),
        module(
            module_kind="command",
            owner_kind="metadata_object_command",
            owner_name="Контрагенты.Открыть",
            relative_path=(
                "Catalog/Контрагенты/CatalogCommand/Открыть/CatalogCommand.obj.bsl"
            ),
        ),
        module(
            module_kind="command",
            owner_kind="metadata_object_command",
            owner_name="Номенклатура.Открыть",
            relative_path=(
                "Catalog/Номенклатура/CatalogCommand/Открыть/CatalogCommand.obj.bsl"
            ),
        ),
    ]
    identities = {artifact.identity for artifact in artifacts}
    assert len(identities) == len(artifacts)


def test_owner_card_id_does_not_collide_with_module_or_form() -> None:
    card = ArtifactRef.owner_card(catalog_owner())
    assert card.artifact_id == "owner:metadata_object:Catalog:Контрагенты"
    assert card.artifact_id != module().artifact_id
    assert not card.artifact_id.startswith("form:")


# --- формы и экранирование ---------------------------------------------------


def test_form_id_matches_form_key_without_reserved_chars() -> None:
    parts = ("Document", "РеализацияТоваров", "DocumentForm", "ФормаДокумента")
    assert ArtifactRef.form(*parts).artifact_id == "form:" + form_key(*parts)


def test_common_form_key_with_empty_object_name() -> None:
    artifact = ArtifactRef.form("CommonForm", "", "CommonForm", "ОбщаяФорма")
    assert artifact.artifact_id == "form:CommonForm//CommonForm/ОбщаяФорма"
    assert artifact.owner is None


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        ("a:b", "a%3Ab"),
        ("a#b", "a%23b"),
        ("a/b", "a%2Fb"),
        ("100%", "100%25"),
        ("%3A", "%253A"),
        ("Обработка ext.epf", "Обработка ext.epf"),
    ],
)
def test_encode_id_segment(raw: str, encoded: str) -> None:
    assert encode_id_segment(raw) == encoded


def test_form_id_is_injective_for_reserved_chars() -> None:
    left = ArtifactRef.form("External", "a/b", "Form", "c")
    right = ArtifactRef.form("External", "a", "b/Form", "c")
    assert left.artifact_id != right.artifact_id
    hashed = ArtifactRef.form("External", "Имя#1", "Form", "Ф:1")
    assert "#" not in hashed.artifact_id
    assert hashed.artifact_id == "form:External/Имя%231/Form/Ф%3A1"


@pytest.mark.parametrize(
    "parts",
    [
        ("Catalog", "A", "CatalogForm", ""),
        ("Catalog", "A" + chr(0), "CatalogForm", "F"),
        ("Catalog", "A", "CatalogForm" + chr(10), "F"),
    ],
)
def test_invalid_form_key_rejected(parts: tuple[str, str, str, str]) -> None:
    with pytest.raises(ValueError):
        ArtifactRef.form(*parts)


def test_module_names_never_need_escaping() -> None:
    with pytest.raises(ValueError):
        OwnerRef("metadata_object", "Catalog", "A:B")
    with pytest.raises(ValueError):
        OwnerRef("metadata_object", "Catalog", "A#B")
    with pytest.raises(ValueError):
        OwnerRef("metadata_object", "Каталог", "A")


# --- регистр и кириллица ------------------------------------------------------


def test_identity_key_is_casefold_and_nfc() -> None:
    composed = "Й"
    decomposed = "И" + chr(0x306)
    assert identity_key("КонтрАгенты") == identity_key("контрагенты")
    assert identity_key(composed) == identity_key(decomposed)


def test_ids_keep_original_case() -> None:
    assert form("ФормаСписка").artifact_id.endswith("/ФормаСписка")


def test_case_duplicates_rejected_like_module_index() -> None:
    upper = entry()
    lower = entry(
        owner_name="контрагенты", relative_path="Catalog/контрагенты/Catalog.obj.bsl"
    )
    with pytest.raises(ValueError):
        ModuleIndex.from_entries([upper, lower])
    with pytest.raises(ValueError):
        SearchDocumentSet.from_documents(
            [
                SearchDocument(ArtifactRef.from_module_entry(upper), "А"),
                SearchDocument(ArtifactRef.from_module_entry(lower), "Б"),
            ]
        )


# --- источники и координаты ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/abs/Catalog.obj.bsl",
        "C:/dump/Catalog.obj.bsl",
        "C:Catalog.obj.bsl",
        BS + BS + "server" + BS + "share",
        "Catalog" + BS + "A.bsl",
        "../Catalog/A.bsl",
        "Catalog/./A.bsl",
        "Catalog//A.bsl",
        "",
        "Catalog/A.bsl ",
        "CON/A.bsl",
        "Catalog/A" + chr(9) + ".bsl",
    ],
)
def test_absolute_or_unsafe_source_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_source_path(path)
    with pytest.raises(ValueError):
        ArtifactRef.form("Catalog", "A", "CatalogForm", "F", source_path=path)


def test_relative_source_accepted_without_bsl_suffix() -> None:
    path = "Catalog/Контрагенты/CatalogForm/ФормаСписка"
    assert validate_relative_source_path(path) == path


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 1), (2, 1), (-1, 3), (True, 2), (1.0, 2), (1, None)],
)
def test_invalid_coordinates_rejected(start: Any, end: Any) -> None:
    with pytest.raises(ValueError):
        TextSpan(start, end)


def test_fragment_ids() -> None:
    artifact = module()
    whole = SearchDocument(artifact, "текст")
    proc = SearchDocument(
        artifact, "текст", procedure="ПриЗаписи", span=TextSpan(3, 9)
    )
    window = SearchDocument(artifact, "текст", span=TextSpan(1, 40))
    base = artifact.artifact_id
    assert whole.document_id == base
    assert proc.document_id == base + "#ПриЗаписи"
    assert window.document_id == base + "#L1-40"


@pytest.mark.parametrize("name", ["", "Процедура#1", "a:b", "1Имя", "L1-2"])
def test_invalid_procedure_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        SearchDocument(module(), "текст", procedure=name)


def test_empty_document_text_rejected() -> None:
    with pytest.raises(ValueError):
        SearchDocument(module(), " \n")


# --- набор документов и дубликаты ----------------------------------------------


def test_document_set_allows_fragments_and_equal_text() -> None:
    artifact = module()
    other = form()
    docs = [
        SearchDocument(artifact, "общий текст", span=TextSpan(10, 20)),
        SearchDocument(artifact, "общий текст", span=TextSpan(9, 12)),
        SearchDocument(artifact, "тело", procedure="ПриЗаписи"),
        SearchDocument(artifact, "модуль целиком"),
        SearchDocument(other, "общий текст"),
    ]
    shuffled = docs[:]
    random.Random(322).shuffle(shuffled)
    left = SearchDocumentSet.from_documents(docs)
    right = SearchDocumentSet.from_documents(shuffled)
    assert left == right
    base = artifact.artifact_id
    ids = [d.document_id for d in left.documents if d.artifact == artifact]
    assert ids == [base, base + "#ПриЗаписи", base + "#L9-12", base + "#L10-20"]
    assert len(left.artifacts()) == 2


def test_document_set_rejects_duplicate_document_id() -> None:
    with pytest.raises(ValueError):
        SearchDocumentSet.from_documents(
            [
                SearchDocument(module(), "a", procedure="ПриЗаписи"),
                SearchDocument(module(), "b", procedure="приЗаписи"),
            ]
        )


def test_document_set_rejects_conflicting_artifact() -> None:
    second = ArtifactRef.form(
        "Catalog",
        "Контрагенты",
        "CatalogForm",
        "ФормаСписка",
        owner=catalog_owner(),
        source_path="Catalog/Контрагенты/CatalogForm/ФормаСписка",
    )
    with pytest.raises(ValueError):
        SearchDocumentSet.from_documents(
            [
                SearchDocument(form(), "a", procedure="П1"),
                SearchDocument(second, "b", procedure="П2"),
            ]
        )


# --- фильтры ---------------------------------------------------------------------


def _artifacts() -> dict[str, ArtifactRef]:
    return {
        "object": module(),
        "manager": module(
            module_kind="manager", relative_path="Catalog/Контрагенты/Catalog.mgr.bsl"
        ),
        "document": module(
            metadata_type="Document",
            owner_name="Реализация",
            relative_path="Document/Реализация/Document.obj.bsl",
        ),
        "form": form(),
        "orphan_form": ArtifactRef.form("CommonForm", "", "CommonForm", "Общая"),
        "card": ArtifactRef.owner_card(catalog_owner()),
    }


def _selected(filters: SearchFilters) -> set[str]:
    return {name for name, art in _artifacts().items() if filters.matches(art)}


def test_filters_by_kind_module_kind_and_owner() -> None:
    every = set(_artifacts())
    assert _selected(SearchFilters()) == every
    assert SearchFilters().is_empty
    assert _selected(SearchFilters(artifact_kinds=("form",))) == {
        "form",
        "orphan_form",
    }
    assert _selected(SearchFilters(module_kinds=("manager",))) == {"manager"}
    assert _selected(SearchFilters(metadata_types=("Document",))) == {"document"}
    assert _selected(SearchFilters(owner_names=("КОНТРАГЕНТЫ",))) == {
        "object",
        "manager",
        "form",
        "card",
    }
    assert _selected(
        SearchFilters(artifact_kinds=("module",), owner_names=("контрагенты",))
    ) == {"object", "manager"}
    assert _selected(SearchFilters(owner_kinds=("metadata_object",))) == every - {
        "orphan_form"
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifact_kinds": ()},
        {"artifact_kinds": ("table",)},
        {"module_kinds": ("form_descent_part",)},
        {"owner_kinds": ("catalog",)},
        {"metadata_types": ("Справочник",)},
        {"owner_names": "Контрагенты"},
    ],
)
def test_invalid_filters_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        SearchFilters(**kwargs)


def test_filters_are_normalized_deterministically() -> None:
    left = SearchFilters(
        owner_names=("б", "А", "а"), module_kinds=("object", "manager")
    )
    right = SearchFilters(owner_names=["А", "б"], module_kinds=["manager", "object"])
    assert left == right
    assert left.owner_names == ("А", "б")
    assert left.module_kinds == ("manager", "object")


def test_document_set_select() -> None:
    docs = SearchDocumentSet.from_documents(
        SearchDocument(art, "текст") for art in _artifacts().values()
    )
    picked = docs.select(SearchFilters(artifact_kinds=("form",)))
    assert {d.artifact.artifact_kind for d in picked.documents} == {"form"}
    assert len(picked) == 2


# --- состояния результата ------------------------------------------------------


def test_all_states_are_distinguishable() -> None:
    query = SearchQuery("Контрагенты")
    a, b = _artifacts()["object"], _artifacts()["manager"]
    results = [
        SearchResult.exact(query, exact_hit(a)),
        SearchResult.ambiguous(query, [exact_hit(a), exact_hit(b)]),
        SearchResult.semantic(query, [semantic_hit(a, 0.8)]),
        SearchResult.empty(query),
        SearchResult.failed(query, "index_not_ready"),
    ]
    assert [r.status for r in results] == [
        "exact",
        "ambiguous",
        "semantic",
        "empty",
        "error",
    ]
    assert results[4].error == SearchError("index_not_ready")
    assert results[4].hits == ()


def test_invalid_state_combinations_rejected() -> None:
    query = SearchQuery("x", top_k=1)
    a, b = _artifacts()["object"], _artifacts()["manager"]
    bad_code: Any = "provider said: details"
    cases = [
        lambda: SearchResult("exact", query, (exact_hit(a), exact_hit(b))),
        lambda: SearchResult("ambiguous", query, (exact_hit(a),)),
        lambda: SearchResult("semantic", query, (exact_hit(a),)),
        lambda: SearchResult(
            "semantic", query, (semantic_hit(a, 1), semantic_hit(b, 2))
        ),
        lambda: SearchResult("empty", query, (exact_hit(a),)),
        lambda: SearchResult("error", query),
        lambda: SearchResult("empty", query, (), SearchError("internal")),
        lambda: SearchResult("ambiguous", query, (exact_hit(a), exact_hit(a))),
        lambda: SearchResult.exact(SearchQuery("x", mode="semantic"), exact_hit(a)),
        lambda: SearchResult.semantic(
            SearchQuery("x", mode="exact"), [semantic_hit(a, 1)]
        ),
        lambda: SearchResult.exact(
            SearchQuery("x", filters=SearchFilters(artifact_kinds=("form",))),
            exact_hit(a),
        ),
        lambda: SearchResult.failed(query, bad_code),
    ]
    for case in cases:
        with pytest.raises(ValueError):
            case()


def test_ambiguous_is_not_limited_by_top_k() -> None:
    query = SearchQuery("Контрагенты", top_k=1)
    hits = [exact_hit(art) for art in list(_artifacts().values())[:3]]
    assert len(SearchResult.ambiguous(query, hits).hits) == 3


@pytest.mark.parametrize("text", ["", "   ", "a" + chr(0)])
def test_invalid_query_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        SearchQuery(text)


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_invalid_top_k_rejected(top_k: Any) -> None:
    with pytest.raises(ValueError):
        SearchQuery("x", top_k=top_k)


# --- происхождение и сходство ---------------------------------------------------


def test_provenance_and_similarity_are_separate() -> None:
    a = module()
    with pytest.raises(ValueError):
        SearchHit(a, "exact_id", matched_field="artifact_id", similarity=1.0)
    with pytest.raises(ValueError):
        SearchHit(a, "exact_name", matched_field="artifact_id")
    with pytest.raises(ValueError):
        SearchHit(a, "semantic", document_id=a.artifact_id)
    with pytest.raises(ValueError):
        SearchHit(
            a,
            "semantic",
            matched_field="form_name",
            document_id=a.artifact_id,
            similarity=0.5,
        )
    name_hit = SearchHit(a, "exact_name", matched_field="owner_name")
    assert name_hit.similarity is None


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, "0.5"])
def test_invalid_similarity_rejected(score: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        semantic_hit(module(), score)


def test_similarity_is_not_probability() -> None:
    assert semantic_hit(module(), 7.25).similarity == 7.25
    assert semantic_hit(module(), -0.5).similarity == -0.5


def test_document_id_must_belong_to_artifact() -> None:
    with pytest.raises(ValueError):
        SearchHit(module(), "semantic", document_id=form().artifact_id, similarity=0.1)
    with pytest.raises(ValueError):
        SearchHit(
            module(), "semantic", document_id=module().artifact_id + "X", similarity=0.1
        )


# --- порядок и сериализация ------------------------------------------------------


def _semantic_result(order_seed: int) -> SearchResult:
    arts = list(_artifacts().values())
    hits = [
        semantic_hit(arts[0], 0.5, "#ПриЗаписи"),
        semantic_hit(arts[1], 0.9),
        semantic_hit(arts[2], 0.5),
        semantic_hit(arts[3], 0.7, "#L1-5"),
    ]
    random.Random(order_seed).shuffle(hits)
    return SearchResult.semantic(SearchQuery("цена", top_k=5), hits)


def test_semantic_order_is_deterministic() -> None:
    first = _semantic_result(1)
    second = _semantic_result(2)
    assert first == second
    scores = [hit.similarity for hit in first.hits]
    assert scores == sorted(scores, reverse=True)
    tied = [hit.artifact.identity for hit in first.hits if hit.similarity == 0.5]
    assert tied == sorted(tied)
    assert first.to_json() == second.to_json()


def test_result_round_trip_and_canonical_json() -> None:
    result = _semantic_result(3)
    text = result.to_json()
    data = json.loads(text)
    assert data["schema"] == SEARCH_CONTRACT_SCHEMA
    assert SearchResult.from_dict(data) == result
    assert text.endswith("\n")
    assert BS not in text
    assert "NaN" not in text


def test_error_result_round_trip_has_fixed_message() -> None:
    result = SearchResult.failed(SearchQuery("x"), "embedder_failed")
    data = json.loads(result.to_json())
    assert data["error"] == {"code": "embedder_failed", "message": "embedder failed"}
    assert SearchResult.from_dict(data) == result
    data["error"]["message"] = "embedder failed: provider detail"
    with pytest.raises(ValueError):
        SearchResult.from_dict(data)


def test_document_set_round_trip_and_text_policy() -> None:
    docs = SearchDocumentSet.from_documents(
        [
            SearchDocument(module(), "Процедура ПриЗаписи()", procedure="ПриЗаписи"),
            SearchDocument(form(), "форма", span=TextSpan(1, 3)),
        ]
    )
    safe = json.loads(docs.to_json())
    assert all("text" not in doc for doc in safe["documents"])
    full = json.loads(docs.to_json(include_text=True))
    assert SearchDocumentSet.from_dict(full) == docs
    with pytest.raises(ValueError):
        SearchDocumentSet.from_dict(safe)
    full["documents"][0]["text"] = "подмена"
    with pytest.raises(ValueError):
        SearchDocumentSet.from_dict(full)


def test_from_dict_rejects_tampered_ids_and_extra_keys() -> None:
    data = form().to_dict()
    data["artifact_id"] = "form:Catalog/Другой/CatalogForm/ФормаСписка"
    with pytest.raises(ValueError):
        ArtifactRef.from_dict(data)
    extra = form().to_dict()
    extra["absolute_path"] = None
    with pytest.raises(ValueError):
        ArtifactRef.from_dict(extra)
    bad_path = form().to_dict()
    bad_path["source_path"] = "/abs/Catalog"
    with pytest.raises(ValueError):
        ArtifactRef.from_dict(bad_path)
    wrong_schema = SearchResult.empty(SearchQuery("x")).to_dict()
    wrong_schema["schema"] = "search_contract/0"
    with pytest.raises(ValueError):
        SearchResult.from_dict(wrong_schema)


def test_query_and_filters_round_trip() -> None:
    query = SearchQuery(
        "Контрагенты ИНН",
        mode="exact",
        top_k=3,
        filters=SearchFilters(module_kinds=("manager",), owner_names=("Контрагенты",)),
    )
    assert SearchQuery.from_dict(json.loads(json.dumps(query.to_dict()))) == query


def test_models_are_immutable() -> None:
    artifact: Any = module()
    with pytest.raises(FrozenInstanceError):
        artifact.source_path = "x"
    result: Any = SearchResult.empty(SearchQuery("x"))
    with pytest.raises(FrozenInstanceError):
        result.status = "exact"


# --- связи владельца --------------------------------------------------------------


def test_owner_links() -> None:
    owner = catalog_owner()
    confirmed_module = OwnerLink(owner, module(), "module_entry")
    confirmed_form = OwnerLink(owner, form(), "form_key")
    orphan = ArtifactRef.form("CommonForm", "", "CommonForm", "Общая")
    unconfirmed = OwnerLink(owner, orphan, "unconfirmed")
    assert confirmed_module.confirmed
    assert confirmed_form.confirmed
    assert not unconfirmed.confirmed
    for link in (confirmed_module, confirmed_form, unconfirmed):
        assert OwnerLink.from_dict(json.loads(json.dumps(link.to_dict()))) == link


def test_owner_link_basis_is_checked() -> None:
    other = catalog_owner("Номенклатура")
    with pytest.raises(ValueError):
        OwnerLink(other, module(), "module_entry")
    with pytest.raises(ValueError):
        OwnerLink(other, form(), "form_key")
    with pytest.raises(ValueError):
        OwnerLink(catalog_owner(), module(), "form_key")
    with pytest.raises(ValueError):
        OwnerLink(catalog_owner(), ArtifactRef.owner_card(other), "unconfirmed")


# --- документация -----------------------------------------------------------------


def test_contract_doc_is_linked_from_readme() -> None:
    assert (REPO_ROOT / "docs" / "search_contract.md").is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/search_contract.md" in readme
