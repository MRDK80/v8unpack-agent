"""Тесты scan_forms на синтетических фикстурах.

Только синтетические директории — никаких реальных .cf, .epf, .erf.
Покрывает:
- Form (DataProcessor / ExternalDataProcessor — различаем по object_type)
- CatalogForm
- DocumentForm
- CommonForm
- ReportForm (Report / ExternalReport — различаем по object_type)
- неполная форма (нет .obj.bsl) не попадает в индекс
- ключи (object_type, object_name, container_name, form_name) не коллидируют
- JSON-сериализация (save / round-trip)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import v8unpack_agent.scan_forms as sf_module
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_form(
    root: Path,
    object_type: str,
    object_name: str,
    container_name: str,
    form_name: str,
    *,
    with_bsl: bool = True,
    with_json: bool = True,
) -> Path:
    """Создать синтетическую директорию формы в tmp-дереве."""
    form_dir = root / object_type / object_name / container_name / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_bsl:
        (form_dir / f"{container_name}.obj.bsl").write_text(
            "// synthetic", encoding="utf-8"
        )
    if with_json:
        (form_dir / f"{container_name}.json").write_text(
            '{"synthetic": true}', encoding="utf-8"
        )
    return form_dir


# ---------------------------------------------------------------------------
# базовые тесты
# ---------------------------------------------------------------------------

def test_scan_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    root.mkdir()
    index = scan_forms(root)
    assert index.total == 0
    assert index.forms == []


def test_scan_nonexistent_root(tmp_path: Path) -> None:
    index = scan_forms(tmp_path / "no_such_dir")
    assert index.total == 0
    assert len(index.scan_warnings) >= 1


# ---------------------------------------------------------------------------
# Form (DataProcessor)
# ---------------------------------------------------------------------------

def test_scan_form_container(tmp_path: Path) -> None:
    """Контейнер Form → object_type=DataProcessor, container_name=Form."""
    root = tmp_path / "cf_export"
    _make_form(root, "DataProcessor", "ЗагрузкаДанных", "Form", "Форма")
    index = scan_forms(root)
    assert index.total == 1
    e = index.forms[0]
    assert e.object_type == "DataProcessor"
    assert e.container_name == "Form"
    assert e.form_name == "Форма"
    assert e.bsl_path.name == "Form.obj.bsl"
    assert e.json_path.name == "Form.json"


# ---------------------------------------------------------------------------
# CatalogForm
# ---------------------------------------------------------------------------

def test_scan_catalog_form(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаЭлемента")
    index = scan_forms(root)
    assert index.total == 1
    e = index.forms[0]
    assert e.container_name == "CatalogForm"
    assert e.bsl_path.name == "CatalogForm.obj.bsl"


# ---------------------------------------------------------------------------
# DocumentForm
# ---------------------------------------------------------------------------

def test_scan_document_form(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_form(root, "Document", "АктСписания", "DocumentForm", "ФормаВыбора")
    index = scan_forms(root)
    assert index.total == 1
    e = index.forms[0]
    assert e.container_name == "DocumentForm"
    assert e.object_name == "АктСписания"


# ---------------------------------------------------------------------------
# CommonForm
# ---------------------------------------------------------------------------

def test_scan_common_form(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_form(root, "CommonForm", "ОбщаяФорма", "CommonForm", "Основная")
    index = scan_forms(root)
    assert index.total == 1
    assert index.forms[0].container_name == "CommonForm"


# ---------------------------------------------------------------------------
# ReportForm (Report + ExternalReport различаем по object_type)
# ---------------------------------------------------------------------------

def test_scan_report_form_internal(tmp_path: Path) -> None:
    """object_type=Report → контейнер ReportForm."""
    root = tmp_path / "cf_export"
    _make_form(root, "Report", "Продажи", "ReportForm", "ФормаОтчёта")
    index = scan_forms(root)
    assert index.total == 1
    e = index.forms[0]
    assert e.object_type == "Report"
    assert e.container_name == "ReportForm"


def test_scan_report_form_external(tmp_path: Path) -> None:
    """object_type=ExternalReport → контейнер ReportForm (тот же контейнер)."""
    root = tmp_path / "cf_export"
    _make_form(root, "ExternalReport", "МойОтчёт", "ReportForm", "Форма")
    index = scan_forms(root)
    assert index.total == 1
    assert index.forms[0].object_type == "ExternalReport"
    assert index.forms[0].container_name == "ReportForm"


# ---------------------------------------------------------------------------
# неполная форма (нет .obj.bsl)
# ---------------------------------------------------------------------------

def test_incomplete_form_excluded(tmp_path: Path) -> None:
    """Форма без .obj.bsl не попадает в индекс; предупреждение записывается."""
    root = tmp_path / "cf_export"
    _make_form(
        root, "Catalog", "Контрагенты", "CatalogForm", "НеполнаяФорма",
        with_bsl=False, with_json=True,
    )
    index = scan_forms(root)
    assert index.total == 0
    assert any("skipped" in w for w in index.scan_warnings)


# ---------------------------------------------------------------------------
# нет коллизий ключей при нескольких контейнерах
# ---------------------------------------------------------------------------

def test_no_key_collision(tmp_path: Path) -> None:
    """Формы из разных контейнеров не коллидируют."""
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Номенклатура", "CatalogForm", "ФормаЭлемента")
    _make_form(root, "Catalog", "Номенклатура", "CatalogForm", "ФормаСписка")
    _make_form(root, "Document", "Реализация", "DocumentForm", "ФормаДокумента")
    _make_form(root, "DataProcessor", "Обработка", "Form", "Форма")
    _make_form(root, "Report", "Отчёт", "ReportForm", "ФормаОтчёта")
    index = scan_forms(root)
    assert index.total == 5
    keys = [
        (e.object_type, e.object_name, e.container_name, e.form_name)
        for e in index.forms
    ]
    assert len(keys) == len(set(keys)), "коллизия ключей в индексе"


# ---------------------------------------------------------------------------
# Form обратная совместимость (результат не изменился по сравнению с #9)
# ---------------------------------------------------------------------------

def test_form_container_backward_compat(tmp_path: Path) -> None:
    """Контейнер Form работает так же, как в исходной реализации #9."""
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Counterparties", "Form", "ListForm")
    _make_form(root, "Document", "SalesOrder", "Form", "ObjectForm")
    index = scan_forms(root)
    assert index.total == 2
    names = {e.form_name for e in index.forms}
    assert names == {"ListForm", "ObjectForm"}


# ---------------------------------------------------------------------------
# JSON сериализация
# ---------------------------------------------------------------------------

def test_json_serialization(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаЭлемента")
    out = tmp_path / "forms_scan_index.json"
    index = scan_forms(root, save_to=out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert len(data["forms"]) == 1
    assert data["forms"][0]["container_name"] == "CatalogForm"
    assert data["forms"][0]["form_name"] == "ФормаЭлемента"


# ---------------------------------------------------------------------------
# best-effort: ошибка одной формы не останавливает обход
# ---------------------------------------------------------------------------

def test_best_effort_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в _scan_form_dir не останавливает сканирование."""
    root = tmp_path / "cf_export"
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаЭлемента")
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаСписка")

    call_count = 0
    original = sf_module._scan_form_dir

    def patched(form_dir, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("synthetic error")
        return original(form_dir, *args, **kwargs)

    monkeypatch.setattr(sf_module, "_scan_form_dir", patched)
    index = scan_forms(root)
    # одна упала, одна собралась
    assert index.total == 1
    assert any("error" in w for w in index.scan_warnings)
