"""Сквозные проверки общего поиска через search_api и search_cli (#371).

Синтетическая выгрузка → корпус → снимок → поиск → контекст → JSON CLI.
Эмбеддер — детерминированный ``hashed-bow-demo``: проверяется механика
публичных входов #328, а не релевантность. Модели, веса и сеть не
используются; обращение к сокетам блокируется guard-фикстурой.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_cli
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchFilters, to_canonical_json
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import EmbedderIdentity, SearchIndex, load_index

EXPORT_DIR = "secret-export-371"
CONFIGURATION = "Configuration.802.bsl"
CONSTANT_RATE = "Constant/Rate/Constant.obj.bsl"
SERVICE_RATE = "HTTPService/Rate/HTTPService.obj.bsl"
SERVICE_API = "HTTPService/Api/HTTPService.obj.bsl"
COMMON = "CommonModule/Shared/CommonModule.obj.bsl"
API_BODY_LINES = 40
API_TOTAL_LINES = API_BODY_LINES + 3
FILES = {
    CONFIGURATION: "Процедура ПриНачалеРаботыСистемы()\nКонецПроцедуры\n",
    CONSTANT_RATE: "Процедура ПередЗаписью(Отказ)\n  КурсВалюты = 1;\nКонецПроцедуры\n",
    SERVICE_RATE: "Функция ПолучитьКурс(Запрос)\n  Возврат 1;\nКонецФункции\n",
    SERVICE_API: (
        "Функция ПолучитьЗаказ(Запрос)\n"
        + "".join(f"  Строка{i} = {i};\n" for i in range(1, API_BODY_LINES + 1))
        + "  Возврат Неопределено;\nКонецФункции\n"
    ),
    COMMON: "Процедура ОбщаяПроцедура() Экспорт\nКонецПроцедуры\n",
}
NEGATIVE_TEXT = "нет такого объекта"
SEMANTIC_TEXT = "курс валюты"
AMBIGUOUS_NAME = "Rate"
DEMO = ("--embedder", "hashed-bow-demo")
TRUNCATION_BUDGETS = (900, 1500, 1800, 2100, 2300)
FORBIDDEN_MODULES = (
    "chromadb",
    "httpx",
    "requests",
    "sentence_transformers",
    "torch",
    "transformers",
    "urllib3",
)
ENVELOPE_KEYS = {"schema", "command", "status", "error", "snapshot", "context"}


class _OtherIdentityEmbedder(HashedBagOfWordsDemoEmbedder):
    """Та же механика, иная идентичность: даёт несовместимый снимок."""

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity("test-other-embedder", "1", 256, "other")


class _CountingEmbedder(HashedBagOfWordsDemoEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return super().embed(texts)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in #371 tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _build_export(root: Path) -> Path:
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _fragmentation(root: Path) -> FragmentationResult:
    return fragment_corpus(collect_search_corpus(root))


def _artifact_id(fragmentation: FragmentationResult, source_path: str) -> str:
    for fragment in fragmentation.fragments:
        artifact = fragment.document.artifact
        if artifact.source_path == source_path:
            return artifact.artifact_id
    raise AssertionError(source_path)


def _run(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, Any, str, str]:
    code = search_cli.main(list(argv))
    captured = capsys.readouterr()
    data = json.loads(captured.out) if captured.out else None
    return code, data, captured.out, captured.err


def _assert_clean(out: str, err: str, *secrets: str) -> None:
    assert "Traceback" not in out
    assert "Traceback" not in err
    assert len(err.splitlines()) <= 1
    for secret in secrets:
        assert secret not in out
        assert secret not in err
    loaded = [name for name in FORBIDDEN_MODULES if name in sys.modules]
    assert loaded == []


def _library(
    fragmentation: FragmentationResult,
    text: str,
    *,
    snapshot: Path | None = None,
    embedder: HashedBagOfWordsDemoEmbedder | None = None,
    include_text: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    index: SearchIndex | None = None
    if snapshot is not None and snapshot.exists():
        try:
            index = load_index(snapshot)
        except Exception:  # noqa: BLE001 — сценарии повреждённого снимка
            index = None
    kwargs.setdefault("max_chars", -1)
    context = search_with_context(
        fragmentation, text, index=index, embedder=embedder, **kwargs
    )
    return context.to_dict(include_text=include_text)


def _hit_ids(context: dict[str, Any]) -> list[str]:
    hits = context["dispatch"]["result"]["hits"]
    return [hit["artifact"]["artifact_id"] for hit in hits]


@pytest.fixture
def export(tmp_path: Path) -> Path:
    return _build_export(tmp_path / EXPORT_DIR)


@pytest.fixture
def fragmentation(export: Path) -> FragmentationResult:
    return _fragmentation(export)


@pytest.fixture
def snapshot(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    path = tmp_path / "snapshot.json"
    code, data, out, err = _run(
        capsys, "build", str(export), "--snapshot", str(path), *DEMO
    )
    assert code == 0
    assert data["status"] == "ok"
    _assert_clean(out, err, str(tmp_path), EXPORT_DIR)
    return path


# --- неоднозначность коротких имён -------------------------------------------


@pytest.mark.parametrize("mode", ["exact", "auto"])
def test_ambiguous_short_name_returns_full_candidate_set(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    expected_ids = {
        _artifact_id(fragmentation, CONSTANT_RATE),
        _artifact_id(fragmentation, SERVICE_RATE),
    }
    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        AMBIGUOUS_NAME,
        "--search-mode",
        mode,
        "--top-k",
        "1",
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    assert data["status"] == "ok"
    assert data["error"] is None
    context = data["context"]
    assert context["search_kind"] == "ambiguous"
    assert context["dispatch"]["path"] == ["exact"]
    assert context["dispatch"]["result"]["status"] == "ambiguous"
    assert set(_hit_ids(context)) == expected_ids
    assert len(_hit_ids(context)) == 2
    assert context["candidates_total"] == 2
    assert context["candidates_shown"] == 2
    assert context["candidates_omitted"] == 0
    for hit in context["dispatch"]["result"]["hits"]:
        assert hit["method"] == "exact_name"
        assert hit["matched_field"] == "owner_name"
        assert hit["similarity"] is None
    for block in context["included"]:
        assert block["candidate_only"] is True
        assert block["included_span"] is None
        assert block["source_document_ids"] == []
    assert context["warnings"]
    assert context == _library(
        fragmentation,
        AMBIGUOUS_NAME,
        snapshot=snapshot,
        embedder=HashedBagOfWordsDemoEmbedder(),
        search_mode=mode,
        top_k=1,
    )
    _assert_clean(out, err, str(export), EXPORT_DIR)


def test_ambiguous_and_exact_do_not_need_embedder(
    export: Path,
    fragmentation: FragmentationResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    counting = _CountingEmbedder()
    index = build_search_snapshot(fragmentation, counting, export.parent / "count.json")
    assert counting.calls == 1
    for text in (AMBIGUOUS_NAME, _artifact_id(fragmentation, SERVICE_API)):
        context = search_with_context(
            fragmentation, text, index=index, embedder=counting, max_chars=-1
        )
        assert context.dispatch.result.status in {"exact", "ambiguous"}
        assert context.dispatch.path == ("exact",)
    assert counting.calls == 1
    code, data, _, _ = _run(
        capsys, "query", str(export), "--query", AMBIGUOUS_NAME, "--search-mode", "auto"
    )
    assert code == 0
    assert data["context"]["search_kind"] == "ambiguous"
    assert data["snapshot"] == {
        "provided": False,
        "loaded": False,
        "error_code": None,
        "compatibility": None,
        "freshness": None,
    }


# --- отрицательные запросы и фильтры ------------------------------------------


def test_negative_exact_is_empty_not_error(
    export: Path,
    fragmentation: FragmentationResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, data, out, err = _run(
        capsys, "query", str(export), "--query", NEGATIVE_TEXT, "--search-mode", "exact"
    )
    assert code == 0
    assert data["status"] == "ok"
    assert data["error"] is None
    context = data["context"]
    assert context["search_kind"] == "empty"
    assert context["dispatch"]["path"] == ["exact"]
    assert _hit_ids(context) == []
    assert context == _library(fragmentation, NEGATIVE_TEXT, search_mode="exact")
    assert err == ""
    _assert_clean(out, err, str(export), EXPORT_DIR)


@pytest.mark.parametrize("mode", ["auto", "semantic"])
def test_negative_without_embedder_is_index_not_ready(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    # Снимок указан, эмбеддер — нет: скрытой загрузки нет, оценка не выполняется.
    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        NEGATIVE_TEXT,
        "--search-mode",
        mode,
        "--snapshot",
        str(snapshot),
    )
    assert code == 3
    assert data["status"] == "degraded"
    assert data["error"] == {"code": "index_not_ready", "message": "index is not ready"}
    assert data["snapshot"] == {
        "provided": True,
        "loaded": True,
        "error_code": None,
        "compatibility": None,
        "freshness": None,
    }
    context = data["context"]
    assert context["search_kind"] == "error"
    assert context["dispatch"]["index_status"] is None
    expected_path = ["exact", "semantic"] if mode == "auto" else ["semantic"]
    assert context["dispatch"]["path"] == expected_path
    assert context == _library(
        fragmentation, NEGATIVE_TEXT, snapshot=snapshot, search_mode=mode
    )
    assert "index_not_ready" in err
    _assert_clean(out, err, str(export), EXPORT_DIR)


def test_negative_with_snapshot_falls_back_to_semantic(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        NEGATIVE_TEXT,
        "--top-k",
        "3",
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    assert data["status"] == "ok"
    assert data["error"] is None
    context = data["context"]
    assert context["search_kind"] == "semantic"
    assert context["dispatch"]["path"] == ["exact", "semantic"]
    ids = _hit_ids(context)
    assert 0 < len(ids) <= 3
    assert len(set(ids)) == len(ids)
    for hit in context["dispatch"]["result"]["hits"]:
        assert hit["method"] == "semantic"
        assert hit["similarity"] is not None
    assert context["dispatch"]["index_status"]["compatibility"] == "compatible"
    assert context["dispatch"]["index_status"]["freshness"] == "fresh"
    assert context == _library(
        fragmentation,
        NEGATIVE_TEXT,
        snapshot=snapshot,
        embedder=HashedBagOfWordsDemoEmbedder(),
        top_k=3,
    )
    _assert_clean(out, err, str(export), EXPORT_DIR)


def test_filters_exact_fallback_and_semantic(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    form_filter = SearchFilters(artifact_kinds=("form",))
    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--search-mode",
        "exact",
        "--artifact-kind",
        "form",
    )
    assert code == 0
    context = data["context"]
    assert context["search_kind"] == "empty"
    assert context["dispatch"]["exact_filtered_out"] == 1
    assert context["dispatch"]["path"] == ["exact"]
    assert context == _library(
        fragmentation, api_id, search_mode="exact", filters=form_filter
    )

    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--artifact-kind",
        "form",
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    context = data["context"]
    assert context["dispatch"]["path"] == ["exact", "semantic"]
    assert context["dispatch"]["exact_filtered_out"] == 1
    assert context["search_kind"] == "empty"
    assert _hit_ids(context) == []
    assert data["error"] is None
    assert context == _library(
        fragmentation,
        api_id,
        snapshot=snapshot,
        embedder=HashedBagOfWordsDemoEmbedder(),
        filters=form_filter,
    )

    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--owner-name",
        AMBIGUOUS_NAME,
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    context = data["context"]
    assert context["search_kind"] == "semantic"
    hits = context["dispatch"]["result"]["hits"]
    assert hits
    for hit in hits:
        assert hit["artifact"]["owner"]["owner_name"] == AMBIGUOUS_NAME
    assert context == _library(
        fragmentation,
        SEMANTIC_TEXT,
        snapshot=snapshot,
        embedder=HashedBagOfWordsDemoEmbedder(),
        search_mode="semantic",
        filters=SearchFilters(owner_names=(AMBIGUOUS_NAME,)),
    )


# --- координаты и усечение ----------------------------------------------------


def test_coordinates_match_included_code_and_truncation(
    export: Path,
    fragmentation: FragmentationResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    source_lines = FILES[SERVICE_API].splitlines()
    assert len(source_lines) == API_TOTAL_LINES

    code, data, out, err = _run(
        capsys, "query", str(export), "--query", api_id, "--include-text"
    )
    assert code == 0
    full = data["context"]
    assert full["status"] == "built"
    assert [block["included_span"] for block in full["included"]] == [
        {"start_line": 1, "end_line": API_TOTAL_LINES}
    ]
    for line in source_lines:
        assert line in full["text"]
    assert full == _library(fragmentation, api_id, include_text=True)
    _assert_clean(out, err, str(export), EXPORT_DIR)
    full_chars = full["text_chars"]
    assert len(full["text"]) == full_chars

    statuses: dict[int, str] = {}
    partial_spans: list[dict[str, int]] = []
    budgets = (*TRUNCATION_BUDGETS, full_chars - 50, full_chars - 1)
    for budget in budgets:
        assert 0 < budget < full_chars
        code, data, out, err = _run(
            capsys,
            "query",
            str(export),
            "--query",
            api_id,
            "--include-text",
            "--max-chars",
            str(budget),
        )
        context = data["context"]
        statuses[budget] = context["status"]
        assert context["search_kind"] == "exact"
        assert context["dispatch"]["result"]["status"] == "exact"
        assert _hit_ids(context) == [api_id]
        assert len(context["text"]) <= budget
        assert data["error"] is None
        assert context == _library(
            fragmentation, api_id, include_text=True, max_chars=budget
        )
        _assert_clean(out, err, str(export), EXPORT_DIR)
        if context["status"] == "insufficient_budget":
            assert code == 3
            assert data["status"] == "degraded"
            assert context["included"] == []
            assert [block["reason"] for block in context["omitted"]] == ["budget"]
            assert "insufficient_budget" in err
            continue
        assert context["status"] == "truncated"
        assert code == 0
        assert data["status"] == "ok"
        assert context["text"]
        if not context["included"]:
            # Заголовки уместились, код опущен: по бюджету либо потому, что не
            # поместился префикс первой строки (line_too_long). Hit виден.
            reasons = [block["reason"] for block in context["omitted"]]
            assert reasons
            assert set(reasons) <= {"budget", "line_too_long"}, reasons
            for line in source_lines:
                assert line not in context["text"]
            continue
        assert len(context["included"]) == 1
        span = context["included"][0]["included_span"]
        assert 1 <= span["start_line"] <= span["end_line"] <= API_TOTAL_LINES
        assert span["end_line"] - span["start_line"] + 1 < API_TOTAL_LINES
        inside = source_lines[span["start_line"] - 1 : span["end_line"]]
        outside = (
            source_lines[: span["start_line"] - 1] + source_lines[span["end_line"] :]
        )
        for line in inside:
            assert line in context["text"]
        for line in outside:
            assert line not in context["text"]
        partial_spans.append(span)
    assert "truncated" in statuses.values(), statuses
    assert partial_spans, statuses


def test_zero_budget_is_insufficient_without_hiding_hit(
    export: Path,
    fragmentation: FragmentationResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    code, data, _, err = _run(
        capsys, "query", str(export), "--query", api_id, "--max-chars", "0"
    )
    assert code == 3
    assert data["status"] == "degraded"
    assert data["error"] is None
    context = data["context"]
    assert context["status"] == "insufficient_budget"
    assert _hit_ids(context) == [api_id]
    assert context["candidates_omitted"] == 1
    assert context == _library(fragmentation, api_id, max_chars=0)
    assert "context:insufficient_budget" in err


# --- состояния снимка и эмбеддера ---------------------------------------------


def _write_corrupt_json(path: Path) -> None:
    path.write_bytes(b"{not json")


def _tamper_vector(path: Path) -> None:
    raw = json.loads(path.read_bytes().decode("utf-8"))
    raw["entries"][0]["vector"][0] = raw["entries"][0]["vector"][0] + 0.5
    path.write_bytes(json.dumps(raw, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize(
    ("prepare", "error_code"),
    [
        (None, "snapshot_not_found"),
        (_write_corrupt_json, "corrupt_snapshot"),
        (_tamper_vector, "corrupt_snapshot"),
    ],
)
def test_missing_or_corrupt_snapshot_keeps_exact_and_degrades(
    tmp_path: Path,
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
    prepare: object,
    error_code: str,
) -> None:
    broken = tmp_path / "broken.json"
    if callable(prepare):
        broken.write_bytes(snapshot.read_bytes())
        prepare(broken)
    api_id = _artifact_id(fragmentation, SERVICE_API)
    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--snapshot",
        str(broken),
        *DEMO,
    )
    assert code == 3
    assert data["status"] == "degraded"
    assert data["error"] is None
    assert data["snapshot"] == {
        "provided": True,
        "loaded": False,
        "error_code": error_code,
        "compatibility": None,
        "freshness": None,
    }
    assert data["context"]["search_kind"] == "exact"
    assert _hit_ids(data["context"]) == [api_id]
    assert data["context"] == _library(fragmentation, api_id)
    assert f"snapshot:{error_code}" in err
    _assert_clean(out, err, str(tmp_path), EXPORT_DIR)

    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--snapshot",
        str(broken),
        *DEMO,
    )
    assert code == 3
    assert data["error"]["code"] == "index_not_ready"
    assert data["snapshot"]["error_code"] == error_code
    assert data["snapshot"]["loaded"] is False
    _assert_clean(out, err, str(tmp_path), EXPORT_DIR)


def test_incompatible_snapshot_blocks_only_semantic(
    tmp_path: Path,
    export: Path,
    fragmentation: FragmentationResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = tmp_path / "other.json"
    build_search_snapshot(fragmentation, _OtherIdentityEmbedder(), other)
    api_id = _artifact_id(fragmentation, SERVICE_API)
    code, data, out, err = _run(
        capsys, "query", str(export), "--query", api_id, "--snapshot", str(other), *DEMO
    )
    assert code == 3
    assert data["status"] == "degraded"
    assert data["error"] is None
    assert data["snapshot"] == {
        "provided": True,
        "loaded": True,
        "error_code": None,
        "compatibility": "incompatible",
        "freshness": "fresh",
    }
    assert data["context"]["search_kind"] == "exact"
    assert data["context"] == _library(
        fragmentation, api_id, snapshot=other, embedder=HashedBagOfWordsDemoEmbedder()
    )
    assert "snapshot:incompatible" in err

    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--snapshot",
        str(other),
        *DEMO,
    )
    assert code == 3
    assert data["error"]["code"] == "incompatible_index"
    context = data["context"]
    assert context["search_kind"] == "error"
    assert context["dispatch"]["index_status"]["compatibility"] == "incompatible"
    assert context == _library(
        fragmentation,
        SEMANTIC_TEXT,
        snapshot=other,
        embedder=HashedBagOfWordsDemoEmbedder(),
        search_mode="semantic",
    )
    _assert_clean(out, err, str(tmp_path), EXPORT_DIR)


def test_stale_snapshot_uses_content_hashes_not_mtime(
    export: Path,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(_fragmentation(export), SERVICE_API)
    common = export / COMMON
    stat = common.stat()
    os.utime(common, (stat.st_atime + 3600, stat.st_mtime + 3600))
    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    assert data["snapshot"]["freshness"] == "fresh"

    (export / CONSTANT_RATE).write_bytes(
        "Процедура ПередЗаписью(Отказ)\n  КурсВалюты = 2;\nКонецПроцедуры\n".encode()
    )
    fragmentation = _fragmentation(export)
    code, data, _, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 3
    assert data["status"] == "degraded"
    assert data["error"] is None
    assert data["snapshot"]["compatibility"] == "compatible"
    assert data["snapshot"]["freshness"] == "stale"
    assert data["context"]["search_kind"] == "exact"
    assert data["context"] == _library(
        fragmentation,
        api_id,
        snapshot=snapshot,
        embedder=HashedBagOfWordsDemoEmbedder(),
    )
    assert "snapshot:stale" in err

    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 3
    assert data["error"]["code"] == "incompatible_index"
    assert data["context"]["dispatch"]["index_status"]["freshness"] == "stale"


def test_fresh_snapshot_is_assessed_only_with_embedder(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    code, data, _, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        api_id,
        "--snapshot",
        str(snapshot),
        *DEMO,
    )
    assert code == 0
    assert err == ""
    assert data["snapshot"] == {
        "provided": True,
        "loaded": True,
        "error_code": None,
        "compatibility": "compatible",
        "freshness": "fresh",
    }
    code, data, _, _ = _run(
        capsys, "query", str(export), "--query", api_id, "--snapshot", str(snapshot)
    )
    assert code == 0
    assert data["snapshot"]["compatibility"] is None
    assert data["snapshot"]["freshness"] is None
    assert data["snapshot"]["loaded"] is True


# --- повторяемость и согласованность -------------------------------------------


def test_two_runs_are_byte_identical(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outputs: list[str] = []
    paths = [tmp_path / "first" / "a.json", tmp_path / "second-dir" / "b.json"]
    for path in paths:
        path.parent.mkdir()
        code, _, out, err = _run(
            capsys, "build", str(export), "--snapshot", str(path), *DEMO
        )
        assert code == 0
        assert err == ""
        outputs.append(out)
    assert outputs[0] == outputs[1]
    assert paths[0].read_bytes() == paths[1].read_bytes()
    assert outputs[0] == to_canonical_json(json.loads(outputs[0]))

    queries: list[str] = []
    for path in paths:
        code, _, out, _ = _run(
            capsys,
            "query",
            str(export),
            "--query",
            NEGATIVE_TEXT,
            "--include-text",
            "--snapshot",
            str(path),
            *DEMO,
        )
        assert code == 0
        queries.append(out)
    assert queries[0] == queries[1]
    assert queries[0] == to_canonical_json(json.loads(queries[0]))
    for secret in (str(tmp_path), "first", "second-dir", EXPORT_DIR):
        assert secret not in queries[0]


def test_cli_context_equals_library_projection(
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    index = load_index(snapshot)
    embedder = HashedBagOfWordsDemoEmbedder()
    cases = [
        (api_id, "exact"),
        (AMBIGUOUS_NAME, "auto"),
        (NEGATIVE_TEXT, "auto"),
        (SEMANTIC_TEXT, "semantic"),
    ]
    for text, mode in cases:
        code, data, _, _ = _run(
            capsys,
            "query",
            str(export),
            "--query",
            text,
            "--search-mode",
            mode,
            "--top-k",
            "2",
            "--include-text",
            "--snapshot",
            str(snapshot),
            *DEMO,
        )
        assert code == 0
        assert set(data) == ENVELOPE_KEYS
        expected = search_with_context(
            fragmentation,
            text,
            search_mode=mode,
            top_k=2,
            index=index,
            embedder=embedder,
            max_chars=-1,
        )
        assert data["context"] == expected.to_dict(include_text=True)
        assert data["context"]["schema"] == "search_context/1"
        assert data["schema"] == "search_cli/1"


# --- обезличенность и guards ----------------------------------------------------


def test_outputs_are_anonymized_across_scenarios(
    tmp_path: Path,
    export: Path,
    fragmentation: FragmentationResult,
    snapshot: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_id = _artifact_id(fragmentation, SERVICE_API)
    missing = tmp_path / "secret-missing.json"
    scenarios: list[list[str]] = [
        ["query", str(export), "--query", api_id, "--include-text"],
        ["query", str(export), "--query", AMBIGUOUS_NAME, "--max-chars", "120"],
        ["query", str(export), "--query", NEGATIVE_TEXT],
        ["query", str(export), "--query", api_id, "--snapshot", str(missing), *DEMO],
        [
            "query",
            str(export),
            "--query",
            SEMANTIC_TEXT,
            "--search-mode",
            "semantic",
            "--snapshot",
            str(snapshot),
            *DEMO,
        ],
        ["query", str(tmp_path / "secret-root"), "--query", api_id],
        [
            "build",
            str(export),
            "--snapshot",
            str(tmp_path / "no-dir" / "s.json"),
            *DEMO,
        ],
    ]
    for argv in scenarios:
        code, data, out, err = _run(capsys, *argv)
        assert code in {0, 2, 3, 5}
        if data is not None:
            assert out == to_canonical_json(data)
            assert data["schema"] == "search_cli/1"
        else:
            assert out == ""
            assert err
        _assert_clean(
            out,
            err,
            str(tmp_path),
            EXPORT_DIR,
            "secret-missing",
            "secret-root",
            "no-dir",
        )
