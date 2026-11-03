"""Сквозной пример не изменяет выгрузку; API пишет только по запросу — issue #335.

Каталог выгрузки доступен на запись и содержит ``Template.bin`` с запросом:
до исправления шаг 10 создавал рядом ``skd_queries.json``. Снимок файловой
системы (путь, sha256) сравнивается до и после реального запуска примера.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_full_pipeline_walkthrough_issue175 import (  # bootstrap sys.path выше
    build_export,
    parse_steps,
)
from test_skd_status_issue287 import QUERY_SET, container  # bootstrap sys.path выше

from v8unpack_agent.skd_extractor import extract_all_skd_queries, extract_skd_queries

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "full_pipeline_walkthrough.py"
SKD_FILE = "skd_queries.json"


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def put_template(root: Path, obj: str, template: str = "Макет") -> Path:
    path = root / "Report" / obj / "Template" / template / "Template.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(container(QUERY_SET))
    return path


def test_walkthrough_leaves_writable_export_unchanged(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export")
    put_template(root, "Отчёт1")
    put_template(root, "Отчёт2", "М1")
    put_template(root, "Отчёт2", "М2")
    assert os.access(root / "Report" / "Отчёт1", os.W_OK)
    before = snapshot(root)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "--skip-common-modules"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )

    assert proc.returncode == 0, proc.stderr
    step = parse_steps(proc.stdout)[10]
    assert step["success"] == "3"
    assert int(step["queries_total"]) == 3
    assert snapshot(root) == before


def test_batch_without_write_returns_queries_and_writes_nothing(tmp_path: Path) -> None:
    put_template(tmp_path, "Отчёт")
    before = snapshot(tmp_path)
    batch = extract_all_skd_queries(tmp_path, write_json=False)
    assert batch.skd_extracted is True
    assert batch.results[0].datasets
    assert snapshot(tmp_path) == before


def test_single_without_write_returns_queries_and_writes_nothing(tmp_path: Path) -> None:
    put_template(tmp_path, "Отчёт")
    report = tmp_path / "Report" / "Отчёт"
    before = snapshot(tmp_path)
    result = extract_skd_queries(report, write_json=False)
    assert result.skd_extracted is True
    assert snapshot(tmp_path) == before


def test_default_api_still_writes_into_writable_root(tmp_path: Path) -> None:
    put_template(tmp_path, "Отчёт", "М1")
    put_template(tmp_path, "Отчёт", "М2")
    extract_all_skd_queries(tmp_path)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob(SKD_FILE))
    assert written == [f"Report/Отчёт/{SKD_FILE}"]
    single_root = tmp_path / "single"
    put_template(single_root, "Отчёт")
    extract_skd_queries(single_root / "Report" / "Отчёт")
    assert (single_root / "Report" / "Отчёт" / SKD_FILE).is_file()
