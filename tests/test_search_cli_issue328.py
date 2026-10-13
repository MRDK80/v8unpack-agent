"""Opt-in CLI общего поиска search_cli (#367, часть B задачи #328)."""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import cli, search_api, search_cli
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchFilters, to_canonical_json
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import load_index

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
SEMANTIC_TEXT = "курсвалюты"


def _build_export(root: Path) -> Path:
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _fragmentation(root: Path) -> FragmentationResult:
    return fragment_corpus(collect_search_corpus(root))


def _artifact_id(root: Path, source_path: str) -> str:
    for fragment in _fragmentation(root).fragments:
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
    assert "Traceback" not in err
    assert len(err.splitlines()) <= 1
    for secret in secrets:
        assert secret not in out
        assert secret not in err


@pytest.fixture
def export(tmp_path: Path) -> Path:
    return _build_export(tmp_path / "export")


@pytest.fixture
def snapshot(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    path = tmp_path / "snapshot.json"
    code, _, _, _ = _run(
        capsys,
        "build",
        str(export),
        "--snapshot",
        str(path),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 0
    return path


def test_build_writes_snapshot(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "snapshot.json"
    code, data, out, err = _run(
        capsys,
        "build",
        str(export),
        "--snapshot",
        str(path),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 0
    assert err == ""
    assert out == to_canonical_json(json.loads(out))
    fragmentation = _fragmentation(export)
    report = fragmentation.corpus_report
    assert report is not None
    index = load_index(path)
    assert data == {
        "schema": "search_cli/1",
        "command": "build",
        "status": "ok",
        "error": None,
        "corpus": {"total": len(report.items), "counts": report.counts()},
        "fragments": len(fragmentation.fragments),
        "snapshot": {
            "schema": "search_index/1",
            "representation_version": fragmentation.representation_version,
            "embedder": HashedBagOfWordsDemoEmbedder().identity.to_dict(),
            "entries": len(index.entries),
        },
    }
    assert index.assess(
        HashedBagOfWordsDemoEmbedder().identity, fragmentation
    ).queryable
    _assert_clean(out, err, str(tmp_path))


def test_build_requires_embedder(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "snapshot.json"
    code, data, _, err = _run(
        capsys, "build", str(export), "--snapshot", str(path)
    )
    assert code == 2
    assert data is None
    assert not path.exists()
    _assert_clean("", err, str(tmp_path))


def test_build_write_error(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "missing-dir" / "snapshot.json"
    code, data, out, err = _run(
        capsys,
        "build",
        str(export),
        "--snapshot",
        str(path),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 5
    assert data is not None
    assert data["status"] == "error"
    assert data["error"] == {
        "code": "snapshot_write_failed",
        "message": "snapshot could not be written",
    }
    assert data["snapshot"] is None
    _assert_clean(out, err, str(tmp_path))


class _FailingEmbedder(HashedBagOfWordsDemoEmbedder):
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        raise OSError("secret-path/leak")


class _ZeroEmbedder(HashedBagOfWordsDemoEmbedder):
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.0,) * 256 for _ in texts)


@pytest.mark.parametrize(
    ("embedder", "error_code"),
    [(_FailingEmbedder, "embedder_failed"), (_ZeroEmbedder, "invalid_vector")],
)
def test_build_embedder_errors(
    tmp_path: Path,
    export: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    embedder: type[HashedBagOfWordsDemoEmbedder],
    error_code: str,
) -> None:
    monkeypatch.setattr(search_cli, "HashedBagOfWordsDemoEmbedder", embedder)
    path = tmp_path / "snapshot.json"
    code, data, out, err = _run(
        capsys,
        "build",
        str(export),
        "--snapshot",
        str(path),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 4
    assert data is not None
    assert data["status"] == "error"
    assert data["error"]["code"] == error_code
    assert not path.exists()
    _assert_clean(out, err, str(tmp_path), "secret")


def test_exact_query_matches_library(
    tmp_path: Path, export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = _artifact_id(export, SERVICE)
    code, data, out, err = _run(capsys, "query", str(export), "--query", text)
    assert code == 0
    assert err == ""
    assert data is not None
    assert data["schema"] == "search_cli/1"
    assert data["command"] == "query"
    assert data["status"] == "ok"
    assert data["error"] is None
    assert data["snapshot"] == {
        "provided": False,
        "loaded": False,
        "error_code": None,
        "compatibility": None,
        "freshness": None,
    }
    expected = search_with_context(_fragmentation(export), text, max_chars=-1)
    assert data["context"] == expected.to_dict()
    assert expected.dispatch.result.status == "exact"
    assert "text" not in data["context"]
    _assert_clean(out, err, str(tmp_path))


@pytest.mark.parametrize("mode", ["auto", "semantic"])
def test_without_embedder_semantic_step_not_ready(
    export: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    code, data, _, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        mode,
    )
    assert code == 3
    assert data is not None
    assert data["status"] == "degraded"
    assert data["error"] == {
        "code": "index_not_ready",
        "message": "index is not ready",
    }
    context = data["context"]
    assert context["dispatch"]["result"]["status"] == "error"
    assert "index_not_ready" in err


def test_semantic_query_matches_library_on_same_snapshot(
    export: Path, snapshot: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, data, _, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        "--top-k",
        "2",
        "--snapshot",
        str(snapshot),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 0
    assert err == ""
    assert data is not None
    assert data["snapshot"] == {
        "provided": True,
        "loaded": True,
        "error_code": None,
        "compatibility": "compatible",
        "freshness": "fresh",
    }
    expected = search_with_context(
        _fragmentation(export),
        SEMANTIC_TEXT,
        search_mode="semantic",
        top_k=2,
        index=load_index(snapshot),
        embedder=HashedBagOfWordsDemoEmbedder(),
        max_chars=-1,
    )
    assert expected.dispatch.result.status == "semantic"
    assert data["context"] == expected.to_dict()


def test_snapshot_without_embedder_is_not_assessed(
    export: Path, snapshot: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = _artifact_id(export, SERVICE)
    code, data, _, _ = _run(
        capsys, "query", str(export), "--query", text, "--snapshot", str(snapshot)
    )
    assert code == 0
    assert data is not None
    assert data["snapshot"] == {
        "provided": True,
        "loaded": True,
        "error_code": None,
        "compatibility": None,
        "freshness": None,
    }


def _write_corrupt(path: Path) -> None:
    path.write_text("{not json", encoding="utf-8")


@pytest.mark.parametrize(
    ("prepare", "error_code"),
    [(None, "snapshot_not_found"), (_write_corrupt, "corrupt_snapshot")],
)
def test_snapshot_failure_keeps_exact_search(
    tmp_path: Path,
    export: Path,
    capsys: pytest.CaptureFixture[str],
    prepare: object,
    error_code: str,
) -> None:
    path = tmp_path / "snapshot.json"
    if callable(prepare):
        prepare(path)
    text = _artifact_id(export, SERVICE)
    code, data, out, err = _run(
        capsys,
        "query",
        str(export),
        "--query",
        text,
        "--snapshot",
        str(path),
        "--embedder",
        "hashed-bow-demo",
    )
    assert code == 3
    assert data is not None
    assert data["status"] == "degraded"
    assert data["error"] is None
    snapshot = data["snapshot"]
    assert snapshot["provided"] is True
    assert snapshot["loaded"] is False
    assert snapshot["error_code"] == error_code
    assert data["context"]["search_kind"] == "exact"
    expected = search_with_context(_fragmentation(export), text, max_chars=-1)
    assert data["context"] == expected.to_dict()
    assert error_code in err
    _assert_clean(out, err, str(tmp_path))


def test_stale_snapshot_degrades(
    export: Path, snapshot: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (export / CONSTANT).write_bytes(
        "Процедура ПередЗаписью(Отказ)\n  КурсВалюты = 2;\nКонецПроцедуры\n".encode()
    )
    text = _artifact_id(export, SERVICE)
    common = ("--snapshot", str(snapshot), "--embedder", "hashed-bow-demo")
    code, data, _, _ = _run(capsys, "query", str(export), "--query", text, *common)
    assert code == 3
    assert data is not None
    assert data["snapshot"]["freshness"] == "stale"
    assert data["context"]["search_kind"] == "exact"
    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        SEMANTIC_TEXT,
        "--search-mode",
        "semantic",
        *common,
    )
    assert code == 3
    assert data is not None
    assert data["error"]["code"] == "incompatible_index"


def test_insufficient_budget_degrades(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = _artifact_id(export, SERVICE)
    code, data, _, err = _run(
        capsys, "query", str(export), "--query", text, "--max-chars", "0"
    )
    assert code == 3
    assert data is not None
    assert data["context"]["status"] == "insufficient_budget"
    assert "insufficient_budget" in err


def test_include_text(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = _artifact_id(export, SERVICE)
    code, data, _, _ = _run(
        capsys, "query", str(export), "--query", text, "--include-text"
    )
    assert code == 0
    assert data is not None
    expected = search_with_context(_fragmentation(export), text, max_chars=-1)
    assert data["context"] == expected.to_dict(include_text=True)
    assert "ПолучитьЗаказ" in data["context"]["text"]


def test_filters_reach_query(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = _artifact_id(export, SERVICE)
    code, data, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        text,
        "--search-mode",
        "exact",
        "--artifact-kind",
        "form",
        "--owner-kind",
        "metadata_object",
        "--metadata-type",
        "HTTPService",
        "--owner-name",
        "Api",
    )
    filters = SearchFilters(
        artifact_kinds=("form",),
        owner_kinds=("metadata_object",),
        metadata_types=("HTTPService",),
        owner_names=("Api",),
    )
    expected = search_with_context(
        _fragmentation(export),
        text,
        search_mode="exact",
        filters=filters,
        max_chars=-1,
    )
    assert code == 0
    assert data is not None
    assert data["context"] == expected.to_dict()
    assert expected.dispatch.exact_filtered_out == 1


def test_corpus_options_are_passed(
    export: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def spy(root: Path, **kwargs: object) -> object:
        calls.append(kwargs)
        return collect_search_corpus(root)

    monkeypatch.setattr(search_cli, "collect_search_corpus", spy)
    code, _, _, _ = _run(
        capsys,
        "query",
        str(export),
        "--query",
        "x",
        "--search-mode",
        "exact",
        "--mode",
        "external",
        "--skip-common-modules",
        "--skip-module-group",
        "services",
        "--skip-module-group",
        "commands",
    )
    assert code == 0
    assert calls == [
        {
            "mode": "external",
            "include_common_modules": False,
            "module_groups": (
                "configuration",
                "metadata",
                "record-sets",
                "value-managers",
            ),
        }
    ]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["query", "{root}"],
        ["query", "{root}", "--query", ""],
        ["query", "{root}", "--query", "x", "--top-k", "0"],
        ["query", "{root}", "--query", "x", "--max-chars", "-2"],
        ["query", "{root}", "--query", "x", "--search-mode", "fuzzy"],
        ["query", "{root}", "--query", "x", "--embedder", "{root}/model"],
        ["query", "{root}", "--query", "x", "--artifact-kind", "{root}"],
        ["query", "{root}", "--query", "x", "--module-group", "bogus"],
        [
            "query",
            "{root}",
            "--query",
            "x",
            "--module-group",
            "services",
            "--skip-module-group",
            "commands",
        ],
        ["build", "{root}", "--snapshot", "{root}/s.json"],
    ],
)
def test_bad_arguments(
    export: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    resolved = [item.replace("{root}", str(export)) for item in argv]
    code, data, out, err = _run(capsys, *resolved)
    assert code == 2
    assert data is None
    assert out == ""
    assert err
    _assert_clean(out, err, str(export))


def test_export_root_not_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "secret-root"
    code, data, out, err = _run(capsys, "query", str(missing), "--query", "x")
    assert code == 2
    assert data is None
    _assert_clean(out, err, str(tmp_path), "secret-root")


def test_corpus_failure_is_fatal(
    export: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("secret-path/leak")

    monkeypatch.setattr(search_api, "scan_forms", boom)
    code, data, out, err = _run(capsys, "query", str(export), "--query", "x")
    assert code == 4
    assert data is not None
    assert data["status"] == "error"
    assert data["error"] == {"code": "scan_failed", "message": "form scan failed"}
    assert data["context"] is None
    _assert_clean(out, err, str(export), "secret")


def test_internal_error_is_sanitized(
    export: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"secret {export}")

    monkeypatch.setattr(search_cli, "fragment_corpus", boom)
    code, data, out, err = _run(capsys, "query", str(export), "--query", "x")
    assert code == 4
    assert data is not None
    assert data["error"]["code"] == "internal_error"
    assert "RuntimeError" in err
    _assert_clean(out, err, str(export), "secret")


def test_help_marks_demo_embedder(capsys: pytest.CaptureFixture[str]) -> None:
    assert search_cli.main(["query", "--help"]) == 0
    out = capsys.readouterr().out
    assert "hashed-bow-demo" in out
    assert "механика" in out


def test_exit_codes_come_from_cli() -> None:
    source = (REPO_ROOT / "v8unpack_agent" / "search_cli.py").read_text("utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "v8unpack_agent.cli"
        for alias in node.names
    }
    assert imported == {
        "EXIT_BAD_INPUT",
        "EXIT_DEGRADED",
        "EXIT_FATAL",
        "EXIT_OK",
        "EXIT_WRITE_ERROR",
    }
    assert (cli.EXIT_OK, cli.EXIT_BAD_INPUT, cli.EXIT_DEGRADED) == (0, 2, 3)
    assert (cli.EXIT_FATAL, cli.EXIT_WRITE_ERROR) == (4, 5)


def test_module_has_no_hidden_providers() -> None:
    source = (REPO_ROOT / "v8unpack_agent" / "search_cli.py").read_text("utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {
        "__future__",
        "argparse",
        "collections",
        "pathlib",
        "sys",
        "typing",
        "v8unpack_agent",
    }


def test_console_scripts_unchanged() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
    assert "search_cli" not in text
    assert 'v8unpack-agent-run = "v8unpack_agent.cli:main"' in text


def test_changelog_references_issue() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text("utf-8")
    assert "#367" in text
    assert "search_cli" in text
