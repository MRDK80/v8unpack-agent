"""Сканер модулей уровня конфигурации (issue #204)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from v8unpack_agent.configuration_modules import (
    CONFIGURATION_MODULE_FILES,
    scan_configuration_modules,
)
from v8unpack_agent.modules import ModuleIndex

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
TEXT = "Процедура ПриНачалеРаботыСистемы()\nКонецПроцедуры\n"
MARKER = "МаркерТекстаКоторогоНетВИндексе"
BS = chr(92)


def write_all(root: Path, text: str = TEXT) -> None:
    for name in CONFIGURATION_MODULE_FILES.values():
        (root / name).write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        key = path.relative_to(root).as_posix()
        result[key] = (info.st_size, info.st_mtime_ns)
    return result


# --- доказанные соответствия #202 ------------------------------------------


def test_mapping_matches_issue202_proven_rows() -> None:
    data = json.loads(RESEARCH_JSON.read_text(encoding="utf-8"))
    proven = {
        row["module_kind"]: row["path_pattern"]
        for row in data["detected"]
        if row["owner_kind"] == "configuration"
        and row["semantic_evidence"] == "designer_exact_match_A"
    }
    assert dict(CONFIGURATION_MODULE_FILES) == proven
    assert sorted(proven) == [
        "external_connection",
        "managed_application",
        "ordinary_application",
        "session",
    ]


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        CONFIGURATION_MODULE_FILES["session"] = "x.bsl"  # type: ignore[index]


def test_all_four_kinds_are_detected(tmp_path: Path) -> None:
    write_all(tmp_path)
    index = scan_configuration_modules(tmp_path)
    assert index.total == 4
    assert [(e.module_kind, e.relative_path) for e in index] == [
        ("ordinary_application", "Configuration.802.bsl"),
        ("managed_application", "Configuration.app.bsl"),
        ("external_connection", "Configuration.con.bsl"),
        ("session", "Configuration.seance.bsl"),
    ]
    data = TEXT.encode("utf-8")
    for entry in index:
        assert entry.owner_kind == "configuration"
        assert entry.metadata_type is None
        assert entry.owner_name is None
        assert entry.module_id == f"configuration:::{entry.module_kind}"
        assert entry.read_status == "ok"
        assert entry.size_bytes == len(data)
        assert entry.sha256 == hashlib.sha256(data).hexdigest()


# --- недоказанные суффиксы -------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Configuration.ssn.bsl",
        "Configuration.obj.bsl",
        "Configuration.mgr.bsl",
        "Configuration.app.0.bsl",
        "Configuration.app.txt",
        "ConfigurationExtension.app.bsl",
        "ConfigurationExtension.ssn.bsl",
        "ConfigurationExtension.con.bsl",
    ],
)
def test_unproven_files_are_not_classified(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / name).write_text(TEXT, encoding="utf-8")
    assert scan_configuration_modules(tmp_path) == ModuleIndex()
    write_all(tmp_path)
    paths = {e.relative_path for e in scan_configuration_modules(tmp_path)}
    assert paths == set(CONFIGURATION_MODULE_FILES.values())


def test_nested_configuration_dir_is_not_scanned(tmp_path: Path) -> None:
    nested = tmp_path / "Configuration"
    nested.mkdir()
    write_all(nested)
    assert scan_configuration_modules(tmp_path).total == 0


def test_root_without_configuration_modules_is_empty(
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "CommonModule" / "Alpha"
    module_dir.mkdir(parents=True)
    (module_dir / "CommonModule.obj.bsl").write_text(TEXT, encoding="utf-8")
    index = scan_configuration_modules(tmp_path)
    assert index.total == 0
    assert index.to_dict()["entries"] == []


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    (tmp_path / "Configuration.app.bsl").write_bytes(b"")
    (tmp_path / "Configuration.seance.bsl").write_bytes(b" \r\n\t")
    (tmp_path / "Configuration.con.bsl").write_bytes(b"\xff\xfe\xfa")
    index = scan_configuration_modules(tmp_path)
    by_kind = {e.module_kind: e for e in index}
    assert by_kind["managed_application"].read_status == "empty"
    assert by_kind["managed_application"].size_bytes == 0
    assert by_kind["session"].read_status == "whitespace_only"
    assert by_kind["external_connection"].read_status == "read_error"
    assert by_kind["external_connection"].size_bytes == 3
    missing = by_kind["ordinary_application"]
    assert missing.read_status == "missing"
    assert missing.relative_path == "Configuration.802.bsl"
    assert missing.size_bytes is None
    assert missing.sha256 is None


def test_bom_only_is_empty(tmp_path: Path) -> None:
    (tmp_path / "Configuration.app.bsl").write_bytes(b"\xef\xbb\xbf")
    index = scan_configuration_modules(tmp_path)
    entry = index.get("configuration:::managed_application")
    assert entry is not None
    assert entry.read_status == "empty"


def test_layout_without_ordinary_application_module(tmp_path: Path) -> None:
    write_all(tmp_path)
    (tmp_path / "Configuration.802.bsl").unlink()
    index = scan_configuration_modules(tmp_path)
    assert index.filter(read_status="ok").total == 3
    assert index.filter(read_status="missing").total == 1
    assert index.filter(module_kind="ordinary_application").total == 1


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_all(tmp_path)
    target = tmp_path / "Configuration.seance.bsl"
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_configuration_modules(tmp_path).get(
        "configuration:::session"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    write_all(tmp_path)
    (tmp_path / "Configuration.app.bsl").unlink()
    (tmp_path / "Configuration.app.bsl").mkdir()
    entry = scan_configuration_modules(tmp_path).get(
        "configuration:::managed_application"
    )
    assert entry is not None
    assert entry.read_status == "read_error"


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    outside = tmp_path / "outside.bsl"
    outside.write_text(TEXT, encoding="utf-8")
    try:
        os.symlink(outside, root / "Configuration.app.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_configuration_modules(root).get(
        "configuration:::managed_application"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_configuration_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        scan_configuration_modules(file_root)


# --- повторяемость, read-only, сериализация --------------------------------


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    write_all(tmp_path, TEXT + "// " + MARKER + "\n")
    (tmp_path / "Configuration.802.bsl").unlink()
    before = snapshot(tmp_path)
    first = scan_configuration_modules(tmp_path)
    second = scan_configuration_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    write_all(tmp_path, TEXT + "// " + MARKER + "\n")
    payload = scan_configuration_modules(tmp_path).to_json()
    assert MARKER not in payload
    assert "Процедура" not in payload
    assert BS not in payload
    assert str(tmp_path) not in payload
    data = json.loads(payload)
    assert data["schema"] == "module_index/1"
    for item in data["entries"]:
        value = item["relative_path"]
        assert not PurePosixPath(value).is_absolute()
        assert not PureWindowsPath(value).drive
        assert not PureWindowsPath(value).root
    assert ModuleIndex.from_dict(data) == scan_configuration_modules(tmp_path)
