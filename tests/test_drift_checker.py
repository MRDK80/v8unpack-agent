# tests/test_drift_checker.py
"""Тесты для drift_checker.

Только синтетические фикстуры — никаких реальных конфигураций,
баз данных, строк подключения.
"""
import json
import os
import time
from pathlib import Path

import pytest

from v8unpack_agent.drift_checker import (
    DriftReport,
    check_drift,
    _form_key,
)


# ---------------------------------------------------------------------------
# Хелперы для построения синтетических фикстур
# ---------------------------------------------------------------------------

def _make_form(root: Path, object_type: str, object_name: str,
               container_name: str, form_name: str) -> Path:
    """Создать директорию формы с .obj.bsl и .json артефактами."""
    form_dir = root / object_type / object_name / container_name / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    bsl = form_dir / (container_name + ".obj.bsl")
    bsl.write_text("-- stub bsl", encoding="utf-8")
    (form_dir / (container_name + ".json")).write_text("{}", encoding="utf-8")
    return form_dir


def _make_common_form(root: Path, form_name: str) -> Path:
    """Создать CommonForm (3-уровневый layout)."""
    form_dir = root / "CommonForm" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "CommonForm.obj.bsl").write_text("-- stub bsl", encoding="utf-8")
    (form_dir / "CommonForm.json").write_text("{}", encoding="utf-8")
    return form_dir


def _build_index(root: Path, forms: list[tuple]) -> dict:
    """Построить минимальный forms_index.json из списка форм.

    Каждый элемент: (object_type, object_name, container_name, form_name).
    bsl_path указывает на реальный файл в root.
    """
    entries = []
    for ot, on, cn, fn in forms:
        if ot == "CommonForm" and on == "":
            bsl = root / "CommonForm" / fn / "CommonForm.obj.bsl"
        else:
            bsl = root / ot / on / cn / fn / (cn + ".obj.bsl")
        entries.append({
            "object_type": ot,
            "object_name": on,
            "container_name": cn,
            "form_name": fn,
            "bsl_path": bsl.as_posix(),
            "json_path": (bsl.parent / (cn + ".json")).as_posix(),
            "warnings": [],
        })
    return {"total": len(entries), "scanned_at": "2026-01-01T00:00:00+00:00",
            "scan_warnings": [], "forms": entries}


# ---------------------------------------------------------------------------
# Тест 1: нет дрейфа — индекс актуален
# ---------------------------------------------------------------------------

def test_no_drift(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")
    _make_form(root, "Document", "Sales", "DocumentForm", "ObjectForm")

    forms = [
        ("Catalog", "Items", "CatalogForm", "ListForm"),
        ("Document", "Sales", "DocumentForm", "ObjectForm"),
    ]
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(_build_index(root, forms)), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert report.has_drift is False
    assert report.added == []
    assert report.removed == []
    assert report.modified == []
    assert report.stale_extractions == []


# ---------------------------------------------------------------------------
# Тест 2: добавлена новая форма (есть на диске, нет в индексе)
# ---------------------------------------------------------------------------

def test_added_form(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")
    _make_form(root, "Catalog", "Items", "CatalogForm", "NewForm")  # новая

    forms = [("Catalog", "Items", "CatalogForm", "ListForm")]
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(_build_index(root, forms)), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert report.has_drift is True
    assert _form_key("Catalog", "Items", "CatalogForm", "NewForm") in report.added
    assert report.removed == []


# ---------------------------------------------------------------------------
# Тест 3: форма удалена (есть в индексе, нет на диске)
# ---------------------------------------------------------------------------

def test_removed_form(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")

    forms = [
        ("Catalog", "Items", "CatalogForm", "ListForm"),
        ("Catalog", "Items", "CatalogForm", "Ghost"),
    ]
    entries = []
    for ot, on, cn, fn in forms:
        bsl = root / ot / on / cn / fn / (cn + ".obj.bsl")
        entries.append({
            "object_type": ot, "object_name": on,
            "container_name": cn, "form_name": fn,
            "bsl_path": bsl.as_posix(),
            "json_path": (bsl.parent / (cn + ".json")).as_posix(),
            "warnings": [],
        })
    idx_data = {"total": 2, "scanned_at": "2026-01-01T00:00:00+00:00",
                "scan_warnings": [], "forms": entries}
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(idx_data), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert report.has_drift is True
    assert _form_key("Catalog", "Items", "CatalogForm", "Ghost") in report.removed


# ---------------------------------------------------------------------------
# Тест 4: stale_extractions — bsl_path из индекса не существует на диске
# ---------------------------------------------------------------------------

def test_stale_extractions(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")

    fake_bsl = (tmp_path / "nonexistent" / "CatalogForm.obj.bsl").as_posix()
    idx_data = {
        "total": 1,
        "scanned_at": "2026-01-01T00:00:00+00:00",
        "scan_warnings": [],
        "forms": [{
            "object_type": "Catalog",
            "object_name": "Items",
            "container_name": "CatalogForm",
            "form_name": "StaleForm",
            "bsl_path": fake_bsl,
            "json_path": fake_bsl.replace(".obj.bsl", ".json"),
            "warnings": [],
        }],
    }
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(idx_data), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert _form_key("Catalog", "Items", "CatalogForm", "StaleForm") in report.stale_extractions
    assert report.has_drift is True


# ---------------------------------------------------------------------------
# Тест 5: index_path не найден → added = все формы на диске
# ---------------------------------------------------------------------------

def test_missing_index(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")
    _make_form(root, "Document", "Sales", "DocumentForm", "ObjectForm")

    missing = tmp_path / "no_such_index.json"

    report = check_drift(cf_export_root=root, index_path=missing)

    assert report.has_drift is True
    assert len(report.added) == 2
    assert report.removed == []


# ---------------------------------------------------------------------------
# Тест 6: CommonForm (3-уровневый layout)
# ---------------------------------------------------------------------------

def test_common_form_no_drift(tmp_path):
    root = tmp_path / "cf_export"
    _make_common_form(root, "MainForm")

    forms = [("CommonForm", "", "CommonForm", "MainForm")]
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(_build_index(root, forms)), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert report.has_drift is False


# ---------------------------------------------------------------------------
# Тест 7: save_to сохраняет JSON и он читается обратно
# ---------------------------------------------------------------------------

def test_save_to_and_load(tmp_path):
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")

    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(_build_index(root, [
        ("Catalog", "Items", "CatalogForm", "ListForm")
    ])), encoding="utf-8")

    out = tmp_path / "drift_report.json"
    report = check_drift(cf_export_root=root, index_path=idx, save_to=out)

    assert out.exists()
    loaded = DriftReport.load_from(out)
    assert loaded.has_drift == report.has_drift
    assert loaded.added == report.added
    assert loaded.checked_at == report.checked_at


# ---------------------------------------------------------------------------
# Тест 8: modified — форма изменилась (mtime вырос)
# ---------------------------------------------------------------------------

def test_modified_form(tmp_path):
    root = tmp_path / "cf_export"
    form_dir = _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")
    bsl = form_dir / "CatalogForm.obj.bsl"

    forms = [("Catalog", "Items", "CatalogForm", "ListForm")]

    old_mtime = bsl.stat().st_mtime - 2.0
    os.utime(bsl, (old_mtime, old_mtime))

    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(_build_index(root, forms)), encoding="utf-8")

    bsl.write_text("-- modified bsl", encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    assert _form_key("Catalog", "Items", "CatalogForm", "ListForm") in report.modified
    assert report.has_drift is True
