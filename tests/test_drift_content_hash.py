"""Синтетические тесты для issue #38: content hash (bsl_sha256) в drift detection.

Все тесты используют только временные директории и синтетические данные.
Нет зависимостей от реальных конфигураций, путей или внешних ресурсов.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms
from v8unpack_agent.drift_checker import check_drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_form_tree(root: Path, bsl_content: bytes = b"Procedure OnOpen()\nEndProcedure") -> Path:
    """Создать минимальную структуру Catalog/TestObj/CatalogForm/Form1/."""
    form_dir = root / "Catalog" / "TestObj" / "CatalogForm" / "Form1"
    form_dir.mkdir(parents=True, exist_ok=True)
    bsl = form_dir / "CatalogForm.obj.bsl"
    bsl.write_bytes(bsl_content)
    json_f = form_dir / "CatalogForm.json"
    json_f.write_text('{"name": "Form1"}', encoding="utf-8")
    return root


def _build_index_json(root: Path, bsl_content: bytes, bsl_path: Path, bsl_mtime: float,
                      include_hash: bool = True) -> Path:
    """Записать forms_scan_index.json в root."""
    entry: dict = {
        "object_type": "Catalog",
        "object_name": "TestObj",
        "container_name": "CatalogForm",
        "form_name": "Form1",
        "form_path": (bsl_path.parent).as_posix(),
        "bsl_path": bsl_path.as_posix(),
        "json_path": (bsl_path.parent / "CatalogForm.json").as_posix(),
        "warnings": [],
        "bsl_mtime": bsl_mtime,
        "form_elem_path": None,
    }
    if include_hash:
        entry["bsl_sha256"] = _sha256(bsl_content)
    index = {"total": 1, "scanned_at": "2026-01-01T00:00:00+00:00", "scan_warnings": [], "forms": [entry]}
    idx_path = root / "forms_scan_index.json"
    idx_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return idx_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_scan_forms_records_bsl_sha256(tmp_path: Path) -> None:
    """scan_forms() должен записывать bsl_sha256 в каждую FormEntry."""
    bsl_content = b"Procedure OnOpen()\nEndProcedure"
    _make_form_tree(tmp_path, bsl_content)

    index = scan_forms(tmp_path)

    assert len(index.forms) == 1
    entry = index.forms[0]
    assert entry.bsl_sha256 is not None, "bsl_sha256 должен быть вычислен"
    assert entry.bsl_sha256 == _sha256(bsl_content)


def test_load_bsl_sha256_round_trip(tmp_path: Path) -> None:
    """bsl_sha256 должен сохраняться в JSON и корректно загружаться через FormScanIndex.load()."""
    bsl_content = b"Procedure OnOpen()\nEndProcedure"
    _make_form_tree(tmp_path, bsl_content)

    idx_path = tmp_path / "forms_scan_index.json"
    index = scan_forms(tmp_path, save_to=idx_path)
    expected_hash = index.forms[0].bsl_sha256

    loaded = FormScanIndex.load(idx_path)

    assert len(loaded.forms) == 1
    assert loaded.forms[0].bsl_sha256 == expected_hash
    assert loaded.forms[0].bsl_sha256 == _sha256(bsl_content)


def test_modified_not_triggered_by_mtime_only_change_when_hash_available(
    tmp_path: Path,
) -> None:
    """Изменение только mtime (содержимое то же) не даёт modified при наличии bsl_sha256."""
    bsl_content = b"Procedure OnOpen()\nEndProcedure"
    export_root = tmp_path / "export"
    _make_form_tree(export_root, bsl_content)

    bsl_path = export_root / "Catalog" / "TestObj" / "CatalogForm" / "Form1" / "CatalogForm.obj.bsl"
    # Записываем baseline с bsl_mtime в далёком прошлом, но правильным hash
    idx_path = _build_index_json(
        tmp_path,
        bsl_content,
        bsl_path,
        bsl_mtime=1000000.0,  # давняя метка — mtime точно не совпадёт
        include_hash=True,
    )

    report = check_drift(export_root, idx_path)

    assert report.modified == [], f"Ожидалось modified=[], получено {report.modified}"
    assert not report.has_drift


def test_modified_triggered_by_bsl_hash_change(tmp_path: Path) -> None:
    """Изменение содержимого BSL-файла даёт ровно одну форму в modified."""
    original_content = b"Procedure OnOpen()\nEndProcedure"
    export_root = tmp_path / "export"
    _make_form_tree(export_root, original_content)

    bsl_path = export_root / "Catalog" / "TestObj" / "CatalogForm" / "Form1" / "CatalogForm.obj.bsl"
    # Baseline записан с hash оригинального содержимого
    bsl_stat = bsl_path.stat()
    idx_path = _build_index_json(
        tmp_path,
        original_content,
        bsl_path,
        bsl_mtime=bsl_stat.st_mtime,
        include_hash=True,
    )

    # Меняем содержимое BSL-файла
    bsl_path.write_bytes(b"Procedure OnOpen()\n  Alert(\"changed\");\nEndProcedure")

    report = check_drift(export_root, idx_path)

    assert len(report.modified) == 1
    assert report.has_drift


def test_old_index_without_hash_keeps_legacy_mtime_behavior(tmp_path: Path) -> None:
    """Старый индекс без bsl_sha256 ведёт себя по legacy-пути через bsl_mtime."""
    bsl_content = b"Procedure OnOpen()\nEndProcedure"
    export_root = tmp_path / "export"
    _make_form_tree(export_root, bsl_content)

    bsl_path = export_root / "Catalog" / "TestObj" / "CatalogForm" / "Form1" / "CatalogForm.obj.bsl"
    bsl_stat = bsl_path.stat()

    # Случай A: mtime совпадает → не modified
    idx_path = _build_index_json(
        tmp_path,
        bsl_content,
        bsl_path,
        bsl_mtime=bsl_stat.st_mtime,
        include_hash=False,  # старый формат без hash
    )
    report_same = check_drift(export_root, idx_path)
    assert report_same.modified == [], f"Ожидалось modified=[], получено {report_same.modified}"

    # Случай B: mtime изменился → modified
    idx_path_old = _build_index_json(
        tmp_path,
        bsl_content,
        bsl_path,
        bsl_mtime=1000000.0,  # давняя метка
        include_hash=False,
    )
    report_changed = check_drift(export_root, idx_path_old)
    assert len(report_changed.modified) == 1, (
        f"Ожидалось modified=[...], получено {report_changed.modified}"
    )
