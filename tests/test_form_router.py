"""Тесты FormRouter на синтетических фикстурах (без реальных данных)."""
import json
import time
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex
from v8unpack_agent.form_router import FormRouter, RouteResult
from v8unpack_agent.drift_checker import check_drift


def _entry(object_type: str, object_name: str, form_name: str, bsl_mtime: float = 0.0) -> FormEntry:
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=object_type + "Form",
        form_name=form_name,
        form_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}"),
        bsl_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}/{object_type}Form.obj.bsl"),
        json_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}/{object_type}Form.json"),
        bsl_mtime=bsl_mtime,
    )


FIXTURES = [
    _entry("Catalog",  "Catalog1",  "ListForm"),
    _entry("Catalog",  "Catalog1",  "ObjectForm"),
    _entry("Catalog",  "Catalog2",  "ListForm"),
    _entry("Document", "Document1", "ListForm"),
    _entry("Document", "Document1", "ObjectForm"),
]


@pytest.fixture()
def router(tmp_path: Path) -> FormRouter:
    index_path = tmp_path / "forms_scan_index.json"
    idx = FormScanIndex(forms=FIXTURES, total=len(FIXTURES), scanned_at="2026-01-01T00:00:00+00:00")
    idx.save(index_path)
    return FormRouter(index_path=index_path)


def test_exact_form_name(router: FormRouter) -> None:
    # form_name "ListForm" встречается у нескольких объектов
    result = router.route("ListForm")
    assert result.confidence == 1.0
    assert all(e.form_name == "ListForm" for e in result.matched)
    assert len(result.matched) == 3


def test_partial_object_name(router: FormRouter) -> None:
    result = router.route("Catalog1")
    assert result.confidence >= 0.5
    assert all(e.object_name == "Catalog1" for e in result.matched)
    assert len(result.matched) == 2


def test_object_type_fallback(router: FormRouter) -> None:
    result = router.route("Document")
    assert result.confidence == pytest.approx(0.4)
    assert all(e.object_type == "Document" for e in result.matched)


def test_no_match(router: FormRouter) -> None:
    result = router.route("НесуществующаяФорма")
    assert result.matched == []
    assert result.confidence == 0.0
    assert result.warnings


def test_reindex_updates_entry(router: FormRouter, tmp_path: Path) -> None:
    updated = _entry("Catalog", "Catalog1", "ListForm")
    updated.warnings = ["updated"]
    router.reindex([updated])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    result = router2.route("ListForm")
    updated_entries = [e for e in result.matched if e.object_name == "Catalog1"]
    assert updated_entries[0].warnings == ["updated"]


def test_reindex_preserves_others(router: FormRouter, tmp_path: Path) -> None:
    new_entry = _entry("Report", "Report1", "ReportForm")
    router.reindex([new_entry])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    assert len(router2._entries) == 6  # 5 исходных + 1 новая


# ---------------------------------------------------------------------------
# Regression tests — issue #22
# ---------------------------------------------------------------------------

def test_reindex_preserves_bsl_mtime(tmp_path: Path) -> None:
    """reindex() не должен удалять bsl_mtime из сохранённого JSON."""
    index_path = tmp_path / "forms_scan_index.json"

    baseline_mtime = 1_700_000_000.0
    entry = _entry("Catalog", "Товары", "ListForm", bsl_mtime=baseline_mtime)
    idx = FormScanIndex(
        forms=[entry],
        total=1,
        scanned_at="2026-01-01T00:00:00+00:00",
        scan_warnings=[],
    )
    idx.save(index_path)

    router = FormRouter(index_path=index_path)
    # reindex с той же записью (ничего не меняем содержательно)
    router.reindex([entry])

    # Перечитываем JSON напрямую
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    saved_form = raw["forms"][0]
    assert saved_form["bsl_mtime"] == baseline_mtime, (
        f"bsl_mtime was dropped: expected {baseline_mtime}, got {saved_form.get('bsl_mtime')}"
    )


def test_reindex_preserves_scanned_at(tmp_path: Path) -> None:
    """reindex() сохраняет scanned_at из исходного индекса."""
    index_path = tmp_path / "forms_scan_index.json"
    original_ts = "2026-03-15T10:00:00+00:00"

    entry = _entry("Document", "АктСписания", "ObjectForm", bsl_mtime=1_700_000_001.0)
    idx = FormScanIndex(forms=[entry], total=1, scanned_at=original_ts)
    idx.save(index_path)

    router = FormRouter(index_path=index_path)
    router.reindex([entry])

    raw = json.loads(index_path.read_text(encoding="utf-8"))
    assert raw["scanned_at"] == original_ts, (
        f"scanned_at was overwritten: expected {original_ts!r}, got {raw.get('scanned_at')!r}"
    )
    assert "scan_warnings" in raw


def test_check_drift_detects_modified_after_reindex(tmp_path: Path) -> None:
    """check_drift() видит modified после изменения .obj.bsl + router.reindex().

    Сценарий:
    1. Создать реальный .obj.bsl, записать индекс с bsl_mtime = t0.
    2. Вызвать router.reindex() — индекс должен сохранить bsl_mtime = t0.
    3. Изменить .obj.bsl (новый mtime = t1 > t0).
    4. check_drift() должен вернуть modified != [].
    """
    # --- Фиктивная структура cf_export ---
    cf_root = tmp_path / "cf_export"
    form_dir = cf_root / "Catalog" / "Товары" / "CatalogForm" / "ListForm"
    form_dir.mkdir(parents=True)
    bsl_file = form_dir / "CatalogForm.obj.bsl"
    bsl_file.write_text("// initial", encoding="utf-8")

    t0 = bsl_file.stat().st_mtime

    # --- Индекс с baseline mtime ---
    index_path = tmp_path / "forms_scan_index.json"
    entry = FormEntry(
        object_type="Catalog",
        object_name="Товары",
        container_name="CatalogForm",
        form_name="ListForm",
        form_path=form_dir,
        bsl_path=bsl_file,
        json_path=form_dir / "CatalogForm.json",
        bsl_mtime=t0,
    )
    idx = FormScanIndex(forms=[entry], total=1, scanned_at="2026-01-01T00:00:00+00:00")
    idx.save(index_path)

    # --- router.reindex() ---
    router = FormRouter(index_path=index_path)
    router.reindex([entry])

    # Убеждаемся, что bsl_mtime не потерялся после reindex
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    assert raw["forms"][0]["bsl_mtime"] == t0

    # --- Изменяем .obj.bsl (симулируем правку разработчика) ---
    time.sleep(0.01)  # гарантируем t1 > t0 даже на быстрых FS
    bsl_file.write_text("// modified", encoding="utf-8")
    # Принудительно выставляем mtime t0 + 10 сек для надёжности
    t1 = t0 + 10.0
    import os
    os.utime(bsl_file, (t1, t1))

    # --- check_drift должен увидеть modified ---
    report = check_drift(cf_root, index_path)
    assert report.modified, (
        f"Expected modified forms after bsl change, got: {report}"
    )
    assert any("Товары" in key for key in report.modified)
