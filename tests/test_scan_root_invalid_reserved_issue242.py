"""Issue #242: SCAN_ROOT_INVALID — зарезервированный legacy-код.

После #234 невалидный корень выгрузки всегда приводит к ``NotADirectoryError``.
Недостижимая ветка с предупреждением удалена, но сам код остаётся в каноническом
перечне, чтобы ранее сохранённые предупреждения по-прежнему разбирались.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import (
    SCAN_WARNING_CODE_MARKER,
    SCAN_WARNING_CODES,
    SCAN_WARNING_SCAN_ROOT_INVALID,
    scan_forms,
    scan_warning_code,
)


def test_missing_root_raises_not_a_directory_issue242(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        scan_forms(tmp_path / "absent")


def test_file_root_raises_not_a_directory_issue242(tmp_path: Path) -> None:
    file_root = tmp_path / "root.txt"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        scan_forms(file_root)


def test_empty_root_does_not_emit_scan_root_invalid_issue242(
    tmp_path: Path,
) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    index = scan_forms(empty_root)
    codes = [scan_warning_code(warning) for warning in index.scan_warnings]

    assert SCAN_WARNING_SCAN_ROOT_INVALID not in codes


def test_scan_root_invalid_stays_reserved_for_reading_issue242() -> None:
    assert SCAN_WARNING_SCAN_ROOT_INVALID in SCAN_WARNING_CODES

    legacy_warning = (
        "legacy scan warning"
        f"{SCAN_WARNING_CODE_MARKER}{SCAN_WARNING_SCAN_ROOT_INVALID}]"
    )

    assert scan_warning_code(legacy_warning) == SCAN_WARNING_SCAN_ROOT_INVALID
