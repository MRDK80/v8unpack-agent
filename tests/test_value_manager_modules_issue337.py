"""Сканер модулей менеджеров значений констант (issue #337)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from v8unpack_agent import value_manager_modules
from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.record_set_modules import (
    RECORD_SET_MODULE_FILES,
    scan_record_set_modules,
)
from v8unpack_agent.service_modules import scan_service_modules
from v8unpack_agent.value_manager_modules import (
    VALUE_MANAGER_MODULE_FILES,
    scan_value_manager_modules,
)

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
TYPE = "Constant"
FILE = "Constant.obj.bsl"
TEXT = "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n"
DATA = TEXT.encode("utf-8")
MARKER = "МаркерТекстаКоторогоНетВИндексе"
BS = chr(92)
PROVEN = "designer_content_match_A"


def put(
    root: Path,
    owner: str,
    data: bytes = DATA,
    *,
    metadata_type: str = TYPE,
    name: str = FILE,
) -> Path:
    directory = root / metadata_type / owner
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


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


def test_files_match_issue202_proven_value_manager_rows() -> None:
    proven = {
        row["metadata_type"]: (row["owner_kind"], row["path_pattern"])
        for row in research_rows()
        if row["module_kind"] == "value_manager"
    }
    assert proven == {TYPE: ("metadata_object", f"{TYPE}/{{Name}}/{FILE}")}
    evidence = {
        row["semantic_evidence"]
        for row in research_rows()
        if row["module_kind"] == "value_manager"
    }
    assert evidence == {PROVEN}
    assert dict(VALUE_MANAGER_MODULE_FILES) == {TYPE: FILE}


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        VALUE_MANAGER_MODULE_FILES["Catalog"] = "x.bsl"  # type: ignore[index]


def test_neighbour_scanners_do_not_claim_constant() -> None:
    assert TYPE not in METADATA_OBJECT_MODULE_LAYOUTS
    assert TYPE not in RECORD_SET_MODULE_FILES
    for layout in METADATA_OBJECT_MODULE_LAYOUTS.values():
        assert "value_manager" not in layout


# --- позитивные fixtures ---------------------------------------------------


def test_constant_value_manager_is_detected(tmp_path: Path) -> None:
    put(tmp_path, "Alpha")
    index = scan_value_manager_modules(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    assert entry.module_kind == "value_manager"
    assert entry.owner_kind == "metadata_object"
    assert entry.metadata_type == TYPE
    assert entry.owner_name == "Alpha"
    assert entry.read_status == "ok"
    assert entry.relative_path == "Constant/Alpha/Constant.obj.bsl"
    assert entry.module_id == "metadata_object:Constant:Alpha:value_manager"
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_constant_obj_is_not_object_manager_or_form(tmp_path: Path) -> None:
    put(tmp_path, "Alpha")
    put(tmp_path, "Товары")
    index = scan_value_manager_modules(tmp_path)
    assert index.total == 2
    assert index.filter(module_kind="value_manager").total == 2
    assert index.filter(module_kind="object").total == 0
    assert index.filter(module_kind="manager").total == 0
    assert index.filter(module_kind="form").total == 0
    assert index.filter(owner_kind="metadata_object_form").total == 0
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()
    assert scan_record_set_modules(tmp_path) == ModuleIndex()


# --- неприменимые владельцы и иные layout ----------------------------------


def test_nested_form_and_designer_layout_are_not_scanned(
    tmp_path: Path,
) -> None:
    owner = tmp_path / TYPE / "Alpha"
    form = owner / "ConstantForm" / "ItemForm"
    form.mkdir(parents=True)
    (form / "ConstantForm.obj.bsl").write_bytes(DATA)
    ext = owner / "Ext"
    ext.mkdir()
    (ext / "ValueManagerModule.bsl").write_bytes(DATA)
    (owner / "Constant.mgr.bsl").write_bytes(DATA)
    index = scan_value_manager_modules(tmp_path)
    assert ids(index) == {"metadata_object:Constant:Alpha:value_manager"}
    entry = next(iter(index))
    assert entry.read_status == "missing"
    assert entry.relative_path == "Constant/Alpha/Constant.obj.bsl"


@pytest.mark.parametrize(
    "metadata_type",
    [
        "AccountingRegister",
        "AccumulationRegister",
        "BusinessProcess",
        "Catalog",
        "ChartOfAccounts",
        "ChartOfCharacteristicType",
        "CommonModule",
        "Constants",
        "DataProcessor",
        "Document",
        "Enum",
        "ExchangePlan",
        "HTTPService",
        "InformationRegister",
        "Report",
        "Sequences",
        "Task",
        "WebService",
    ],
)
def test_unproven_owner_types_give_no_entries(
    tmp_path: Path, metadata_type: str
) -> None:
    put(
        tmp_path,
        "Alpha",
        metadata_type=metadata_type,
        name=f"{metadata_type}.obj.bsl",
    )
    (tmp_path / metadata_type / "Beta").mkdir()
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()


def test_lowercase_type_dir_is_not_classified(tmp_path: Path) -> None:
    put(tmp_path, "Alpha", metadata_type="constant")
    if (tmp_path / TYPE).exists():
        pytest.skip("case-insensitive file system")
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()


# --- правило missing -------------------------------------------------------


def test_missing_requires_existing_owner_dir(tmp_path: Path) -> None:
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()
    (tmp_path / TYPE).mkdir()
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()
    (tmp_path / TYPE / "Readme.bsl").write_bytes(DATA)
    (tmp_path / TYPE / FILE).write_bytes(DATA)
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()


def test_existing_owner_without_file_is_missing_not_error(
    tmp_path: Path,
) -> None:
    (tmp_path / TYPE / "Alpha").mkdir(parents=True)
    (tmp_path / TYPE / "Beta").mkdir()
    (tmp_path / TYPE / "Beta" / "Constant.json").write_bytes(b"{}")
    index = scan_value_manager_modules(tmp_path)
    assert index.total == 2
    for entry in index:
        assert entry.read_status == "missing"
        assert entry.module_kind == "value_manager"
        assert entry.size_bytes is None
        assert entry.sha256 is None
    assert index.filter(read_status="read_error").total == 0


def test_invalid_owner_dir_names_are_skipped(tmp_path: Path) -> None:
    for owner in ("1Alpha", "Alpha.bak", "Al-pha", "CON", "Alpha Beta"):
        put(tmp_path, owner)
    put(tmp_path, "ВалютаУчёта")
    index = scan_value_manager_modules(tmp_path)
    assert {entry.owner_name for entry in index} == {"ВалютаУчёта"}


def test_case_insensitive_duplicate_owner_is_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / TYPE / "Alpha").mkdir(parents=True)
    (tmp_path / TYPE / "ALPHA").mkdir(exist_ok=True)
    if len(list((tmp_path / TYPE).iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    with pytest.raises(ValueError, match="duplicate module_id"):
        scan_value_manager_modules(tmp_path)


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    put(tmp_path, "Empty", b"")
    put(tmp_path, "Bom", b"\xef\xbb\xbf")
    put(tmp_path, "Spaces", b" \r\n\t")
    put(tmp_path, "Broken", b"\xff\xfe\xfa")
    put(tmp_path, "Text")
    (tmp_path / TYPE / "Absent").mkdir()
    index = scan_value_manager_modules(tmp_path)

    def entry_of(owner: str) -> tuple[str, int | None]:
        entry = index.get(f"metadata_object:Constant:{owner}:value_manager")
        assert entry is not None
        return entry.read_status, entry.size_bytes

    assert entry_of("Empty") == ("empty", 0)
    assert entry_of("Bom") == ("empty", 3)
    assert entry_of("Spaces") == ("whitespace_only", 4)
    assert entry_of("Broken") == ("read_error", 3)
    assert entry_of("Text") == ("ok", len(DATA))
    assert entry_of("Absent") == ("missing", None)
    assert index.total == 6
    for status in ("ok", "empty", "whitespace_only", "missing", "read_error"):
        assert index.filter(read_status=status).total >= 1


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put(tmp_path, "Alpha")
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_value_manager_modules(tmp_path).get(
        "metadata_object:Constant:Alpha:value_manager"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    (tmp_path / TYPE / "Alpha" / FILE).mkdir(parents=True)
    entry = scan_value_manager_modules(tmp_path).get(
        "metadata_object:Constant:Alpha:value_manager"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None


def test_symlink_module_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    owner = root / TYPE / "Alpha"
    owner.mkdir(parents=True)
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    try:
        os.symlink(outside, owner / FILE)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_value_manager_modules(root).get(
        "metadata_object:Constant:Alpha:value_manager"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    outside_owner = tmp_path / "outside_owner"
    outside_owner.mkdir()
    (outside_owner / FILE).write_bytes(DATA)
    outside_type = tmp_path / "outside_type"
    (outside_type / "Beta").mkdir(parents=True)
    (outside_type / "Beta" / FILE).write_bytes(DATA)
    try:
        os.symlink(outside_type, root / TYPE, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    assert scan_value_manager_modules(root) == ModuleIndex()
    os.unlink(root / TYPE)
    (root / TYPE).mkdir()
    os.symlink(outside_owner, root / TYPE / "Alpha", target_is_directory=True)
    assert scan_value_manager_modules(root) == ModuleIndex()


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_value_manager_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_value_manager_modules(file_root)


# --- детерминированность, read-only, сериализация --------------------------


def test_traversal_order_does_not_change_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for owner in ("Zulu", "alpha", "Beta", "Валюта"):
        put(tmp_path, owner)
    (tmp_path / TYPE / "Gamma").mkdir()
    baseline = scan_value_manager_modules(tmp_path)
    original = Path.iterdir

    def reversed_iterdir(self: Path) -> Iterator[Path]:
        return iter(sorted(original(self), reverse=True))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reordered = scan_value_manager_modules(tmp_path)
    assert reordered == baseline
    assert reordered.to_json() == baseline.to_json()
    assert baseline.total == 5
    assert [entry.owner_name for entry in baseline] == [
        "alpha",
        "Beta",
        "Gamma",
        "Zulu",
        "Валюта",
    ]


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    put(tmp_path, "Alpha", DATA + ("// " + MARKER + "\n").encode())
    (tmp_path / TYPE / "Beta").mkdir()
    put(tmp_path, "Broken", b"\xff")
    before = snapshot(tmp_path)
    first = scan_value_manager_modules(tmp_path)
    second = scan_value_manager_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    put(tmp_path, "Alpha", DATA + ("// " + MARKER + "\n").encode())
    (tmp_path / TYPE / "Beta").mkdir()
    index = scan_value_manager_modules(tmp_path)
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
        assert item["module_kind"] == "value_manager"
        assert item["metadata_type"] == TYPE
    assert ModuleIndex.from_dict(data) == index


# --- совместимость ---------------------------------------------------------


def test_scanner_imports_only_module_contract() -> None:
    source = Path(value_manager_modules.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    project = {name for name in imported if name.startswith("v8unpack_agent")}
    assert project == {"v8unpack_agent.modules"}


def test_combines_with_other_scanners(tmp_path: Path) -> None:
    put(tmp_path, "Alpha", b"// value manager\n")
    put(
        tmp_path,
        "Alpha",
        b"// object\n",
        metadata_type="Catalog",
        name="Catalog.obj.bsl",
    )
    put(
        tmp_path,
        "Alpha",
        b"// record set\n",
        metadata_type="InformationRegister",
        name="InformationRegister.obj.bsl",
    )
    put(
        tmp_path,
        "Alpha",
        b"// web\n",
        metadata_type="WebService",
        name="WebService.obj.bsl",
    )
    value_managers = scan_value_manager_modules(tmp_path)
    objects = scan_metadata_object_modules(tmp_path)
    record_sets = scan_record_set_modules(tmp_path)
    services = scan_service_modules(tmp_path)
    assert objects.filter(module_kind="value_manager").total == 0
    assert record_sets.filter(module_kind="value_manager").total == 0
    assert services.filter(module_kind="value_manager").total == 0
    combined = ModuleIndex.from_entries(
        [*value_managers, *objects, *record_sets, *services]
    )
    assert combined.total == (
        value_managers.total
        + objects.total
        + record_sets.total
        + services.total
    )
    assert combined.filter(module_kind="value_manager").total == 1
    value_manager = combined.get(
        "metadata_object:Constant:Alpha:value_manager"
    )
    catalog_object = combined.get("metadata_object:Catalog:Alpha:object")
    assert value_manager is not None and catalog_object is not None
    assert value_manager.sha256 != catalog_object.sha256
