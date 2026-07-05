"""Tests for issue #32: external mode scan_forms with .obj.bsl suffix (v8unpack 1.2.11).

Покрывает реальную схему живых данных:
- обработки: контейнер Form/, форма Form.obj.bsl, модуль ExternalDataProcessor.obj.bsl;
- отчёты:    контейнер ReportForm/, форма ReportForm.obj.bsl, тип ExternalReport
             определяется по контейнеру (модуля ExternalReport.obj.bsl может не быть).

All fixtures are synthetic — no domain names, hosts, or connection strings.
Paths are built via pathlib (OS-neutral).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import scan_forms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_form_dir(
    proc_dir: Path,
    container_name: str,
    form_name: str,
    bsl_filename: str,
) -> Path:
    """Создать синтетическую форму proc_dir/<container>/<form_name>/<bsl_filename>."""
    form_dir = proc_dir / container_name / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / bsl_filename).write_text("// synthetic bsl", encoding="utf-8")
    (form_dir / "Form.json").write_text("{}", encoding="utf-8")
    (form_dir / "Form.elem").write_text("{}", encoding="utf-8")
    (form_dir / "Form.id.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_processor(
    tmp_path: Path,
    processor_name: str = "SomeProcessor",
    form_bsl: str = "Form.obj.bsl",
    object_bsl: str = "ExternalDataProcessor.obj.bsl",
) -> Path:
    """Обработка: контейнер Form/, модуль объекта в каталоге обработки.

    Layout::
        <tmp_path>/<processor_name>/Form/MainForm/<form_bsl>
        <tmp_path>/<processor_name>/<object_bsl>
    Возвращает scan-root (<tmp_path>).
    """
    proc_dir = tmp_path / processor_name
    proc_dir.mkdir(parents=True, exist_ok=True)
    _make_form_dir(proc_dir, "Form", "MainForm", form_bsl)
    if object_bsl:
        (proc_dir / object_bsl).write_text("// synthetic object module", encoding="utf-8")
    return tmp_path


def _make_report(
    tmp_path: Path,
    report_name: str = "SomeReport",
    form_bsl: str = "ReportForm.obj.bsl",
    object_bsl: str = "",
) -> Path:
    """Отчёт: контейнер ReportForm/. Модуль объекта опционален (в живых данных часто нет).

    Layout::
        <tmp_path>/<report_name>/ReportForm/MainReportForm/<form_bsl>
        [<tmp_path>/<report_name>/<object_bsl>]  # опционально
    Возвращает scan-root (<tmp_path>).
    """
    proc_dir = tmp_path / report_name
    proc_dir.mkdir(parents=True, exist_ok=True)
    _make_form_dir(proc_dir, "ReportForm", "MainReportForm", form_bsl)
    if object_bsl:
        (proc_dir / object_bsl).write_text("// synthetic object module", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Обработки: контейнер Form/
# ---------------------------------------------------------------------------

class TestExternalProcessorForms:
    """Issue #32: external mode должен находить формы обработок с Form.obj.bsl."""

    def test_finds_forms_with_bsl_suffix(self, tmp_path):
        """Schema A (v8unpack 1.2.11): формы с Form.obj.bsl находятся."""
        root = _make_processor(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert result.total > 0, "external mode должен находить формы с Form.obj.bsl; got 0"

    def test_finds_forms_without_bsl_suffix(self, tmp_path):
        """Schema B (legacy): обратная совместимость — Form.obj без .bsl."""
        root = _make_processor(
            tmp_path,
            form_bsl="Form.obj",
            object_bsl="ExternalDataProcessor.obj",
        )
        result = scan_forms(root, mode="external")
        assert result.total > 0, "external mode должен оставаться совместим с Form.obj"

    def test_object_type_is_data_processor(self, tmp_path):
        """Контейнер Form + модуль ExternalDataProcessor.obj.bsl ⇒ ExternalDataProcessor."""
        root = _make_processor(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert result.total > 0
        assert all(f.object_type == "ExternalDataProcessor" for f in result.forms), (
            "тип обработки должен определяться как ExternalDataProcessor по модулю объекта"
        )

    def test_container_name_is_form(self, tmp_path):
        """container_name формы обработки — Form."""
        root = _make_processor(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert all(f.container_name == "Form" for f in result.forms)

    def test_prefers_bsl_over_legacy(self, tmp_path):
        """При наличии обоих Form.obj.bsl и Form.obj приоритет у .bsl."""
        scan_root = _make_processor(tmp_path, form_bsl="Form.obj.bsl")
        legacy = scan_root / "SomeProcessor" / "Form" / "MainForm" / "Form.obj"
        legacy.write_text("// legacy bsl", encoding="utf-8")

        result = scan_forms(scan_root, mode="external")
        assert result.total > 0
        assert result.forms[0].bsl_path.name == "Form.obj.bsl", (
            "Form.obj.bsl должен иметь приоритет над legacy Form.obj"
        )

    def test_returns_zero_for_empty_root(self, tmp_path):
        """Regression guard: пустой каталог ⇒ 0 форм, без исключения."""
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        result = scan_forms(empty_root, mode="external")
        assert result.total == 0


# ---------------------------------------------------------------------------
# Отчёты: контейнер ReportForm/
# ---------------------------------------------------------------------------

class TestExternalReportForms:
    """Issue #32: external mode должен находить формы отчётов в ReportForm/."""

    def test_finds_report_forms_with_bsl_suffix(self, tmp_path):
        """ReportForm/ + ReportForm.obj.bsl — формы отчёта находятся."""
        root = _make_report(tmp_path, form_bsl="ReportForm.obj.bsl")
        result = scan_forms(root, mode="external")
        assert result.total > 0, "external mode должен находить формы отчёта с ReportForm.obj.bsl"

    def test_report_object_type_is_external_report(self, tmp_path):
        """Контейнер ReportForm ⇒ object_type=ExternalReport (по контейнеру, без модуля)."""
        root = _make_report(tmp_path, form_bsl="ReportForm.obj.bsl", object_bsl="")
        result = scan_forms(root, mode="external")
        assert result.total > 0
        assert all(f.object_type == "ExternalReport" for f in result.forms), (
            "тип отчёта должен определяться как ExternalReport по контейнеру ReportForm"
        )

    def test_report_type_ignores_data_processor_module(self, tmp_path):
        """Даже если рядом лежит ExternalDataProcessor.obj.bsl, ReportForm ⇒ ExternalReport."""
        root = _make_report(
            tmp_path,
            form_bsl="ReportForm.obj.bsl",
            object_bsl="ExternalDataProcessor.obj.bsl",
        )
        result = scan_forms(root, mode="external")
        assert result.total > 0
        assert all(f.object_type == "ExternalReport" for f in result.forms), (
            "контейнер ReportForm имеет приоритет над модулем объекта при типизации"
        )

    def test_report_container_name_is_report_form(self, tmp_path):
        """container_name формы отчёта — ReportForm."""
        root = _make_report(tmp_path, form_bsl="ReportForm.obj.bsl")
        result = scan_forms(root, mode="external")
        assert all(f.container_name == "ReportForm" for f in result.forms)

    def test_report_forms_without_bsl_suffix(self, tmp_path):
        """Обратная совместимость: ReportForm.obj без .bsl тоже находится."""
        root = _make_report(tmp_path, form_bsl="ReportForm.obj")
        result = scan_forms(root, mode="external")
        assert result.total > 0, "external mode должен быть совместим с ReportForm.obj (legacy)"


# ---------------------------------------------------------------------------
# Смешанный корень: обработки и отчёты вместе (как в живой выгрузке)
# ---------------------------------------------------------------------------

class TestExternalMixedRoot:
    """Issue #32: один корень с обработками и отчётами — оба типа находятся и типизируются."""

    def test_mixed_root_finds_both_types(self, tmp_path):
        """В общем корне обработка (Form) и отчёт (ReportForm) видны обе."""
        _make_processor(tmp_path, processor_name="Proc1", form_bsl="Form.obj.bsl")
        _make_report(tmp_path, report_name="Report1", form_bsl="ReportForm.obj.bsl")

        result = scan_forms(tmp_path, mode="external")
        types = {f.object_type for f in result.forms}
        assert result.total >= 2, "должны найтись формы и обработки, и отчёта"
        assert types == {"ExternalDataProcessor", "ExternalReport"}, (
            f"ожидались оба типа, получено: {types}"
        )

    def test_mixed_root_no_skips(self, tmp_path):
        """В смешанном корне не должно быть skip-предупреждений по формам."""
        _make_processor(tmp_path, processor_name="Proc1", form_bsl="Form.obj.bsl")
        _make_report(tmp_path, report_name="Report1", form_bsl="ReportForm.obj.bsl")

        result = scan_forms(tmp_path, mode="external")
        skipped = [w for w in result.scan_warnings if w.startswith("skipped")]
        assert not skipped, f"неожиданные skip-предупреждения: {skipped}"
