"""Виды модулей, подтверждённые сверкой с Конфигуратором в #433 (issue #434).

`AccountingRegister.obj.bsl` и `Sequences.obj.bsl` — модуль набора записей,
`ChartOfAccounts.obj.bsl` и `ChartOfCalculationTypes.obj.bsl` — модуль
объекта (docs/research/configurator_check_issue433.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.metadata_modules import scan_metadata_object_modules
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.record_set_modules import scan_record_set_modules
from v8unpack_agent.runner import RunOptions, run_pipeline
from v8unpack_agent.search_api import collect_search_corpus
from v8unpack_agent.search_fragments import fragment_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]

# metadata_type -> (module_kind, имя файла)
CONFIRMED: dict[str, tuple[str, str]] = {
    "AccountingRegister": ("record_set", "AccountingRegister.obj.bsl"),
    "Sequences": ("record_set", "Sequences.obj.bsl"),
    "ChartOfAccounts": ("object", "ChartOfAccounts.obj.bsl"),
    "ChartOfCalculationTypes": ("object", "ChartOfCalculationTypes.obj.bsl"),
}

WORDS = {
    "AccountingRegister": "ПроводкиХозрасчетные",
    "Sequences": "ГраницаПоследовательности",
    "ChartOfAccounts": "СубконтоСчета",
    "ChartOfCalculationTypes": "БазовыеВидыРасчета",
}

# Демо-эмбеддер делит идентификатор на слова: запрос — те же слова через пробел.
QUERIES = {
    "AccountingRegister": "проводки хозрасчетные",
    "Sequences": "граница последовательности",
    "ChartOfAccounts": "субконто счета",
    "ChartOfCalculationTypes": "базовые виды расчета",
}


def _text(metadata_type: str) -> bytes:
    word = WORDS[metadata_type]
    return f"Процедура {word}()\n  {word} = 1;\nКонецПроцедуры\n".encode()


def _scan(root: Path) -> ModuleIndex:
    return ModuleIndex.from_entries(
        [*scan_metadata_object_modules(root), *scan_record_set_modules(root)]
    )


def _put(root: Path, metadata_type: str, owner: str, payload: bytes | None) -> None:
    directory = root / metadata_type / owner
    directory.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (directory / CONFIRMED[metadata_type][1]).write_bytes(payload)


@pytest.mark.parametrize("metadata_type", sorted(CONFIRMED))
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (None, "ok"),
        (b"", "empty"),
        (b" \r\n\t", "whitespace_only"),
        ("missing", "missing"),
        (b"\xff\xfe\xfa", "read_error"),
    ],
)
def test_statuses(
    tmp_path: Path, metadata_type: str, payload: bytes | str | None, status: str
) -> None:
    kind, file_name = CONFIRMED[metadata_type]
    if payload is None:
        _put(tmp_path, metadata_type, "Alpha", _text(metadata_type))
    elif payload == "missing":
        _put(tmp_path, metadata_type, "Alpha", None)
    else:
        assert isinstance(payload, bytes)
        _put(tmp_path, metadata_type, "Alpha", payload)
    index = _scan(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    assert entry.module_kind == kind
    assert entry.owner_kind == "metadata_object"
    assert entry.metadata_type == metadata_type
    assert entry.relative_path == f"{metadata_type}/Alpha/{file_name}"
    assert entry.read_status == status
    assert entry.module_id == f"metadata_object:{metadata_type}:Alpha:{kind}"


def test_symlink_module_is_read_error(tmp_path: Path) -> None:
    for metadata_type, (_kind, file_name) in CONFIRMED.items():
        _put(tmp_path, metadata_type, "Alpha", None)
        target = tmp_path / f"{metadata_type}.target"
        target.write_bytes(_text(metadata_type))
        link = tmp_path / metadata_type / "Alpha" / file_name
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlink недоступен")
    statuses = {entry.metadata_type: entry.read_status for entry in _scan(tmp_path)}
    assert statuses == dict.fromkeys(CONFIRMED, "read_error")


def test_managers_of_confirmed_types_are_not_classified(tmp_path: Path) -> None:
    for metadata_type in CONFIRMED:
        _put(tmp_path, metadata_type, "Alpha", None)
        (tmp_path / metadata_type / "Alpha" / f"{metadata_type}.mgr.bsl").write_bytes(
            _text(metadata_type)
        )
    index = _scan(tmp_path)
    assert {entry.read_status for entry in index} == {"missing"}
    assert all(not entry.relative_path.endswith(".mgr.bsl") for entry in index)


def _build(root: Path) -> None:
    for metadata_type in CONFIRMED:
        _put(root, metadata_type, "Alpha", _text(metadata_type))


def test_runner_reports_confirmed_modules(tmp_path: Path) -> None:
    _build(tmp_path)
    outcome = run_pipeline(
        RunOptions(export_root=tmp_path, include_skd=False, include_module_index=True)
    )
    objects = {
        item.object: (item.object_kind.value, item.status.value)
        for item in outcome.report.objects
        if item.object_kind.value.startswith("module_")
    }
    assert objects == {
        f"{metadata_type}/Alpha/{file_name}": (f"module_{kind}", "complete")
        for metadata_type, (kind, file_name) in CONFIRMED.items()
    }
    table: Any = outcome.report.to_dict()["summary"]["modules"]
    assert {"AccountingRegister", "Sequences"} <= set(table["record_set_by_metadata_type"])


def _artifact_ids(root: Path) -> dict[str, str]:
    fragmentation = fragment_corpus(collect_search_corpus(root))
    result: dict[str, str] = {}
    for fragment in fragmentation.fragments:
        artifact = fragment.document.artifact
        source = artifact.source_path
        if source is None:
            continue
        metadata_type = source.split("/", 1)[0]
        if metadata_type in CONFIRMED:
            result[metadata_type] = artifact.artifact_id
    return result


def _cli(*args: str, cwd: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "v8unpack_agent.search_cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    data: dict[str, Any] = json.loads(proc.stdout.decode("utf-8"))
    return data


def _hit_paths(data: dict[str, Any]) -> list[str]:
    hits = data["context"]["dispatch"]["result"]["hits"]
    return [hit["artifact"]["source_path"] for hit in hits]


def test_search_cli_exact_and_semantic(tmp_path: Path) -> None:
    export = tmp_path / "export"
    _build(export)
    ids = _artifact_ids(export)
    assert set(ids) == set(CONFIRMED)
    for metadata_type, artifact_id in ids.items():
        data = _cli(
            "query", str(export), "--query", artifact_id, "--search-mode", "exact", cwd=tmp_path
        )
        _kind, file_name = CONFIRMED[metadata_type]
        assert data["context"]["dispatch"]["result"]["status"] == "exact"
        assert _hit_paths(data) == [f"{metadata_type}/Alpha/{file_name}"]
    snapshot = tmp_path / "snapshot.json"
    _cli("build", str(export), "--snapshot", str(snapshot), "--embedder", "hashed-bow-demo", cwd=tmp_path)
    for metadata_type, query in QUERIES.items():
        _kind, file_name = CONFIRMED[metadata_type]
        data = _cli(
            "query",
            str(export),
            "--query",
            query,
            "--search-mode",
            "semantic",
            "--snapshot",
            str(snapshot),
            "--embedder",
            "hashed-bow-demo",
            "--top-k",
            "1",
            cwd=tmp_path,
        )
        assert data["context"]["dispatch"]["result"]["status"] == "semantic"
        assert _hit_paths(data) == [f"{metadata_type}/Alpha/{file_name}"]
