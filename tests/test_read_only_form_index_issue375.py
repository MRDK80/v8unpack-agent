"""Read-only form consumers and explicit parser/pipeline writes (#375)."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from v8unpack_agent import form_context, runner
from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_summary import build_form_summary
from v8unpack_agent.pipeline import unpack_all_forms
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_api import collect_search_corpus, search_with_context
from v8unpack_agent.search_cli import main as search_main
from v8unpack_agent.search_fragments import fragment_corpus

INDEX_NAME = "form_elements_index.json"
PAYLOAD = {"tree": [{"name": "SyntheticValue", "type": "InputField", "data_path": "SyntheticValue"}]}
STRUCTURAL_KEYS = {"name", "type", "path", "parent", "parent_path", "page", "source", "data_path", "handler"}


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    result = {}
    for path in sorted(root.rglob("*")):
        assert not path.is_symlink()
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (
                stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        else:
            assert path.is_dir()
            result[path.relative_to(root).as_posix() + "/"] = (0, 0, "directory")
    return result


def _export(root: Path, mode: str) -> tuple[Path, Path]:
    if mode == "external":
        form = root / "Synthetic.epf" / "Form" / "SyntheticForm"
        container = "Form"
    elif mode == "catalog":
        form = root / "Catalog" / "SyntheticOwner" / "CatalogForm" / "SyntheticForm"
        container = "CatalogForm"
    else:
        form = root / "CommonForm" / "SyntheticForm"
        container = "CommonForm"
    form.mkdir(parents=True)
    (form / f"{container}.json").write_text("{}", encoding="utf-8")
    (form / f"{container}.elem.json").write_text(json.dumps(PAYLOAD), encoding="utf-8")
    (form / f"{container}.obj.bsl").write_text("Procedure SyntheticAction()\nEndProcedure\n", encoding="utf-8")
    if mode != "external":
        (root / "Configuration.802.bsl").write_text("Procedure SyntheticStart()\nEndProcedure\n", encoding="utf-8")
    return root, form


def _set_index(form: Path, exists: bool) -> None:
    path = form / INDEX_NAME
    path.unlink(missing_ok=True)
    if exists:
        path.write_text('{"sentinel": "preserve"}\n', encoding="utf-8")
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))


@pytest.mark.parametrize("mode", ["common", "catalog", "external"])
@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("operation", ["parser", "scan", "summary", "context", "corpus", "search", "build", "query", "runner"])
def test_reading_preserves_export_tree(tmp_path: Path, mode: str, exists: bool, operation: str, capsys) -> None:
    root, form = _export(tmp_path / "export", mode)
    scan_mode = "external" if mode == "external" else "config"
    entry = scan_forms(root, mode=scan_mode).forms[0]
    fragmentation = fragment_corpus(collect_search_corpus(root, mode=scan_mode))
    assert fragmentation.fragments
    query = fragmentation.fragments[0].document.artifact.artifact_id
    _set_index(form, exists)
    before = _snapshot(root)
    if operation == "parser":
        assert parse_elem_json(form, write_index=False).elem_index_ok
    elif operation == "scan":
        assert scan_forms(root, mode=scan_mode).total == 1
    elif operation == "summary":
        assert build_form_summary(form).elements
    elif operation == "context":
        assert form_context.build_form_context(entry, root).summary.elements
    elif operation == "corpus":
        assert collect_search_corpus(root, mode=scan_mode).report.items
    elif operation == "search":
        context = search_with_context(fragmentation, query, max_chars=100000)
        assert context.dispatch.result.status == "exact"
    elif operation in {"build", "query"}:
        args = [operation, str(root), "--mode", scan_mode]
        if operation == "build":
            args += ["--snapshot", str(tmp_path / "snapshot.json"), "--embedder", "hashed-bow-demo"]
        else:
            args += ["--query", query, "--search-mode", "exact"]
        assert search_main(args) == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["status"] == "ok"
        if operation == "build":
            assert (tmp_path / "snapshot.json").is_file()
    else:
        outcome = runner.run_pipeline(runner.RunOptions(export_root=root, mode=scan_mode, include_skd=False))
        assert outcome.completed
        assert not outcome.degraded
    assert _snapshot(root) == before


@pytest.mark.parametrize("exists", [False, True])
def test_default_parser_remains_write_compatible(tmp_path: Path, exists: bool) -> None:
    root, form = _export(tmp_path / "export", "common")
    _set_index(form, exists)
    read_result = parse_elem_json(form, write_index=False)
    before = _snapshot(root)
    write_result = parse_elem_json(form)
    assert write_result == read_result
    assert json.loads((form / INDEX_NAME).read_text("utf-8")) == {
        "form": form.name, "elements": write_result.elements
    }
    after = _snapshot(root)
    key = (form / INDEX_NAME).relative_to(root).as_posix()
    assert {p: data for p, data in after.items() if p != key} == {p: data for p, data in before.items() if p != key}
    assert inspect.signature(parse_elem_json).parameters["write_index"].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(parse_elem_json).parameters["write_index"].default is True


def test_read_parser_does_not_attempt_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, form = _export(tmp_path / "export", "common")
    original = Path.write_text
    attempted = []

    def denied(path: Path, *args, **kwargs):
        if path.name == INDEX_NAME:
            attempted.append(path)
            raise PermissionError("synthetic write denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", denied)
    read_result = parse_elem_json(form, write_index=False)
    assert read_result.elem_index_ok
    assert attempted == []
    write_result = parse_elem_json(form)
    assert attempted == [form / INDEX_NAME]
    assert write_result.elem_index_ok
    assert write_result.elements == read_result.elements
    assert len(write_result.warnings) == len(read_result.warnings) + 1


@pytest.mark.parametrize("payload", [None, "{invalid", json.dumps({"tree": []})])
def test_unsuccessful_parse_never_writes(tmp_path: Path, payload: str | None) -> None:
    form = tmp_path / "CommonForm" / "SyntheticForm"
    form.mkdir(parents=True)
    if payload is not None:
        (form / "CommonForm.elem.json").write_text(payload, encoding="utf-8")
    before = _snapshot(tmp_path)
    for write_index in (True, False):
        assert not parse_elem_json(form, write_index=write_index).elem_index_ok
        assert _snapshot(tmp_path) == before


def test_structural_hash_does_not_depend_on_index_file(tmp_path: Path) -> None:
    root, form = _export(tmp_path / "export", "common")
    parsed = parse_elem_json(form)
    structural = [{key: value for key, value in element.items() if key in STRUCTURAL_KEYS} for element in parsed.elements]
    expected = hashlib.sha256(json.dumps(structural, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    first = scan_forms(root)
    assert first.forms[0].elem_sha256 == expected
    baseline = tmp_path / "baseline.json"
    first.save(baseline)
    _set_index(form, True)
    before = _snapshot(root)
    second = scan_forms(root)
    assert second.forms[0].elem_sha256 == expected
    assert _snapshot(root) == before
    assert baseline.is_file()


def test_runner_probe_preserves_classification(tmp_path: Path) -> None:
    root, form = _export(tmp_path / "export", "common")
    entry = scan_forms(root).forms[0]
    context = form_context.build_form_context(entry, root)
    _set_index(form, True)
    before = _snapshot(root)
    assert runner._probe_elem_index(context, root) == (True, "")
    assert _snapshot(root) == before
    (form / "CommonForm.elem.json").write_text("{invalid", encoding="utf-8")
    before = _snapshot(root)
    assert runner._probe_elem_index(context, root) == (False, runner.REASON_ELEM_INDEX_UNAVAILABLE)
    assert _snapshot(root) == before


def test_pipeline_keeps_explicit_index_write(tmp_path: Path) -> None:
    dump = tmp_path / "dump"
    ext = dump / "Catalog" / "SyntheticOwner" / "Forms" / "SyntheticForm" / "Ext"
    ext.mkdir(parents=True)
    (ext / "Form.bin").write_bytes(b"synthetic")
    written = []

    def unpacker(source, root: Path) -> FormArtifact:
        artifact = FormArtifact.for_source(root, source)
        form = artifact.paths["object_module"].parent
        form.mkdir(parents=True)
        (form / "Form.elem.json").write_text(json.dumps(PAYLOAD), encoding="utf-8")
        written.append(form / INDEX_NAME)
        return artifact

    artifacts = unpack_all_forms(dump, tmp_path / "unpacked", unpacker)
    assert len(artifacts) == 1
    assert artifacts[0].elem_index_ok
    assert all(path.is_file() for path in written)
