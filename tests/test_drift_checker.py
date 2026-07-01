# tests/test_drift_checker.py
"""Тесты для drift_checker.

Только синтетические фикстуры — никаких реальных конфигураций,
баз данных, строк подключения.
"""
import json
import time
from pathlib import Path

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
    bsl_mtime заполняется из stat() файла (если он существует).
    """
    entries = []
    for ot, on, cn, fn in forms:
        if ot == "CommonForm" and on == "":
            bsl = root / "CommonForm" / fn / "CommonForm.obj.bsl"
        else:
            bsl = root / ot / on / cn / fn / (cn + ".obj.bsl")
        mtime = bsl.stat().st_mtime if bsl.exists() else 0.0
        entries.append({
            "object_type": ot,
            "object_name": on,
            "container_name": cn,
            "form_name": fn,
            "bsl_path": bsl.as_posix(),
            "json_path": (bsl.parent / (cn + ".json")).as_posix(),
            "bsl_mtime": mtime,
            "warnings": [],
        })
    return {"total": len(entries), "scanned_at": "2026-01-01T00:00:00+00:00",
            "scan_warnings": [], "forms": entries}


def _build_index_no_mtime(root: Path, forms: list[tuple]) -> dict:
    """Построить индекс без поля bsl_mtime (старый формат)."""
    entries = []
    for ot, on, cn, fn in forms:
        bsl = root / ot / on / cn / fn / (cn + ".obj.bsl")
        entries.append({
            "object_type": ot,
            "object_name": on,
            "container_name": cn,
            "form_name": fn,
            "bsl_path": bsl.as_posix(),
            "json_path": (bsl.parent / (cn + ".json")).as_posix(),
            "warnings": [],
            # bsl_mtime отсутствует намеренно
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
    _make_form(root, "Catalog", "Items", "CatalogForm", "NewForm")

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
            "bsl_mtime": 0.0,
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
            "bsl_mtime": 0.0,
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
# Тест 8: modified — форма изменена после записи baseline (#18)
# ---------------------------------------------------------------------------

def test_modified_form(tmp_path):
    """Форма попадает в modified, если .obj.bsl изменён после записи bsl_mtime."""
    root = tmp_path / "cf_export"
    form_dir = _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")
    bsl = form_dir / "CatalogForm.obj.bsl"

    # Записываем baseline mtime в индекс сразу после создания файла
    baseline_mtime = bsl.stat().st_mtime
    idx_data = _build_index(root, [("Catalog", "Items", "CatalogForm", "ListForm")])
    # Перезаписываем bsl_mtime вручную, чтобы быть точно уверенным в baseline
    idx_data["forms"][0]["bsl_mtime"] = baseline_mtime
    idx = tmp_path / "forms_index.json"
    idx.write_text(json.dumps(idx_data), encoding="utf-8")

    # Изменяем файл: ждём > 1 секунды (допуск FAT/NTFS) и обновляем mtime
    time.sleep(1.1)
    bsl.write_text("-- modified bsl content", encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    key = _form_key("Catalog", "Items", "CatalogForm", "ListForm")
    assert key in report.modified, f"expected {key!r} in modified, got {report.modified}"
    assert report.has_drift is True
    assert report.added == []
    assert report.removed == []


# ---------------------------------------------------------------------------
# Тест 9: обратная совместимость — старый индекс без bsl_mtime → modified=[]
# ---------------------------------------------------------------------------

def test_modified_old_index_backward_compat(tmp_path):
    """Старый индекс без bsl_mtime: modified остаётся [], has_drift=False."""
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Items", "CatalogForm", "ListForm")

    forms = [("Catalog", "Items", "CatalogForm", "ListForm")]
    idx = tmp_path / "forms_index.json"
    # Индекс без поля bsl_mtime (старый формат)
    idx.write_text(json.dumps(_build_index_no_mtime(root, forms)), encoding="utf-8")

    report = check_drift(cf_export_root=root, index_path=idx)

    # bsl_mtime=0.0 (fallback) vs disk mtime (реальный) — разница > 1 сек.
    # Но это ожидаемое поведение: без baseline невозможно
    # отличить «изменено» от «неизвестно».
    # modified пуст — документированное поведение при старом индексе.
    assert report.modified == []
    assert report.added == []
    assert report.removed == []
    assert report.has_drift is False
