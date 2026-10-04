"""Сканер модулей наборов записей регистров (issue #206)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from v8unpack_agent import record_set_modules
from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.record_set_modules import (
    RECORD_SET_MODULE_FILES,
    scan_record_set_modules,
)

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
TEXT = "Процедура ПередЗаписью(Отказ, Замещение)\nКонецПроцедуры\n"
DATA = TEXT.encode("utf-8")
MARKER = "МаркерТекстаКоторогоНетВИндексе"
BS = chr(92)
PROVEN = "designer_content_match_A"


def put(
    root: Path,
    metadata_type: str,
    owner: str,
    name: str,
    data: bytes = DATA,
) -> Path:
    directory = root / metadata_type / owner
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


def populate(root: Path, owner: str = "Alpha", data: bytes = DATA) -> None:
    for metadata_type, file_name in RECORD_SET_MODULE_FILES.items():
        put(root, metadata_type, owner, file_name, data)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        key = path.relative_to(root).as_posix()
        result[key] = (info.st_size, info.st_mtime_ns)
    return result


def ids(index: ModuleIndex) -> set[str]:
    return {entry.module_id for entry in index}


def research_rows() -> list[dict[str, Any]]:
    data = json.loads(RESEARCH_JSON.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = data["detected"]
    return rows


# --- доказанная матрица #202 -----------------------------------------------


def test_files_match_issue202_proven_record_set_rows() -> None:
    proven = {
        row["metadata_type"]: row["path_pattern"]
        for row in research_rows()
        if row["owner_kind"] == "metadata_object"
        and row["module_kind"] == "record_set"
        and row["semantic_evidence"] == PROVEN
    }
    actual = {
        metadata_type: f"{metadata_type}/{{Name}}/{file_name}"
        for metadata_type, file_name in RECORD_SET_MODULE_FILES.items()
    }
    assert actual == proven
    assert sorted(proven) == ["AccumulationRegister", "InformationRegister"]


def test_unresolved_record_set_rows_are_excluded() -> None:
    unresolved = {
        row["metadata_type"]
        for row in research_rows()
        if row["module_kind"] == "record_set"
        and row["semantic_evidence"] != PROVEN
    }
    assert {"AccountingRegister", "Sequences"} <= unresolved
    assert not unresolved & set(RECORD_SET_MODULE_FILES)
    assert "CalculationRegister" not in RECORD_SET_MODULE_FILES


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        RECORD_SET_MODULE_FILES["Catalog"] = "x.bsl"  # type: ignore[index]


def test_metadata_layouts_are_not_extended_with_record_set() -> None:
    for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items():
        assert "record_set" not in layout
        if metadata_type in RECORD_SET_MODULE_FILES:
            assert RECORD_SET_MODULE_FILES[metadata_type] not in (
                layout.values()
            )


# --- позитивные fixtures ---------------------------------------------------


@pytest.mark.parametrize("metadata_type", sorted(RECORD_SET_MODULE_FILES))
def test_each_proven_register_type_is_detected(
    tmp_path: Path, metadata_type: str
) -> None:
    file_name = RECORD_SET_MODULE_FILES[metadata_type]
    put(tmp_path, metadata_type, "Alpha", file_name)
    index = scan_record_set_modules(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    assert entry.module_kind == "record_set"
    assert entry.owner_kind == "metadata_object"
    assert entry.metadata_type == metadata_type
    assert entry.owner_name == "Alpha"
    assert entry.read_status == "ok"
    assert entry.relative_path == f"{metadata_type}/Alpha/{file_name}"
    assert entry.module_id == (
        f"metadata_object:{metadata_type}:Alpha:record_set"
    )
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_register_obj_is_record_set_not_object(tmp_path: Path) -> None:
    populate(tmp_path)
    index = scan_record_set_modules(tmp_path)
    assert index.total == 2
    assert index.filter(module_kind="record_set").total == 2
    assert index.filter(module_kind="object").total == 0
    assert index.filter(module_kind="manager").total == 0


# --- неприменимые владельцы и иные layout ----------------------------------


def test_manager_form_and_command_files_are_not_scanned(
    tmp_path: Path,
) -> None:
    register = "InformationRegister"
    put(tmp_path, register, "Alpha", f"{register}.mgr.bsl")
    form = tmp_path / register / "Alpha" / f"{register}Form" / "ItemForm"
    form.mkdir(parents=True)
    (form / f"{register}Form.obj.bsl").write_bytes(DATA)
    command = tmp_path / register / "Alpha" / f"{register}Command" / "Run"
    command.mkdir(parents=True)
    (command / f"{register}Command.obj.bsl").write_bytes(DATA)
    ext = tmp_path / register / "Alpha" / "Ext"
    ext.mkdir()
    (ext / "RecordSetModule.bsl").write_bytes(DATA)
    index = scan_record_set_modules(tmp_path)
    assert ids(index) == {
        "metadata_object:InformationRegister:Alpha:record_set"
    }
    entry = next(iter(index))
    assert entry.read_status == "missing"
    assert entry.relative_path == (
        "InformationRegister/Alpha/InformationRegister.obj.bsl"
    )


@pytest.mark.parametrize(
    "metadata_type",
    [
        "AccountingRegister",
        "BusinessProcess",
        "CalculationRegister",
        "Catalog",
        "ChartOfAccounts",
        "ChartOfCalculationTypes",
        "ChartOfCharacteristicType",
        "CommonModule",
        "Constant",
        "DataProcessor",
        "Document",
        "Enum",
        "ExchangePlan",
        "HTTPService",
        "Report",
        "Sequences",
        "Task",
        "WebService",
    ],
)
def test_unproven_owner_types_give_no_entries(
    tmp_path: Path, metadata_type: str
) -> None:
    put(tmp_path, metadata_type, "Alpha", f"{metadata_type}.obj.bsl")
    (tmp_path / metadata_type / "Beta").mkdir()
    assert scan_record_set_modules(tmp_path) == ModuleIndex()


def test_accounting_register_is_not_guessed(tmp_path: Path) -> None:
    put(tmp_path, "AccountingRegister", "Alpha", "AccountingRegister.obj.bsl")
    put(tmp_path, "InformationRegister", "Beta", "InformationRegister.obj.bsl")
    index = scan_record_set_modules(tmp_path)
    assert {entry.metadata_type for entry in index} == {"InformationRegister"}


# --- правило missing -------------------------------------------------------


def test_missing_requires_existing_owner_dir(tmp_path: Path) -> None:
    assert scan_record_set_modules(tmp_path) == ModuleIndex()
    (tmp_path / "InformationRegister").mkdir()
    assert scan_record_set_modules(tmp_path) == ModuleIndex()
    (tmp_path / "InformationRegister" / "Readme.bsl").write_bytes(DATA)
    (tmp_path / "AccumulationRegister").write_bytes(DATA)
    assert scan_record_set_modules(tmp_path) == ModuleIndex()


def test_existing_owner_without_file_is_missing(tmp_path: Path) -> None:
    for metadata_type in RECORD_SET_MODULE_FILES:
        (tmp_path / metadata_type / "Alpha").mkdir(parents=True)
    index = scan_record_set_modules(tmp_path)
    assert index.total == 2
    for entry in index:
        assert entry.read_status == "missing"
        assert entry.module_kind == "record_set"
        assert entry.size_bytes is None
        assert entry.sha256 is None


def test_invalid_owner_dir_names_are_skipped(tmp_path: Path) -> None:
    register = "AccumulationRegister"
    for owner in ("1Alpha", "Alpha.bak", "Al-pha", "CON"):
        put(tmp_path, register, owner, f"{register}.obj.bsl")
    put(tmp_path, register, "Остатки", f"{register}.obj.bsl")
    index = scan_record_set_modules(tmp_path)
    assert {entry.owner_name for entry in index} == {"Остатки"}


def test_case_insensitive_duplicate_owner_is_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / "InformationRegister" / "Alpha").mkdir(parents=True)
    (tmp_path / "InformationRegister" / "ALPHA").mkdir(exist_ok=True)
    if len(list((tmp_path / "InformationRegister").iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    with pytest.raises(ValueError, match="duplicate module_id"):
        scan_record_set_modules(tmp_path)


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    ir = "InformationRegister"
    ar = "AccumulationRegister"
    put(tmp_path, ir, "Empty", f"{ir}.obj.bsl", b"")
    put(tmp_path, ir, "Bom", f"{ir}.obj.bsl", b"\xef\xbb\xbf")
    put(tmp_path, ir, "Spaces", f"{ir}.obj.bsl", b" \r\n\t")
    put(tmp_path, ar, "Broken", f"{ar}.obj.bsl", b"\xff\xfe\xfa")
    put(tmp_path, ar, "Text", f"{ar}.obj.bsl")
    (tmp_path / ar / "Absent").mkdir()
    index = scan_record_set_modules(tmp_path)

    def entry_of(metadata_type: str, owner: str) -> tuple[str, int | None]:
        module_id = f"metadata_object:{metadata_type}:{owner}:record_set"
        entry = index.get(module_id)
        assert entry is not None
        return entry.read_status, entry.size_bytes

    assert entry_of(ir, "Empty") == ("empty", 0)
    assert entry_of(ir, "Bom") == ("empty", 3)
    assert entry_of(ir, "Spaces") == ("whitespace_only", 4)
    assert entry_of(ar, "Broken") == ("read_error", 3)
    assert entry_of(ar, "Text") == ("ok", len(DATA))
    assert entry_of(ar, "Absent") == ("missing", None)
    assert index.filter(read_status="whitespace_only").total == 1


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put(
        tmp_path, "InformationRegister", "Alpha", "InformationRegister.obj.bsl"
    )
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_record_set_modules(tmp_path).get(
        "metadata_object:InformationRegister:Alpha:record_set"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    (
        tmp_path / "AccumulationRegister" / "Alpha"
        / "AccumulationRegister.obj.bsl"
    ).mkdir(parents=True)
    entry = scan_record_set_modules(tmp_path).get(
        "metadata_object:AccumulationRegister:Alpha:record_set"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None


def test_symlink_module_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    owner = root / "InformationRegister" / "Alpha"
    owner.mkdir(parents=True)
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    try:
        os.symlink(outside, owner / "InformationRegister.obj.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_record_set_modules(root).get(
        "metadata_object:InformationRegister:Alpha:record_set"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "InformationRegister").mkdir(parents=True)
    outside_owner = tmp_path / "outside_owner"
    outside_owner.mkdir()
    (outside_owner / "InformationRegister.obj.bsl").write_bytes(DATA)
    outside_type = tmp_path / "outside_type"
    (outside_type / "Beta").mkdir(parents=True)
    (outside_type / "Beta" / "AccumulationRegister.obj.bsl").write_bytes(DATA)
    try:
        os.symlink(
            outside_owner,
            root / "InformationRegister" / "Alpha",
            target_is_directory=True,
        )
        os.symlink(
            outside_type,
            root / "AccumulationRegister",
            target_is_directory=True,
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    assert scan_record_set_modules(root) == ModuleIndex()


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_record_set_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_record_set_modules(file_root)


# --- детерминированность, read-only, сериализация --------------------------


def test_traversal_order_does_not_change_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for owner in ("Zulu", "alpha", "Beta", "Остатки"):
        populate(tmp_path, owner)
    (tmp_path / "InformationRegister" / "Gamma").mkdir()
    baseline = scan_record_set_modules(tmp_path)
    original = Path.iterdir

    def reversed_iterdir(self: Path) -> Iterator[Path]:
        return iter(sorted(original(self), reverse=True))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reordered = scan_record_set_modules(tmp_path)
    assert reordered == baseline
    assert reordered.to_json() == baseline.to_json()
    assert baseline.total == 4 * 2 + 1


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    (tmp_path / "InformationRegister" / "Beta").mkdir()
    put(tmp_path, "AccountingRegister", "Gamma", "AccountingRegister.obj.bsl")
    before = snapshot(tmp_path)
    first = scan_record_set_modules(tmp_path)
    second = scan_record_set_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    index = scan_record_set_modules(tmp_path)
    payload = index.to_json()
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
    assert ModuleIndex.from_dict(data) == index


# --- совместимость ---------------------------------------------------------


def test_scanner_imports_only_module_contract() -> None:
    source = Path(record_set_modules.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    project = {name for name in imported if name.startswith("v8unpack_agent")}
    assert project == {
        "v8unpack_agent._exact_names",
        "v8unpack_agent.modules",
    }


def test_combines_with_metadata_object_modules(tmp_path: Path) -> None:
    for metadata_type, file_name in RECORD_SET_MODULE_FILES.items():
        put(tmp_path, metadata_type, "Alpha", file_name, b"// record set\n")
        put(
            tmp_path,
            metadata_type,
            "Alpha",
            f"{metadata_type}.mgr.bsl",
            b"// manager\n",
        )
    record_sets = scan_record_set_modules(tmp_path)
    managers = scan_metadata_object_modules(tmp_path)
    assert managers.filter(module_kind="record_set").total == 0
    assert managers.filter(module_kind="manager").total == 2
    combined = ModuleIndex.from_entries([*record_sets, *managers])
    assert combined.total == 4
    record_set = combined.get(
        "metadata_object:InformationRegister:Alpha:record_set"
    )
    manager = combined.get(
        "metadata_object:InformationRegister:Alpha:manager"
    )
    assert record_set is not None and manager is not None
    assert record_set.sha256 != manager.sha256
