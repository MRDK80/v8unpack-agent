"""Тесты scan_forms на синтетических фикстурах.

Только синтетические директории — никаких реальных .cf, .epf, .erf.
Покрывает:
- Form (DataProcessor / ExternalDataProcessor — различаем по object_type)
- CatalogForm
- DocumentForm
- CommonForm (3-уровневый layout, без object_name)
- ReportForm (Report / ExternalReport — различаем по object_type)
- неполная форма (нет .obj.bsl) не попадает в индекс
- ключи (object_type, object_name, container_name, form_name) не коллидируют
- JSON-сериализация (save / round-trip)
- формы без .obj.bsl учтены в scan_warnings (issue #31)
"""
from __future__ import annotations

import json
from pathlib import Path

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
    """Создать синтетическую директорию формы в 4-уровневом tmp-дереве.

    Layout: root/<object_type>/<object_name>/<container_name>/<form_name>/
    """
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


def _make_common_form(
    root: Path,
    form_name: str,
    *,
    with_bsl: bool = True,
    with_json: bool = True,
) -> Path:
    """Создать синтетическую общую форму в 3-уровневом tmp-дереве.

    Layout: root/CommonForm/<form_name>/
    """
    form_dir = root / "CommonForm" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_bsl:
        (form_dir / "CommonForm.obj.bsl").write_text(
            "// synthetic", encoding="utf-8"
        )
    if with_json:
        (form_dir / "CommonForm.json").write_text(
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
# CommonForm — 3-уровневый layout (нет object_name)
# ---------------------------------------------------------------------------

def test_scan_common_form(tmp_path: Path) -> None:
    """Общая форма: root/CommonForm/<form_name>/CommonForm.obj.bsl."""
    root = tmp_path / "cf_export"
    _make_common_form(root, "Основная")
    index = scan_forms(root)
    assert index.total == 1
    e = index.forms[0]
    assert e.object_type == "CommonForm"
    assert e.object_name == ""
    assert e.container_name == "CommonForm"
    assert e.form_name == "Основная"
    assert e.bsl_path.name == "CommonForm.obj.bsl"


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


def test_missing_bsl_recorded_in_scan_warnings(tmp_path: Path) -> None:
    """Формы без .obj.bsl явно попадают в scan_warnings (issue #31).

    Подтверждает наблюдаемость: 48 «пропущенных» форм не теряются молча.
    Три формы без bsl + одна полная → total==1, три предупреждения.
    """
    root = tmp_path / "cf_export"
    # полная форма
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаСписка")
    # три формы без bsl
    for name in ("ФормаБезБСЛ1", "ФормаБезБСЛ2", "ФормаБезБСЛ3"):
        _make_form(
            root, "Catalog", "Склады", "CatalogForm", name,
            with_bsl=False, with_json=True,
        )

    index = scan_forms(root)

    assert index.total == 1, "только полная форма должна попасть в индекс"
    skipped_warnings = [w for w in index.scan_warnings if "skipped" in w]
    assert len(skipped_warnings) == 3, (
        f"ожидали 3 предупреждения о пропущенных формах, "
        f"получили {len(skipped_warnings)}: {skipped_warnings}"
    )
    # имена форм без bsl должны быть упомянуты в предупреждениях
    warnings_text = " ".join(skipped_warnings)
    for name in ("ФормаБезБСЛ1", "ФормаБезБСЛ2", "ФормаБезБСЛ3"):
        assert name in warnings_text, (
            f"имя формы '{name}' должно быть упомянуто в scan_warnings"
        )


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
    _make_common_form(root, "Общая")
    index = scan_forms(root)
    assert index.total == 6
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
# best-effort: не-директория в контейнере пропускается, соседняя форма собирается
# ---------------------------------------------------------------------------

def test_best_effort_continues(tmp_path: Path) -> None:
    """Файл вместо директории формы пропускается (is_dir()==False),
    но соседняя нормальная форма всё равно собирается.
    Нет monkeypatch, нет зависимости от приватных имён модуля.
    """
    root = tmp_path / "cf_export"
    container_dir = root / "Catalog" / "Склады" / "CatalogForm"
    container_dir.mkdir(parents=True, exist_ok=True)

    # файл с именем формы — is_dir()==False, сканер пропустит его
    (container_dir / "НеДиректория").write_text("not a dir", encoding="utf-8")

    # нормальная форма рядом
    _make_form(root, "Catalog", "Склады", "CatalogForm", "ФормаСписка")

    index = scan_forms(root)
    assert index.total == 1
    assert index.forms[0].form_name == "ФормаСписка"
