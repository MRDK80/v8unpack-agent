"""Тесты для drift_checker с mode=external (issue #73).

Покрывают все Acceptance Criteria:
  AC1: check_drift(external_root, baseline, mode="external") не даёт removed сразу после создания baseline
  AC2: check_drift(external_root, baseline, mode="external") корректно детектирует реальное изменение BSL-файла
  AC3: check_drift(config_root, baseline) (mode=config, default) — регрессий нет
  AC4: elem-only external-форма (#58) не даёт ложный stale при mode="external"
  AC5: новые тесты покрывают external-layout в check_drift

Только синтетические фикстуры — никаких реальных путей, хостов,
строк подключения. Пути строятся через pathlib.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from v8unpack_agent.drift_checker import (
    DriftReport,
    check_drift,
    _form_key,
)
from v8unpack_agent.scan_forms import scan_forms


# ---------------------------------------------------------------------------
# Хелперы для построения синтетических external-фикстур
# ---------------------------------------------------------------------------

def _make_external_form(
    root: Path,
    object_name: str,
    container_name: str,
    form_name: str,
    bsl_content: str = "-- stub bsl",
) -> Path:
    """Создать external-форму: root/<object_name>/<container_name>/<form_name>/.

    Структура соответствует external-layout после _scan_external в scan_forms:
    root может быть уровнем объектов (без дополнительного External/).
    Создаёт <container_name>.obj.bsl и <container_name>.json.
    """
    form_dir = root / object_name / container_name / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    bsl = form_dir / (container_name + ".obj.bsl")
    bsl.write_text(bsl_content, encoding="utf-8")
    (form_dir / (container_name + ".json")).write_text("{}", encoding="utf-8")
    return form_dir


def _make_external_processor_root(root: Path, object_name: str) -> None:
    """Добавить маркер ExternalDataProcessor.obj.bsl для определения типа объекта."""
    (root / object_name / "ExternalDataProcessor.obj.bsl").write_text(
        "-- obj stub", encoding="utf-8"
    )


def _baseline_from_scan(
    root: Path,
    index_path: Path,
    mode: str = "external",
) -> None:
    """Создать baseline-индекс через scan_forms и сохранить в index_path."""
    idx = scan_forms(root, mode=mode, include_elem_only=False)
    idx.save(index_path)


def _build_external_index_raw(
    root: Path,
    entries: list[dict],
    index_path: Path,
) -> None:
    """Записать forms_index.json вручную (для тестов без scan_forms)."""
    data = {
        "total": len(entries),
        "scanned_at": "2026-01-01T00:00:00+00:00",
        "scan_warnings": [],
        "forms": entries,
    }
    index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1 — нет ложного removed сразу после создания baseline
# ---------------------------------------------------------------------------

def test_no_false_removed_after_baseline(tmp_path):
    """AC1: check_drift(external_root, baseline, mode='external') → has_drift=False
    сразу после создания baseline через scan_forms.

    Это был основной баг #73: _disk_snapshot без mode находил 0 форм,
    поэтому removed = все формы из индекса.
    """
    root = tmp_path / "external_unpacked"
    _make_external_form(root, "ext__Акт.epf", "Form", "Форма")
    _make_external_processor_root(root, "ext__Акт.epf")

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    report = check_drift(root, baseline, mode="external")

    assert report.has_drift is False, (
        f"Ожидали has_drift=False, но removed={report.removed}, "
        f"added={report.added}, modified={report.modified}"
    )
    assert report.removed == [], f"Ложный removed: {report.removed}"
    assert report.added == []
    assert report.modified == []


# ---------------------------------------------------------------------------
# AC1b — два объекта, несколько форм
# ---------------------------------------------------------------------------

def test_no_false_removed_multiple_forms(tmp_path):
    """AC1b: несколько external-объектов и форм — нет дрейфа сразу после baseline."""
    root = tmp_path / "external_unpacked"
    _make_external_form(root, "ext__Обработка1.epf", "Form", "Форма")
    _make_external_form(root, "ext__Обработка1.epf", "Form", "ФормаДоп")
    _make_external_processor_root(root, "ext__Обработка1.epf")
    _make_external_form(root, "ext__Отчёт1.erf", "ReportForm", "ФормаОтчёта")

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    report = check_drift(root, baseline, mode="external")

    assert report.has_drift is False
    assert report.removed == []
    assert report.added == []
    assert report.modified == []


# ---------------------------------------------------------------------------
# AC2 — корректная детекция реального изменения BSL
# ---------------------------------------------------------------------------

def test_detects_real_bsl_change(tmp_path):
    """AC2: check_drift корректно детектирует modified при изменении BSL-файла."""
    root = tmp_path / "external_unpacked"
    form_dir = _make_external_form(
        root, "ext__Обработка.epf", "Form", "Форма", bsl_content="-- original"
    )
    _make_external_processor_root(root, "ext__Обработка.epf")
    bsl = form_dir / "Form.obj.bsl"

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    # Изменяем BSL после создания baseline
    time.sleep(0.05)  # гарантируем другой mtime (mtime-resolution 1ms на ext4)
    bsl.write_text("-- CHANGED content", encoding="utf-8")

    report = check_drift(root, baseline, mode="external")

    # baseline содержит bsl_sha256 → hash-based detection
    key = _form_key("ExternalDataProcessor", "ext__Обработка.epf", "Form", "Форма")
    assert report.has_drift is True
    assert key in report.modified, (
        f"Ожидали {key!r} в modified, получили modified={report.modified}"
    )
    assert report.removed == []
    assert report.added == []


# ---------------------------------------------------------------------------
# AC2b — добавление новой external-формы (added)
# ---------------------------------------------------------------------------

def test_detects_added_external_form(tmp_path):
    """AC2b: новая форма после baseline попадает в added."""
    root = tmp_path / "external_unpacked"
    _make_external_form(root, "ext__Обработка.epf", "Form", "Форма")
    _make_external_processor_root(root, "ext__Обработка.epf")

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    # Добавляем новую форму после baseline
    _make_external_form(root, "ext__Обработка.epf", "Form", "НоваяФорма")

    report = check_drift(root, baseline, mode="external")

    key_new = _form_key("ExternalDataProcessor", "ext__Обработка.epf", "Form", "НоваяФорма")
    assert report.has_drift is True
    assert key_new in report.added, f"Ожидали {key_new!r} в added, got {report.added}"
    assert report.removed == []


# ---------------------------------------------------------------------------
# AC2c — удаление external-формы после baseline (removed)
# ---------------------------------------------------------------------------

def test_detects_removed_external_form(tmp_path):
    """AC2c: форма, удалённая после baseline, попадает в removed."""
    root = tmp_path / "external_unpacked"
    form_dir = _make_external_form(root, "ext__Обработка.epf", "Form", "Форма")
    _make_external_form(root, "ext__Обработка.epf", "Form", "ВременнаяФорма")
    _make_external_processor_root(root, "ext__Обработка.epf")

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    # Удаляем файл BSL — форма «исчезает» с диска
    bsl_to_remove = form_dir.parent / "ВременнаяФорма" / "Form.obj.bsl"
    bsl_to_remove.unlink()

    report = check_drift(root, baseline, mode="external")

    key_removed = _form_key("ExternalDataProcessor", "ext__Обработка.epf", "Form", "ВременнаяФорма")
    assert report.has_drift is True
    assert key_removed in report.removed, (
        f"Ожидали {key_removed!r} в removed, got {report.removed}"
    )


# ---------------------------------------------------------------------------
# AC3 — регрессия: mode=config (default) не сломан
# ---------------------------------------------------------------------------

def test_config_mode_regression_no_drift(tmp_path):
    """AC3: mode=config (default) — нет дрейфа после создания baseline.

    Проверяем, что добавление mode-параметра не сломало config-layout.
    """
    root = tmp_path / "cf_export"
    # 4-уровневый layout
    form_dir = root / "Catalog" / "Items" / "CatalogForm" / "ListForm"
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "CatalogForm.obj.bsl").write_text("-- stub", encoding="utf-8")
    (form_dir / "CatalogForm.json").write_text("{}", encoding="utf-8")

    # 3-уровневый layout (CommonForm)
    cf_dir = root / "CommonForm" / "MainForm"
    cf_dir.mkdir(parents=True, exist_ok=True)
    (cf_dir / "CommonForm.obj.bsl").write_text("-- stub", encoding="utf-8")
    (cf_dir / "CommonForm.json").write_text("{}", encoding="utf-8")

    baseline = tmp_path / "baseline.json"
    idx = scan_forms(root, mode="config", include_elem_only=False)
    idx.save(baseline)

    # mode не передаём — должен работать default="config"
    report = check_drift(root, baseline)

    assert report.has_drift is False, (
        f"Регрессия config: removed={report.removed}, added={report.added}"
    )
    assert report.removed == []
    assert report.added == []
    assert report.modified == []


def test_config_mode_regression_detects_change(tmp_path):
    """AC3b: mode=config корректно детектирует изменение после добавления mode-параметра."""
    root = tmp_path / "cf_export"
    form_dir = root / "Catalog" / "Items" / "CatalogForm" / "ListForm"
    form_dir.mkdir(parents=True, exist_ok=True)
    bsl = form_dir / "CatalogForm.obj.bsl"
    bsl.write_text("-- original", encoding="utf-8")
    (form_dir / "CatalogForm.json").write_text("{}", encoding="utf-8")

    baseline = tmp_path / "baseline.json"
    idx = scan_forms(root, mode="config", include_elem_only=False)
    idx.save(baseline)

    time.sleep(0.05)
    bsl.write_text("-- CHANGED", encoding="utf-8")

    report = check_drift(root, baseline)

    key = _form_key("Catalog", "Items", "CatalogForm", "ListForm")
    assert report.has_drift is True
    assert key in report.modified


# ---------------------------------------------------------------------------
# AC4 — elem-only external-форма (#58) не даёт ложный stale
# ---------------------------------------------------------------------------

def test_elem_only_external_no_false_stale(tmp_path):
    """AC4: elem-only форма в external-layout не попадает в stale_extractions.

    elem-only форма: есть *.elem.json, нет .obj.bsl — управляемая форма
    без кода (issue #58). При mode='external' она не должна давать stale.
    """
    root = tmp_path / "external_unpacked"
    object_name = "ext__Обработка.epf"
    container_name = "Form"
    form_name = "УправляемаяФорма"

    form_dir = root / object_name / container_name / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    # Только elem.json, без .obj.bsl — это elem-only форма
    (form_dir / "Form.elem.json").write_text(
        json.dumps({"elements": []}), encoding="utf-8"
    )
    _make_external_processor_root(root, object_name)

    # Baseline через scan_forms с include_elem_only=True (включаем elem-only)
    baseline = tmp_path / "baseline.json"
    idx = scan_forms(root, mode="external", include_elem_only=True)
    idx.save(baseline)

    report = check_drift(root, baseline, mode="external")

    # elem-only не должна попасть в stale_extractions
    assert report.stale_extractions == [], (
        f"Ложный stale для elem-only: {report.stale_extractions}"
    )


# ---------------------------------------------------------------------------
# AC5 — ExternalReport (ReportForm container) — нет дрейфа после baseline
# ---------------------------------------------------------------------------

def test_external_report_no_false_removed(tmp_path):
    """AC5: ExternalReport с контейнером ReportForm — нет ложного removed."""
    root = tmp_path / "external_unpacked"
    _make_external_form(root, "ext__Отчёт.erf", "ReportForm", "ФормаОтчёта")

    baseline = tmp_path / "baseline.json"
    _baseline_from_scan(root, baseline, mode="external")

    report = check_drift(root, baseline, mode="external")

    assert report.has_drift is False, (
        f"Ложный дрейф для ExternalReport: removed={report.removed}"
    )
    assert report.removed == []


# ---------------------------------------------------------------------------
# AC5b — missing index при mode=external: added = все формы на диске
# ---------------------------------------------------------------------------

def test_external_missing_index(tmp_path):
    """AC5b: index_path не найден при mode=external → added = все внешние формы."""
    root = tmp_path / "external_unpacked"
    _make_external_form(root, "ext__Обработка.epf", "Form", "Форма")
    _make_external_processor_root(root, "ext__Обработка.epf")
    _make_external_form(root, "ext__Отчёт.erf", "ReportForm", "ФормаОтчёта")

    missing = tmp_path / "no_such_index.json"

    report = check_drift(root, missing, mode="external")

    assert report.has_drift is True
    assert len(report.added) == 2
    assert report.removed == []
