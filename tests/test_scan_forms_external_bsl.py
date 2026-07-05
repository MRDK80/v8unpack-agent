"""Tests for issue #32: external mode scan_forms with Form.obj.bsl suffix (v8unpack 1.2.11).

All fixtures are synthetic — no domain names, hosts, or connection strings.
Paths are built via pathlib (OS-neutral).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import scan_forms


def _make_form_dir(base: Path, form_name: str, bsl_filename: str) -> Path:
    """Create a synthetic form directory under base/Form/<form_name>/."""
    form_dir = base / "Form" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / bsl_filename).write_text("// synthetic bsl", encoding="utf-8")
    (form_dir / "Form.json").write_text("{}", encoding="utf-8")
    (form_dir / "Form.elem").write_text("{}", encoding="utf-8")
    (form_dir / "Form.id.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_external_root(
    tmp_path: Path,
    processor_name: str = "ExternalDataProcessor",
    form_bsl: str = "Form.obj.bsl",
    object_bsl: str = "ExternalDataProcessor.obj.bsl",
) -> Path:
    """Build a synthetic external tree and return the scan root (processors level).

    Layout::
        <tmp_path>/<processor_name>/Form/MainForm/<form_bsl>
        <tmp_path>/<processor_name>/<object_bsl>
    scan_forms is called on <tmp_path>.
    """
    proc_dir = tmp_path / processor_name
    proc_dir.mkdir(parents=True, exist_ok=True)
    _make_form_dir(proc_dir, "MainForm", form_bsl)
    (proc_dir / object_bsl).write_text("// synthetic object module", encoding="utf-8")
    return tmp_path


class TestExternalModeFormsBslSuffix:
    """Issue #32: scan_forms external mode must support Form.obj.bsl (v8unpack 1.2.11)."""

    def test_external_finds_forms_with_bsl_suffix(self, tmp_path):
        """Schema A (v8unpack 1.2.11): forms with Form.obj.bsl must be found."""
        root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert result.total > 0, (
            "external mode must find forms with Form.obj.bsl suffix; got 0"
        )

    def test_external_finds_forms_without_bsl_suffix(self, tmp_path):
        """Schema B (legacy): backward compatibility — Form.obj without .bsl."""
        root = _make_external_root(
            tmp_path,
            form_bsl="Form.obj",
            object_bsl="ExternalDataProcessor.obj",
        )
        result = scan_forms(root, mode="external")
        assert result.total > 0, (
            "external mode must remain backward-compatible with Form.obj (no .bsl suffix)"
        )

    def test_external_object_type_is_set(self, tmp_path):
        """object_type must be ExternalDataProcessor or ExternalReport after scan."""
        root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert result.total > 0
        for form in result.forms:
            assert form.object_type in (
                "ExternalDataProcessor",
                "ExternalReport",
            ), f"Unexpected object_type: {form.object_type}"

    def test_external_prefers_existing_file(self, tmp_path):
        """When both Form.obj.bsl and Form.obj exist, the .bsl variant is preferred."""
        scan_root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        legacy = scan_root / "ExternalDataProcessor" / "Form" / "MainForm" / "Form.obj"
        legacy.write_text("// legacy bsl", encoding="utf-8")

        result = scan_forms(scan_root, mode="external")
        assert result.total > 0, "Must find forms when both Form.obj.bsl and Form.obj exist"
        assert result.forms[0].bsl_path.name == "Form.obj.bsl", (
            "Form.obj.bsl must take priority over legacy Form.obj"
        )

    def test_external_returns_zero_for_empty_root(self, tmp_path):
        """Regression guard: empty root directory must return 0 forms, not raise."""
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        result = scan_forms(empty_root, mode="external")
        assert result.total == 0, "Empty root must return zero forms, not raise"


class TestExternalModeExternalReport:
    """Issue #32: same behaviour for ExternalReport type."""

    def test_external_report_finds_forms_with_bsl_suffix(self, tmp_path):
        """ExternalReport must also be found with Form.obj.bsl and typed correctly."""
        root = _make_external_root(
            tmp_path,
            processor_name="ExternalReport",
            form_bsl="Form.obj.bsl",
            object_bsl="ExternalReport.obj.bsl",
        )
        result = scan_forms(root, mode="external")
        assert result.total > 0, "ExternalReport: must find forms with Form.obj.bsl suffix"
        assert all(f.object_type == "ExternalReport" for f in result.forms), (
            "object_type must be resolved to ExternalReport by object module name"
        )
