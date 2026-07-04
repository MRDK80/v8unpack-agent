"""Тесты scan_forms(mode="external") на синтетических фикстурах (issue #25).

Только синтетические директории — никаких реальных .epf/.erf.
Реальный паттерн внешней обработки (подтверждён 02.07.2026):

    External/<имя обработки>/
    ├── ExternalDataProcessor.json
    ├── ExternalDataProcessor.obj      # модуль объекта (не форма)
    └── Form/
        └── <ИмяФормы>/
            ├── Form.obj               # bsl формы, БЕЗ .bsl-суффикса
            ├── Form.json
            ├── Form.elem
            └── Form.id

Отличия от конфигурации:
- bsl-файл называется Form.obj, а не Form.obj.bsl;
- верхний уровень — имя обработки, а не object_type;
- формы вложены через фиксированный контейнер Form/<ИмяФормы>/.
"""
from __future__ import annotations

import json
from pathlib import Path

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_external_processor(
    root: Path,
    processing_name: str,
    *,
    with_object_module: bool = True,
) -> Path:
    """Создать каталог внешней обработки External/<имя>/ (без форм)."""
    proc_dir = root / "External" / processing_name
    proc_dir.mkdir(parents=True, exist_ok=True)
    (proc_dir / "ExternalDataProcessor.json").write_text(
        '{"synthetic": true}', encoding="utf-8"
    )
    if with_object_module:
        (proc_dir / "ExternalDataProcessor.obj").write_text(
            "// synthetic object module", encoding="utf-8"
        )
    return proc_dir


def _make_external_form(
    root: Path,
    processing_name: str,
    form_name: str,
    *,
    with_bsl: bool = True,
    with_json: bool = True,
    with_elem: bool = True,
    with_id: bool = True,
) -> Path:
    """Создать форму External/<имя>/Form/<ИмяФормы>/ по реальному паттерну."""
    form_dir = root / "External" / processing_name / "Form" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_bsl:
        (form_dir / "Form.obj").write_text("// synthetic", encoding="utf-8")
    if with_json:
        (form_dir / "Form.json").write_text('{"synthetic": true}', encoding="utf-8")
    if with_elem:
        (form_dir / "Form.elem").write_text('{"items": []}', encoding="utf-8")
    if with_id:
        (form_dir / "Form.id").write_text("synthetic-id", encoding="utf-8")
    return form_dir


# ---------------------------------------------------------------------------
# базовые случаи
# ---------------------------------------------------------------------------

def test_external_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    root.mkdir()
    index = scan_forms(root, mode="external")
    assert index.total == 0
    assert index.forms == []


def test_external_nonexistent_root(tmp_path: Path) -> None:
    index = scan_forms(tmp_path / "no_such_dir", mode="external")
    assert index.total == 0
    assert len(index.scan_warnings) >= 1


# ---------------------------------------------------------------------------
# happy path: одна форма одной обработки
# ---------------------------------------------------------------------------

def test_external_single_form(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "ЗагрузкаЦен")
    _make_external_form(root, "ЗагрузкаЦен", "Форма")
    index = scan_forms(root, mode="external")
    assert index.total == 1
    e = index.forms[0]
    assert e.object_type == "ExternalDataProcessor"
    assert e.object_name == "ЗагрузкаЦен"
    assert e.container_name == "Form"
    assert e.form_name == "Форма"
    assert e.bsl_path.name == "Form.obj"      # БЕЗ .bsl
    assert e.json_path.name == "Form.json"
    assert e.form_elem_path is not None
    assert e.form_elem_path.name == "Form.elem"


# ---------------------------------------------------------------------------
# несколько форм / несколько обработок
# ---------------------------------------------------------------------------

