"""Положительный сквозной поиск форм от scan_forms до Library и CLI (#384).

Синтетические выгрузки → ``scan_forms`` → ``collect_search_corpus`` →
``fragment_corpus`` → снимок → ``search_with_context`` / ``search_cli`` →
контекст. Покрыты четыре группы форм: форма объекта конфигурации,
``CommonForm``, форма внешней обработки и форма внешнего отчёта.

Сканеры, конструкторы и корпус не подменяются. Эмбеддер — явный
демонстрационный ``hashed-bow-demo``: проверяется механика, а не
релевантность. Модели, веса и сеть не используются.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_cli
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchFilters, to_canonical_json
from v8unpack_agent.search_corpus import SearchCorpus
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import load_index

CONFIG_DIR = "secret-config-384"
EXTERNAL_DIR = "secret-external-384"
DEMO = ("--embedder", "hashed-bow-demo")
FORBIDDEN_MODULES = (
    "chromadb",
    "httpx",
    "requests",
    "sentence_transformers",
    "torch",
    "transformers",
    "urllib3",
)

CATALOG_MODULE = "Catalog/Товары/Catalog.obj.bsl"
CATALOG_FORM = "Catalog/Товары/Form/ФормаЭлемента"
DOCUMENT_FORM = "Document/Заказ/Form/ФормаЭлемента"
COMMON_FORM = "CommonForm/ОбщаяФормаВыбора"
EMPTY_FORM = "Catalog/Товары/Form/ПустаяФорма"
PROCESSOR_DIR = "External/ЗагрузкаЦен.epf"
PROCESSOR_FORM = PROCESSOR_DIR + "/Form/ФормаЗагрузки"
REPORT_DIR = "External/ОтчетПродаж.erf"
REPORT_FORM = REPORT_DIR + "/ReportForm/ФормаОтчета"

CATALOG_FORM_BSL = (
    "Процедура ПриОткрытии(Отказ)\n"
    + "".join(f"  ЦенаТовара{i} = {i};\n" for i in range(1, 31))
    + "КонецПроцедуры\n"
)
CONFIG_FILES = {
    CATALOG_MODULE: "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n",
    CATALOG_FORM + "/Form.obj.bsl": CATALOG_FORM_BSL,
    CATALOG_FORM + "/Form.json": "{}",
    DOCUMENT_FORM + "/Form.obj.bsl": (
        "Процедура ПриСозданииНаСервере()\n  СуммаЗаказа = 0;\nКонецПроцедуры\n"
    ),
    DOCUMENT_FORM + "/Form.json": "{}",
    COMMON_FORM + "/CommonForm.obj.bsl": (
        "Процедура ВыбратьЗначение()\n  ВыбранноеЗначение = 1;\nКонецПроцедуры\n"
    ),
    COMMON_FORM + "/CommonForm.json": "{}",
    EMPTY_FORM + "/Form.obj.bsl": "",
    EMPTY_FORM + "/Form.json": "{}",
}
EXTERNAL_FILES = {
    PROCESSOR_DIR + "/ExternalDataProcessor.json": json.dumps({"name": "ЗагрузкаЦен"}),
    PROCESSOR_DIR + "/ExternalDataProcessor.obj.bsl": (
        "Процедура Загрузить() Экспорт\nКонецПроцедуры\n"
    ),
    PROCESSOR_FORM + "/Form.obj.bsl": (
        "Процедура КомандаЗагрузить()\n  ЗагрузитьЦены = Истина;\nКонецПроцедуры\n"
    ),
    PROCESSOR_FORM + "/Form.json": "{}",
    REPORT_DIR + "/ExternalDataProcessor.json": json.dumps({"name": "ОтчетПродаж"}),
    REPORT_DIR + "/ExternalDataProcessor.obj.bsl": (
        "Процедура ПриКомпоновкеРезультата()\nКонецПроцедуры\n"
    ),
    REPORT_FORM + "/ReportForm.obj.bsl": (
        "Процедура Сформировать()\n  ОтчетПоПродажам = 1;\nКонецПроцедуры\n"
    ),
    REPORT_FORM + "/ReportForm.json": "{}",
}

FORM_IDS = {
    "catalog": "form:Catalog/Товары/Form/ФормаЭлемента",
    "document": "form:Document/Заказ/Form/ФормаЭлемента",
    "common": "form:CommonForm//CommonForm/ОбщаяФормаВыбора",
    "processor": "form:ExternalDataProcessor/ЗагрузкаЦен.epf/Form/ФормаЗагрузки",
    "report": "form:ExternalReport/ОтчетПродаж.erf/ReportForm/ФормаОтчета",
}
EMPTY_FORM_ID = "form:Catalog/Товары/Form/ПустаяФорма"
FORM_SOURCES = {
    "catalog": CATALOG_FORM,
    "document": DOCUMENT_FORM,
    "common": COMMON_FORM,
    "processor": PROCESSOR_FORM,
    "report": REPORT_FORM,
}
FORM_BSL = {
    "catalog": CONFIG_FILES[CATALOG_FORM + "/Form.obj.bsl"],
    "document": CONFIG_FILES[DOCUMENT_FORM + "/Form.obj.bsl"],
    "common": CONFIG_FILES[COMMON_FORM + "/CommonForm.obj.bsl"],
    "processor": EXTERNAL_FILES[PROCESSOR_FORM + "/Form.obj.bsl"],
    "report": EXTERNAL_FILES[REPORT_FORM + "/ReportForm.obj.bsl"],
}
MODE_OF = {
    "catalog": "config",
    "document": "config",
    "common": "config",
    "processor": "external",
    "report": "external",
}
UNIQUE_FORM_NAMES = {
    "common": "ОбщаяФормаВыбора",
    "processor": "ФормаЗагрузки",
    "report": "ФормаОтчета",
}
COLLIDING_FORM_NAME = "ФормаЭлемента"
SEMANTIC_TEXT = "цена товара выбранное значение"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in #384 tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any, str, str]:
    code = search_cli.main(list(argv))
    captured = capsys.readouterr()
    data = json.loads(captured.out) if captured.out else None
    return code, data, captured.out, captured.err


def _assert_clean(out: str, err: str, *secrets: str) -> None:
    assert "Traceback" not in out
    assert "Traceback" not in err
    for secret in secrets:
        assert secret not in out
        assert secret not in err
    assert [name for name in FORBIDDEN_MODULES if name in sys.modules] == []


def _hit_ids(context: dict[str, Any]) -> list[str]:
    return [
        hit["artifact"]["artifact_id"] for hit in context["dispatch"]["result"]["hits"]
    ]


@pytest.fixture
def exports(tmp_path: Path) -> dict[str, Path]:
    return {
        "config": _write_tree(tmp_path / CONFIG_DIR, CONFIG_FILES),
        "external": _write_tree(tmp_path / EXTERNAL_DIR, EXTERNAL_FILES),
    }


@pytest.fixture
def corpora(exports: dict[str, Path]) -> dict[str, SearchCorpus]:
    return {
        mode: collect_search_corpus(root, mode=mode) for mode, root in exports.items()
    }


@pytest.fixture
def fragmentations(corpora: dict[str, SearchCorpus]) -> dict[str, FragmentationResult]:
    return {mode: fragment_corpus(corpus) for mode, corpus in corpora.items()}


@pytest.fixture
def snapshots(
    tmp_path: Path, exports: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> dict[str, Path]:
    store = tmp_path / "snapshots"
    store.mkdir()
    paths: dict[str, Path] = {}
    for mode, root in exports.items():
        path = store / f"{mode}.json"
        code, data, out, err = _run(
            capsys, "build", str(root), "--mode", mode, "--snapshot", str(path), *DEMO
        )
        assert code == 0, err
        assert data["status"] == "ok"
        _assert_clean(out, err, str(tmp_path), CONFIG_DIR, EXTERNAL_DIR)
        paths[mode] = path
    return paths


def _library(
    fragmentation: FragmentationResult,
    text: str,
    *,
    snapshot: Path | None = None,
    include_text: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    index = load_index(snapshot) if snapshot is not None else None
    embedder = HashedBagOfWordsDemoEmbedder() if snapshot is not None else None
    kwargs.setdefault("max_chars", -1)
    context = search_with_context(
        fragmentation, text, index=index, embedder=embedder, **kwargs
    )
    return context.to_dict(include_text=include_text)


# --- обнаружение и включение в корпус -------------------------------------------


def test_four_form_groups_are_discovered_and_included(
    corpora: dict[str, SearchCorpus],
    fragmentations: dict[str, FragmentationResult],
) -> None:
    for name, artifact_id in FORM_IDS.items():
        corpus = corpora[MODE_OF[name]]
        item = next(i for i in corpus.report.items if i.artifact_id == artifact_id)
        assert item.artifact_kind == "form"
        assert item.source == "forms"
        assert item.included is True
        assert item.source_path == FORM_SOURCES[name]
        document = next(
            d
            for d in corpus.documents.documents
            if d.artifact.artifact_id == artifact_id
        )
        assert document.text == FORM_BSL[name]
        fragment_ids = {
            f.document.artifact.artifact_id
            for f in fragmentations[MODE_OF[name]].fragments
        }
        assert artifact_id in fragment_ids
    form_items = [
        i
        for corpus in corpora.values()
        for i in corpus.report.items
        if i.artifact_kind == "form" and i.included
    ]
    assert sorted(i.artifact_id for i in form_items) == sorted(FORM_IDS.values())


def test_empty_form_bsl_is_not_included(corpora: dict[str, SearchCorpus]) -> None:
    corpus = corpora["config"]
    item = next(i for i in corpus.report.items if i.artifact_id == EMPTY_FORM_ID)
    assert item.included is False
    assert item.reason is not None
    assert EMPTY_FORM_ID not in {
        d.artifact.artifact_id for d in corpus.documents.documents
    }


# --- exact и неоднозначность ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(FORM_IDS))
def test_exact_by_artifact_id_library_and_cli(
    name: str,
    exports: dict[str, Path],
    fragmentations: dict[str, FragmentationResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    mode = MODE_OF[name]
    artifact_id = FORM_IDS[name]
    code, data, out, err = _run(
        capsys,
        "query",
        str(exports[mode]),
        "--mode",
        mode,
        "--query",
        artifact_id,
        "--search-mode",
        "exact",
        "--include-text",
    )
    assert code == 0, err
    context = data["context"]
    assert context["search_kind"] == "exact"
    assert context["dispatch"]["path"] == ["exact"]
    assert _hit_ids(context) == [artifact_id]
    assert context["dispatch"]["result"]["hits"][0]["method"] == "exact_id"
    assert context == _library(
        fragmentations[mode], artifact_id, search_mode="exact", include_text=True
    )
    _assert_clean(out, err, CONFIG_DIR, EXTERNAL_DIR)


@pytest.mark.parametrize("name", sorted(UNIQUE_FORM_NAMES))
def test_exact_by_unique_form_name(
    name: str,
    exports: dict[str, Path],
    fragmentations: dict[str, FragmentationResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    mode = MODE_OF[name]
    form_name = UNIQUE_FORM_NAMES[name]
    code, data, out, err = _run(
        capsys,
        "query",
        str(exports[mode]),
        "--mode",
        mode,
        "--query",
        form_name,
        "--search-mode",
        "exact",
    )
    assert code == 0, err
    context = data["context"]
    assert context["search_kind"] == "exact"
    assert _hit_ids(context) == [FORM_IDS[name]]
    hit = context["dispatch"]["result"]["hits"][0]
    assert hit["method"] == "exact_name"
    assert hit["matched_field"] == "form_name"
    assert context == _library(fragmentations[mode], form_name, search_mode="exact")
    _assert_clean(out, err, CONFIG_DIR, EXTERNAL_DIR)


@pytest.mark.parametrize("search_mode", ["exact", "auto"])
def test_form_name_collision_returns_full_candidate_set(
    search_mode: str,
    exports: dict[str, Path],
    fragmentations: dict[str, FragmentationResult],
    snapshots: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, data, out, err = _run(
        capsys,
        "query",
        str(exports["config"]),
        "--query",
        COLLIDING_FORM_NAME,
        "--search-mode",
        search_mode,
        "--top-k",
        "1",
        "--snapshot",
        str(snapshots["config"]),
        *DEMO,
    )
    assert code == 0, err
    context = data["context"]
    assert context["search_kind"] == "ambiguous"
    assert context["dispatch"]["path"] == ["exact"]
    assert sorted(_hit_ids(context)) == sorted(
        [FORM_IDS["catalog"], FORM_IDS["document"]]
    )
    assert context["candidates_total"] == 2
    assert context["candidates_omitted"] == 0
    for hit in context["dispatch"]["result"]["hits"]:
        assert hit["method"] == "exact_name"
        assert hit["matched_field"] == "form_name"
    assert context == _library(
        fragmentations["config"],
        COLLIDING_FORM_NAME,
        snapshot=snapshots["config"],
        search_mode=search_mode,
        top_k=1,
    )
    _assert_clean(out, err, CONFIG_DIR, EXTERNAL_DIR)


# --- semantic с фильтром форм ---------------------------------------------------


@pytest.mark.parametrize("mode", ["config", "external"])
@pytest.mark.parametrize("top_k", [1, 2])
def test_semantic_with_form_filter_returns_real_forms(
    mode: str,
    top_k: int,
    exports: dict[str, Path],
    corpora: dict[str, SearchCorpus],
    fragmentations: dict[str, FragmentationResult],
    snapshots: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, data, out, err = _run(
        capsys,
        "query",
        str(exports[mode]),
        "--mode",
        mode,
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--artifact-kind",
        "form",
        "--top-k",
        str(top_k),
        "--snapshot",
        str(snapshots[mode]),
        *DEMO,
    )
    assert code == 0, err
    assert data["status"] == "ok"
    context = data["context"]
    assert context["search_kind"] == "semantic"
    assert context["dispatch"]["index_status"]["compatibility"] == "compatible"
    assert context["dispatch"]["index_status"]["freshness"] == "fresh"
    ids = _hit_ids(context)
    assert 0 < len(ids) <= top_k
    assert len(set(ids)) == len(ids)
    corpus_forms = {
        d.artifact.artifact_id
        for d in corpora[mode].documents.documents
        if d.artifact.artifact_kind == "form"
    }
    assert set(ids) <= corpus_forms
    for hit in context["dispatch"]["result"]["hits"]:
        assert hit["artifact"]["artifact_kind"] == "form"
        assert hit["method"] == "semantic"
    assert context == _library(
        fragmentations[mode],
        SEMANTIC_TEXT,
        snapshot=snapshots[mode],
        search_mode="semantic",
        filters=SearchFilters(artifact_kinds=("form",)),
        top_k=top_k,
    )
    _assert_clean(out, err, CONFIG_DIR, EXTERNAL_DIR)


# --- источники, координаты и бюджет -------------------------------------------


@pytest.mark.parametrize("name", sorted(FORM_IDS))
def test_context_source_and_coordinates_match_form_text(
    name: str,
    exports: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    mode = MODE_OF[name]
    lines = FORM_BSL[name].splitlines()
    code, data, _, err = _run(
        capsys,
        "query",
        str(exports[mode]),
        "--mode",
        mode,
        "--query",
        FORM_IDS[name],
        "--include-text",
    )
    assert code == 0, err
    context = data["context"]
    assert context["status"] == "built"
    assert len(context["included"]) == 1
    block = context["included"][0]
    assert block["hit"]["artifact"]["artifact_id"] == FORM_IDS[name]
    assert block["hit"]["artifact"]["source_path"] == FORM_SOURCES[name]
    assert block["included_span"] == {"start_line": 1, "end_line": len(lines)}
    assert block["source_document_ids"]
    for line in lines:
        assert line in context["text"]
    assert len(context["text"]) == context["text_chars"]


def test_form_context_budget_truncates_by_included_lines(
    exports: dict[str, Path],
    fragmentations: dict[str, FragmentationResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact_id = FORM_IDS["catalog"]
    lines = CATALOG_FORM_BSL.splitlines()
    _, full, _, _ = _run(
        capsys,
        "query",
        str(exports["config"]),
        "--query",
        artifact_id,
        "--include-text",
    )
    full_chars = full["context"]["text_chars"]
    truncated_seen = False
    for budget in range(full_chars - 1, 0, -97):
        code, data, _, err = _run(
            capsys,
            "query",
            str(exports["config"]),
            "--query",
            artifact_id,
            "--include-text",
            "--max-chars",
            str(budget),
        )
        context = data["context"]
        assert len(context["text"]) <= budget
        assert _hit_ids(context) == [artifact_id]
        assert context == _library(
            fragmentations["config"], artifact_id, include_text=True, max_chars=budget
        )
        if context["status"] != "truncated" or not context["included"]:
            continue
        assert code == 0, err
        span = context["included"][0]["included_span"]
        assert 1 <= span["start_line"] <= span["end_line"] < len(lines)
        inside = lines[span["start_line"] - 1 : span["end_line"]]
        outside = lines[: span["start_line"] - 1] + lines[span["end_line"] :]
        for line in inside:
            assert line in context["text"]
        for line in outside:
            assert line not in context["text"]
        truncated_seen = True
    assert truncated_seen


# --- связи владельцев ----------------------------------------------------------


@pytest.mark.parametrize("name", ["common", "processor", "report"])
def test_common_and_external_forms_stay_unconfirmed(
    name: str, corpora: dict[str, SearchCorpus]
) -> None:
    corpus = corpora[MODE_OF[name]]
    artifact_id = FORM_IDS[name]
    item = next(i for i in corpus.report.items if i.artifact_id == artifact_id)
    assert item.owner_basis == "unconfirmed"
    link = next(l for l in corpus.owner_links if l.artifact.artifact_id == artifact_id)
    assert link.confirmed is False
    document = next(
        d for d in corpus.documents.documents if d.artifact.artifact_id == artifact_id
    )
    assert document.artifact.owner is None
    for card in corpus.documents.documents:
        if card.artifact.artifact_kind == "owner":
            assert artifact_id not in card.text


def test_owner_card_from_module_lists_only_confirmed_form(
    corpora: dict[str, SearchCorpus],
) -> None:
    corpus = corpora["config"]
    item = next(i for i in corpus.report.items if i.artifact_id == FORM_IDS["catalog"])
    assert item.owner_basis == "form_key"
    cards = [
        d for d in corpus.documents.documents if d.artifact.artifact_kind == "owner"
    ]
    catalog_card = next(
        c
        for c in cards
        if c.artifact.artifact_id == "owner:metadata_object:Catalog:Товары"
    )
    assert FORM_IDS["common"] not in catalog_card.text
    assert FORM_IDS["document"] not in catalog_card.text


# --- сохранность входов, офлайн и обезличенность -------------------------------


def test_inputs_unchanged_snapshot_separate_and_output_anonymized(
    tmp_path: Path,
    exports: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = {mode: _tree_digest(root) for mode, root in exports.items()}
    store = tmp_path / "store"
    store.mkdir()
    outputs: list[str] = []
    for mode, root in exports.items():
        snapshot = store / f"{mode}.json"
        runs = [
            ["build", str(root), "--mode", mode, "--snapshot", str(snapshot), *DEMO],
            [
                "query",
                str(root),
                "--mode",
                mode,
                "--query",
                SEMANTIC_TEXT,
                "--artifact-kind",
                "form",
                "--snapshot",
                str(snapshot),
                "--include-text",
                *DEMO,
            ],
            [
                "query",
                str(root),
                "--mode",
                mode,
                "--search-mode",
                "exact",
                "--query",
                COLLIDING_FORM_NAME if mode == "config" else "ФормаЗагрузки",
            ],
        ]
        for argv in runs:
            code, data, out, err = _run(capsys, *argv)
            assert code == 0, err
            assert out == to_canonical_json(data)
            outputs.append(out + err)
            _assert_clean(out, err, str(tmp_path), CONFIG_DIR, EXTERNAL_DIR)
        assert snapshot.is_file()
        assert not snapshot.is_relative_to(root)
    after = {mode: _tree_digest(root) for mode, root in exports.items()}
    assert after == before
    for text in outputs:
        assert "\\" not in text.replace("\\n", "").replace('\\"', "")
