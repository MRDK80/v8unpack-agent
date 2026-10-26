"""Библиотечный вход общего поиска search_api (#366, часть A задачи #328)."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import math
from pathlib import Path

import pytest

import v8unpack_agent
from v8unpack_agent import runner, search_api
from v8unpack_agent.run_report import RunObjectKind
from v8unpack_agent.runner import RunOptions, run_pipeline
from v8unpack_agent.search_api import (
    DEMO_EMBEDDER_NAME,
    HashedBagOfWordsDemoEmbedder,
    SearchCorpusError,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_context import build_search_context
from v8unpack_agent.search_contract import SearchFilters, SearchQuery
from v8unpack_agent.search_corpus import SearchCorpus
from v8unpack_agent.search_dispatch import ExactCatalog, SearchDispatcher
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import SearchIndex, load_index

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = "Configuration.802.bsl"
CONSTANT = "Constant/Rate/Constant.obj.bsl"
SERVICE = "HTTPService/Api/HTTPService.obj.bsl"
COMMON = "CommonModule/Shared/CommonModule.obj.bsl"
FILES = {
    CONFIGURATION: "Процедура ПриНачалеРаботыСистемы()\nКонецПроцедуры\n",
    CONSTANT: "Процедура ПередЗаписью(Отказ)\n  КурсВалюты = 1;\nКонецПроцедуры\n",
    SERVICE: "Функция ПолучитьЗаказ(Запрос)\n  Возврат Неопределено;\nКонецФункции\n",
    COMMON: "Процедура ОбщаяПроцедура() Экспорт\nКонецПроцедуры\n",
}
MAX_CHARS = 100_000


def _build_export(root: Path) -> Path:
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _corpus_paths(corpus: SearchCorpus, source: str) -> set[str]:
    return {
        item.source_path
        for item in corpus.report.items
        if item.source == source and item.source_path is not None
    }


def _runner_paths(
    root: Path, groups: tuple[str, ...] | None = None
) -> tuple[set[str], set[str]]:
    outcome = run_pipeline(
        RunOptions(
            export_root=root,
            include_skd=False,
            include_module_index=True,
            module_groups=groups,
        )
    )
    assert outcome.completed
    modules = {
        item.object
        for item in outcome.report.objects
        if item.object_kind.value.startswith("module_")
    }
    common = {
        item.object
        for item in outcome.report.objects
        if item.object_kind is RunObjectKind.COMMON_MODULE
    }
    return modules, common


def _fragmentation(root: Path) -> FragmentationResult:
    return fragment_corpus(collect_search_corpus(root))


def _artifact_id(fragmentation: FragmentationResult, source_path: str) -> str:
    for fragment in fragmentation.fragments:
        artifact = fragment.document.artifact
        if artifact.source_path == source_path:
            return artifact.artifact_id
    raise AssertionError(source_path)


def _manual(
    fragmentation: FragmentationResult,
    query: SearchQuery,
    index: SearchIndex | None = None,
    embedder: HashedBagOfWordsDemoEmbedder | None = None,
) -> dict[str, object]:
    dispatcher = SearchDispatcher(
        ExactCatalog.from_fragmentation(fragmentation), index, embedder, fragmentation
    )
    return build_search_context(
        dispatcher.search(query), fragmentation, max_chars=MAX_CHARS
    ).to_dict()


def test_corpus_modules_match_runner(tmp_path: Path) -> None:
    root = _build_export(tmp_path)
    corpus = collect_search_corpus(root)
    modules, common = _runner_paths(root)
    assert {CONFIGURATION, CONSTANT, SERVICE} <= modules
    assert _corpus_paths(corpus, "module_index") == modules
    assert _corpus_paths(corpus, "common_modules") == common == {COMMON}


@pytest.mark.parametrize(
    "groups",
    [
        ("services",),
        ("configuration", "value-managers"),
        ("services", "configuration"),
    ],
)
def test_module_groups_match_runner(tmp_path: Path, groups: tuple[str, ...]) -> None:
    root = _build_export(tmp_path)
    corpus = collect_search_corpus(root, module_groups=groups)
    modules, _ = _runner_paths(root, groups)
    assert modules
    assert _corpus_paths(corpus, "module_index") == modules


def test_skip_common_modules(tmp_path: Path) -> None:
    root = _build_export(tmp_path)
    corpus = collect_search_corpus(root, include_common_modules=False)
    assert _corpus_paths(corpus, "common_modules") == set()
    assert SERVICE in _corpus_paths(corpus, "module_index")


def test_external_scanner_only_in_external_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake(root: Path) -> tuple[()]:
        calls.append(root)
        return ()

    monkeypatch.setattr(search_api, "scan_external_object_modules", fake)
    collect_search_corpus(tmp_path)
    assert calls == []
    collect_search_corpus(tmp_path, mode="external")
    assert calls == [tmp_path]


@pytest.mark.parametrize("bad", [(), ("bogus",), ("services", "bogus"), "services"])
def test_invalid_groups_rejected(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError, match="module_groups") as info:
        collect_search_corpus(tmp_path, module_groups=bad)  # type: ignore[arg-type]
    assert "bogus" not in str(info.value)


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        collect_search_corpus(tmp_path, mode="bogus")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("target", "name", "code"),
    [
        (search_api, "scan_forms", "scan_failed"),
        (search_api, "scan_common_modules", "common_modules_failed"),
        (runner, "scan_service_modules", "modules_failed"),
    ],
)
def test_stage_failure_has_runner_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: object,
    name: str,
    code: str,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("secret-path/leak")

    monkeypatch.setattr(target, name, boom)
    with pytest.raises(SearchCorpusError) as info:
        collect_search_corpus(_build_export(tmp_path))
    assert info.value.code == code
    assert "secret" not in str(info.value)


def test_demo_embedder_identity() -> None:
    identity = HashedBagOfWordsDemoEmbedder().identity
    params = json.dumps(
        {"dimension": 256, "tokenization_version": "1"},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert DEMO_EMBEDDER_NAME == "hashed-bow-demo"
    assert identity.name == "v8unpack-agent-hashed-bow-demo"
    assert identity.version == "1"
    assert identity.dimension == 256
    assert identity.config_hash == hashlib.sha256(params.encode()).hexdigest()
    assert identity == HashedBagOfWordsDemoEmbedder().identity


def test_demo_embedder_vectors_are_deterministic() -> None:
    embedder = HashedBagOfWordsDemoEmbedder()
    texts = ["КурсВалюты курсвалюты", "ПолучитьЗаказ", "", "  ;; "]
    first = embedder.embed(texts)
    assert first == HashedBagOfWordsDemoEmbedder().embed(texts)
    assert len(first) == len(texts)
    for vector in first:
        assert len(vector) == 256
        assert math.isclose(math.fsum(v * v for v in vector), 1.0)
    assert first[0] != first[1]
    assert first[2] == first[3]
    assert embedder.embed(["Курс"]) == embedder.embed(["КУРС"])


def test_module_has_no_hidden_providers() -> None:
    source = REPO_ROOT / "v8unpack_agent" / "search_api.py"
    tree = ast.parse(source.read_text("utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {
        "__future__",
        "collections",
        "hashlib",
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "typing",
        "unicodedata",
        "v8unpack_agent",
    }


def test_without_embedder_semantic_step_is_not_ready(tmp_path: Path) -> None:
    fragmentation = _fragmentation(_build_export(tmp_path))
    for mode in ("auto", "semantic"):
        context = search_with_context(
            fragmentation, "курс валюты", search_mode=mode, max_chars=MAX_CHARS
        )
        result = context.dispatch.result
        assert result.status == "error"
        assert result.error is not None
        assert result.error.code == "index_not_ready"


def test_exact_search_matches_manual_composition(tmp_path: Path) -> None:
    fragmentation = _fragmentation(_build_export(tmp_path))
    text = _artifact_id(fragmentation, SERVICE)
    context = search_with_context(fragmentation, text, max_chars=MAX_CHARS)
    assert context.dispatch.result.status == "exact"
    assert context.dispatch.path == ("exact",)
    assert context.to_dict() == _manual(fragmentation, SearchQuery(text))


def test_snapshot_and_semantic_search(tmp_path: Path) -> None:
    fragmentation = _fragmentation(_build_export(tmp_path / "export"))
    embedder = HashedBagOfWordsDemoEmbedder()
    path = tmp_path / "snapshot.json"
    index = build_search_snapshot(fragmentation, embedder, path)
    loaded = load_index(path)
    assert loaded.to_json() == index.to_json()
    assert loaded.assess(embedder.identity, fragmentation).queryable
    query = SearchQuery("курсвалюты", mode="semantic", top_k=2)
    context = search_with_context(
        fragmentation,
        query.text,
        search_mode="semantic",
        top_k=2,
        index=loaded,
        embedder=embedder,
        max_chars=MAX_CHARS,
    )
    assert context.dispatch.semantic_used
    assert context.dispatch.result.status == "semantic"
    expected = _manual(fragmentation, query, loaded, embedder)
    assert context.to_dict() == expected


def test_filters_are_passed_to_query(tmp_path: Path) -> None:
    fragmentation = _fragmentation(_build_export(tmp_path))
    text = _artifact_id(fragmentation, SERVICE)
    filters = SearchFilters(artifact_kinds=("form",))
    context = search_with_context(
        fragmentation,
        text,
        filters=filters,
        search_mode="exact",
        max_chars=MAX_CHARS,
    )
    assert context.dispatch.result.query.filters == filters
    assert context.to_dict() == _manual(
        fragmentation, SearchQuery(text, mode="exact", filters=filters)
    )


def test_defaults_match_search_query() -> None:
    params = inspect.signature(search_with_context).parameters
    fields = {field.name: field for field in dataclasses.fields(SearchQuery)}
    assert params["search_mode"].default == fields["mode"].default
    assert params["top_k"].default == fields["top_k"].default
    assert params["max_chars"].default is inspect.Parameter.empty
    assert params["embedder"].default is None
    assert params["index"].default is None


def test_root_package_is_not_extended() -> None:
    exported = set(getattr(v8unpack_agent, "__all__", ()))
    assert "search_api" not in exported
    assert exported.isdisjoint(search_api.__all__)


def test_changelog_references_issue() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text("utf-8")
    assert "#366" in text
    assert "search_api" in text
