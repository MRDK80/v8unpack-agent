from __future__ import annotations

from pathlib import Path

import pytest

from v8unpack_agent.common_modules import (
    CommonModuleEntry,
    build_common_module_context,
    scan_common_modules,
)


BSL_NAME = "CommonModule.obj.bsl"


def module_dir(root: Path, name: str) -> Path:
    path = root / "CommonModule" / name
    path.mkdir(parents=True)
    return path


def write_module(root: Path, name: str, text: str) -> Path:
    path = module_dir(root, name) / BSL_NAME
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_detects_common_module_without_json(tmp_path: Path) -> None:
    write_module(tmp_path, "Alpha", "Процедура Тест()\nКонецПроцедуры")

    index = scan_common_modules(tmp_path)

    assert index.total == 1
    assert index.modules == [
        CommonModuleEntry(
            name="Alpha",
            bsl_path=Path(
                "CommonModule",
                "Alpha",
                BSL_NAME,
            ),
        )
    ]


def test_scan_is_deterministic_and_sorted(tmp_path: Path) -> None:
    write_module(tmp_path, "Zulu", "z")
    write_module(tmp_path, "alpha", "a")
    write_module(tmp_path, "Beta", "b")

    first = scan_common_modules(tmp_path)
    second = scan_common_modules(tmp_path)

    assert first == second
    assert [entry.name for entry in first.modules] == [
        "alpha",
        "Beta",
        "Zulu",
    ]


def test_scan_includes_object_with_missing_bsl(tmp_path: Path) -> None:
    module_dir(tmp_path, "Missing")

    index = scan_common_modules(tmp_path)

    assert index.total == 1
    assert index.modules[0].name == "Missing"


def test_scan_ignores_foreign_and_direct_files(tmp_path: Path) -> None:
    write_module(tmp_path, "Included", "text")
    (tmp_path / "Report" / "Foreign").mkdir(parents=True)
    (tmp_path / "CommonModule" / "direct.bsl").write_text(
        "foreign",
        encoding="utf-8",
    )

    index = scan_common_modules(tmp_path)

    assert [entry.name for entry in index.modules] == ["Included"]


def test_scan_without_common_module_container_is_empty(
    tmp_path: Path,
) -> None:
    index = scan_common_modules(tmp_path)

    assert index.total == 0
    assert index.modules == []


def test_scan_rejects_invalid_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        scan_common_modules(missing)

    file_root = tmp_path / "not-a-directory"
    file_root.write_text("text", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        scan_common_modules(file_root)


def test_scan_rejects_common_module_container_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "CommonModule").write_text("text", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        scan_common_modules(tmp_path)


def test_context_reads_nonempty_utf8(tmp_path: Path) -> None:
    text = "Процедура Выполнить()\nКонецПроцедуры\n"
    write_module(tmp_path, "Alpha", text)
    entry = scan_common_modules(tmp_path).modules[0]

    context = build_common_module_context(entry, tmp_path)

    assert context.name == "Alpha"
    assert context.bsl_text == text
    assert context.read_status == "ok"
    assert context.metadata == {
        "bsl_path": "CommonModule/Alpha/CommonModule.obj.bsl"
    }
    assert not Path(context.metadata["bsl_path"]).is_absolute()


def test_context_distinguishes_empty_from_missing(
    tmp_path: Path,
) -> None:
    write_module(tmp_path, "Empty", "")
    module_dir(tmp_path, "Missing")
    entries = {
        entry.name: entry
        for entry in scan_common_modules(tmp_path).modules
    }

    empty = build_common_module_context(entries["Empty"], tmp_path)
    missing = build_common_module_context(entries["Missing"], tmp_path)

    assert empty.bsl_text == ""
    assert empty.read_status == "empty"
    assert missing.bsl_text is None
    assert missing.read_status == "missing"


def test_context_reports_invalid_utf8_as_read_error(
    tmp_path: Path,
) -> None:
    path = module_dir(tmp_path, "InvalidUtf8") / BSL_NAME
    path.write_bytes(b"\xff\xfe\xfa")
    entry = scan_common_modules(tmp_path).modules[0]

    context = build_common_module_context(entry, tmp_path)

    assert context.bsl_text is None
    assert context.read_status == "read_error"


def test_context_reports_os_error_without_leaking_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_module(tmp_path, "Unreadable", "text")
    entry = scan_common_modules(tmp_path).modules[0]
    original_read_text = Path.read_text

    def fail_read(
        candidate: Path,
        *args: object,
        **kwargs: object,
    ) -> str:
        if candidate == path:
            raise OSError("synthetic read failure")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)

    context = build_common_module_context(entry, tmp_path)

    assert context.bsl_text is None
    assert context.read_status == "read_error"
    assert str(tmp_path) not in repr(context.metadata)


def test_context_rejects_path_outside_export_root(
    tmp_path: Path,
) -> None:
    entry = CommonModuleEntry(
        name="Unsafe",
        bsl_path=Path("..", "outside.bsl"),
    )

    with pytest.raises(ValueError, match="relative"):
        build_common_module_context(entry, tmp_path)
