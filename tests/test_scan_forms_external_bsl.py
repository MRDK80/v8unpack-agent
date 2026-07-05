"""Tests for issue #32: external mode scan_forms with Form.obj.bsl suffix (v8unpack 1.2.11).

All fixtures are synthetic — no domain names, hosts, or connection strings.
Paths are built via pathlib (OS-neutral).
"""
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_form_dir(base: Path, form_name: str, bsl_filename: str) -> Path:
    """Create a synthetic form directory with the given bsl_filename."""
    form_dir = base / "Form" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / bsl_filename).write_text("// synthetic bsl", encoding="utf-8")
    (form_dir / "Form.json").write_text("{}", encoding="utf-8")
    (form_dir / "Form.elem.json").write_text("{}", encoding="utf-8")
    (form_dir / "Form.id.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_external_root(
    tmp_path: Path,
    processor_name: str = "ExternalDataProcessor",
    form_bsl: str = "Form.obj.bsl",
    object_bsl: str = "ExternalDataProcessor.obj.bsl",
) -> Path:
    """Build a synthetic external processor directory tree."""
    root = tmp_path / processor_name
    root.mkdir(parents=True, exist_ok=True)
    _make_form_dir(root, "MainForm", form_bsl)
    (root / object_bsl).write_text("// synthetic object module", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExternalModeFormsBslSuffix:
    """Issue #32: scan_forms external mode must support Form.obj.bsl (v8unpack 1.2.11)."""

    def test_external_finds_forms_with_bsl_suffix(self, tmp_path):
        """Schema A (v8unpack 1.2.11): forms with Form.obj.bsl must be found."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert len(result) > 0, (
            "external mode must find forms with Form.obj.bsl suffix; got 0"
        )

    def test_external_finds_forms_without_bsl_suffix(self, tmp_path):
        """Schema B (legacy): backward compatibility — Form.obj without .bsl."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        root = _make_external_root(
            tmp_path,
            form_bsl="Form.obj",
            object_bsl="ExternalDataProcessor.obj",
        )
        result = scan_forms(root, mode="external")
        assert len(result) > 0, (
            "external mode must remain backward-compatible with Form.obj (no .bsl suffix)"
        )

    def test_external_object_type_is_set(self, tmp_path):
        """object_type must be ExternalDataProcessor or ExternalReport after scan."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        result = scan_forms(root, mode="external")
        assert len(result) > 0
        for form in result:
            assert form.get("object_type") in (
                "ExternalDataProcessor",
                "ExternalReport",
            ), f"Unexpected object_type: {form.get('object_type')}"

    def test_external_prefers_existing_file(self, tmp_path):
        """When both Form.obj.bsl and Form.obj exist, the existing one is used (no error)."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        root = _make_external_root(tmp_path, form_bsl="Form.obj.bsl")
        # Also create legacy file in same dir
        legacy = root / "Form" / "MainForm" / "Form.obj"
        legacy.write_text("// legacy bsl", encoding="utf-8")

        result = scan_forms(root, mode="external")
        assert len(result) > 0, "Must find forms when both Form.obj.bsl and Form.obj exist"

    def test_external_returns_zero_for_empty_root(self, tmp_path):
        """Regression guard: empty root directory must return 0 forms, not raise."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        empty_root = tmp_path / "EmptyProcessor"
        empty_root.mkdir()
        result = scan_forms(empty_root, mode="external")
        assert result == [] or len(result) == 0, (
            "Empty root must return empty list, not raise"
        )


class TestExternalModeExternalReport:
    """Issue #32: same behaviour for ExternalReport type."""

    def test_external_report_finds_forms_with_bsl_suffix(self, tmp_path):
        """ExternalReport must also be found with Form.obj.bsl."""
        from src.scan_forms import scan_forms  # noqa: PLC0415

        root = _make_external_root(
            tmp_path,
            processor_name="ExternalReport",
            form_bsl="Form.obj.bsl",
            object_bsl="ExternalReport.obj.bsl",
        )
        result = scan_forms(root, mode="external")
        assert len(result) > 0, "ExternalReport: must find forms with Form.obj.bsl suffix"