def test_external_multiple_forms(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка1")
    _make_external_form(root, "Обработка1", "ОсновнаяФорма")
    _make_external_form(root, "Обработка1", "ФормаНастроек")
    index = scan_forms(root, mode="external")
    assert index.total == 2
    assert {e.form_name for e in index.forms} == {"ОсновнаяФорма", "ФормаНастроек"}
    assert all(e.object_name == "Обработка1" for e in index.forms)


def test_external_multiple_processors(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка1")
    _make_external_form(root, "Обработка1", "Форма")
    _make_external_processor(root, "Обработка2")
    _make_external_form(root, "Обработка2", "Форма")
    index = scan_forms(root, mode="external")
    assert index.total == 2
    keys = [(e.object_type, e.object_name, e.container_name, e.form_name)
            for e in index.forms]
    assert len(keys) == len(set(keys)), "коллизия ключей в индексе External"


# ---------------------------------------------------------------------------
# обработка без форм
# ---------------------------------------------------------------------------

def test_external_processor_without_forms(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "ПустаяОбработка")
    index = scan_forms(root, mode="external")
    assert index.total == 0
    assert index.forms == []


# ---------------------------------------------------------------------------
# graceful fallback: битая/неполная форма (нет Form.obj)
# ---------------------------------------------------------------------------

def test_external_incomplete_form_excluded(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка")
    _make_external_form(root, "Обработка", "БитаяФорма", with_bsl=False)
    index = scan_forms(root, mode="external")
    assert index.total == 0
    assert any("skipped" in w for w in index.scan_warnings)


def test_external_missing_elem_is_tolerated(tmp_path: Path) -> None:
    """Нет Form.elem — форма всё равно индексируется, form_elem_path=None."""
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка")
    _make_external_form(root, "Обработка", "Форма", with_elem=False)
    index = scan_forms(root, mode="external")
    assert index.total == 1
    assert index.forms[0].form_elem_path is None


# ---------------------------------------------------------------------------
# object_type не пересекается с типами конфигурации
# ---------------------------------------------------------------------------

def test_external_object_type_not_config(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка")
    _make_external_form(root, "Обработка", "Форма")
    index = scan_forms(root, mode="external")
    config_types = {"Catalog", "Document", "DataProcessor", "Report",
                    "CommonForm", "AccumulationRegister", "BusinessProcess"}
    assert index.forms[0].object_type not in config_types


# ---------------------------------------------------------------------------
# JSON round-trip с новым полем form_elem_path
# ---------------------------------------------------------------------------

def test_external_json_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка")
    _make_external_form(root, "Обработка", "Форма")
    out = tmp_path / "forms_scan_index.json"
    index = scan_forms(root, mode="external", save_to=out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert data["forms"][0]["form_elem_path"].endswith("Form.elem")


# ---------------------------------------------------------------------------
# регрессия: config-режим не затронут (дефолт mode="config")
# ---------------------------------------------------------------------------

def test_config_mode_default_unaffected(tmp_path: Path) -> None:
    """mode по умолчанию = config; External-структура НЕ сканируется в config."""
    root = tmp_path / "cf_export"
    _make_external_processor(root, "Обработка")
    _make_external_form(root, "Обработка", "Форма")
    index = scan_forms(root)  # без mode -> config
    assert index.total == 0


def test_config_still_finds_catalog_form(tmp_path: Path) -> None:
    """В config-режиме обычная CatalogForm по-прежнему находится."""
    root = tmp_path / "cf_export"
    fd = root / "Catalog" / "Склады" / "CatalogForm" / "ФормаЭлемента"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / "CatalogForm.obj.bsl").write_text("// synthetic", encoding="utf-8")
    (fd / "CatalogForm.json").write_text('{"synthetic": true}', encoding="utf-8")
    index = scan_forms(root, mode="config")
    assert index.total == 1
    assert index.forms[0].container_name == "CatalogForm"


# ---------------------------------------------------------------------------
# route() по индексу External
# ---------------------------------------------------------------------------

def test_external_router_matches(tmp_path: Path) -> None:
    from v8unpack_agent.form_router import FormRouter

    root = tmp_path / "cf_export"
    _make_external_processor(root, "ЗагрузкаЦен")
    _make_external_form(root, "ЗагрузкаЦен", "Форма")
    out = tmp_path / "forms_scan_index.json"
    scan_forms(root, mode="external", save_to=out)

    router = FormRouter(index_path=out)
    result = router.route("ЗагрузкаЦен")
    assert result.matched, "route() не нашёл форму внешней обработки"
    assert result.matched[0].object_name == "ЗагрузкаЦен"
