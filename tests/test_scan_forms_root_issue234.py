"""Regression tests for invalid scan roots (issue #234)."""

from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import scan_forms


def test_missing_root_raises_not_a_directory_error_issue234(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing_export"

    with pytest.raises(NotADirectoryError, match="existing directory"):
        scan_forms(missing_root)


def test_file_root_raises_not_a_directory_error_issue234(tmp_path: Path) -> None:
    file_root = tmp_path / "export_file"
    file_root.write_text("synthetic", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="existing directory"):
        scan_forms(file_root)


def test_empty_directory_remains_valid_issue234(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty_export"
    empty_root.mkdir()

    index = scan_forms(empty_root)

    assert index.total == 0
    assert not index.forms


def test_invalid_root_does_not_create_save_target_issue234(tmp_path: Path) -> None:
    target = tmp_path / "result.json"

    with pytest.raises(NotADirectoryError):
        scan_forms(tmp_path / "missing_export", save_to=target)

    assert not target.exists()


def test_invalid_root_does_not_overwrite_save_target_issue234(tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    original = "preserve-me"
    target.write_text(original, encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        scan_forms(tmp_path / "missing_export", save_to=target)

    assert target.read_text(encoding="utf-8") == original
