"""Сканер модулей объектов и менеджеров метаданных (issue #205)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from v8unpack_agent import metadata_modules
from v8unpack_agent.configuration_modules import scan_configuration_modules
from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)
from v8unpack_agent.modules import ModuleIndex

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
TEXT = "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n"
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
    for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items():
        for file_name in layout.values():
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


# --- доказанная матрица #202 -----------------------------------------------


def test_layouts_match_issue202_proven_rows() -> None:
    data = json.loads(RESEARCH_JSON.read_text(encoding="utf-8"))
    proven: dict[str, dict[str, str]] = {}
    for row in data["detected"]:
        if (
            row["owner_kind"] == "metadata_object"
            and row["module_kind"] in ("object", "manager")
            and row["semantic_evidence"] == PROVEN
        ):
            proven.setdefault(row["metadata_type"], {})[
                row["module_kind"]
            ] = row["path_pattern"]
    actual = {
        metadata_type: {
            kind: f"{metadata_type}/{{Name}}/{file_name}"
            for kind, file_name in layout.items()
        }
        for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items()
    }
    assert actual == proven
    assert sum(len(layout) for layout in proven.values()) == 17


def test_unresolved_and_other_kinds_are_excluded() -> None:
    data = json.loads(RESEARCH_JSON.read_text(encoding="utf-8"))
    excluded = {
        (row["metadata_type"], row["module_kind"])
        for row in data["detected"]
        if row["owner_kind"] == "metadata_object"
        and (
            row["module_kind"] not in ("object", "manager")
            or row["semantic_evidence"] != PROVEN
        )
    }
    assert ("Sequences", "record_set") in excluded
    assert ("ChartOfAccounts", "object") in excluded
    assert ("ChartOfCalculationTypes", "object") in excluded
    for metadata_type, kind in excluded:
        layout = METADATA_OBJECT_MODULE_LAYOUTS.get(metadata_type, {})
        assert kind not in layout
        file_name = f"{metadata_type}.obj.bsl"
        if kind != "manager":
            assert file_name not in layout.values()


def test_layouts_are_read_only() -> None:
    with pytest.raises(TypeError):
        METADATA_OBJECT_MODULE_LAYOUTS["Catalog"] = {}  # type: ignore[index]
    inner = METADATA_OBJECT_MODULE_LAYOUTS["Catalog"]
    with pytest.raises(TypeError):
        inner["object"] = "x.bsl"  # type: ignore[index]


# --- позитивные fixtures ---------------------------------------------------


@pytest.mark.parametrize(
    "metadata_type", sorted(METADATA_OBJECT_MODULE_LAYOUTS)
)
def test_each_proven_type_is_detected(
    tmp_path: Path, metadata_type: str
) -> None:
    layout = METADATA_OBJECT_MODULE_LAYOUTS[metadata_type]
    for file_name in layout.values():
        put(tmp_path, metadata_type, "Alpha", file_name)
    index = scan_metadata_object_modules(tmp_path)
    assert index.total == len(layout)
    for entry in index:
        assert entry.owner_kind == "metadata_object"
        assert entry.metadata_type == metadata_type
        assert entry.owner_name == "Alpha"
        assert entry.read_status == "ok"
        assert entry.relative_path == (
            f"{metadata_type}/Alpha/{layout[entry.module_kind]}"
        )
        assert entry.module_id == (
            f"metadata_object:{metadata_type}:Alpha:{entry.module_kind}"
        )
        assert entry.size_bytes == len(DATA)
        assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_all_proven_pairs_together(tmp_path: Path) -> None:
    populate(tmp_path)
    index = scan_metadata_object_modules(tmp_path)
    assert index.total == 17
    assert index.filter(module_kind="object").total == 8
    assert index.filter(module_kind="manager").total == 9
    assert index.filter(read_status="ok").total == 17


def test_object_and_manager_are_separate_entries(tmp_path: Path) -> None:
    put(tmp_path, "Catalog", "Alpha", "Catalog.obj.bsl", b"// object\n")
    put(tmp_path, "Catalog", "Alpha", "Catalog.mgr.bsl", b"// manager\n")
    index = scan_metadata_object_modules(tmp_path)
    obj = index.get("metadata_object:Catalog:Alpha:object")
    mgr = index.get("metadata_object:Catalog:Alpha:manager")
    assert obj is not None and mgr is not None
    assert obj.relative_path == "Catalog/Alpha/Catalog.obj.bsl"
    assert mgr.relative_path == "Catalog/Alpha/Catalog.mgr.bsl"
    assert obj.sha256 != mgr.sha256


def test_enum_obj_is_manager_module(tmp_path: Path) -> None:
    put(tmp_path, "Enum", "Alpha", "Enum.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert ids(index) == {"metadata_object:Enum:Alpha:manager"}


# --- формы и иные layout ---------------------------------------------------


def test_form_module_is_not_object_module(tmp_path: Path) -> None:
    (tmp_path / "Catalog" / "Alpha" / "CatalogForm" / "ItemForm").mkdir(
        parents=True
    )
    (
        tmp_path / "Catalog" / "Alpha" / "CatalogForm" / "ItemForm"
        / "CatalogForm.obj.bsl"
    ).write_bytes(DATA)
    (tmp_path / "DataProcessor" / "Beta" / "Form" / "Main").mkdir(
        parents=True
    )
    (
        tmp_path / "DataProcessor" / "Beta" / "Form" / "Main" / "Form.obj.bsl"
    ).write_bytes(DATA)
    put(tmp_path, "CommonForm", "Gamma", "CommonForm.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert ids(index) == {
        "metadata_object:Catalog:Alpha:manager",
        "metadata_object:Catalog:Alpha:object",
        "metadata_object:DataProcessor:Beta:manager",
        "metadata_object:DataProcessor:Beta:object",
    }
    assert index.filter(read_status="missing").total == 4
    for entry in index:
        assert "Form" not in entry.relative_path


def test_designer_layout_is_not_classified(tmp_path: Path) -> None:
    ext = tmp_path / "Catalog" / "Alpha" / "Ext"
    ext.mkdir(parents=True)
    (ext / "ObjectModule.bsl").write_bytes(DATA)
    (ext / "ManagerModule.bsl").write_bytes(DATA)
    index = scan_metadata_object_modules(tmp_path)
    assert index.total == 2
    assert index.filter(read_status="missing").total == 2


@pytest.mark.parametrize(
    "metadata_type",
    [
        "AccountingRegister",
        "BusinessProcessCommand",
        "CalculationRegister",
        "ChartOfAccounts",
        "ChartOfCalculationTypes",
        "CommonCommand",
        "CommonForm",
        "CommonModule",
        "Configuration",
        "ConfigurationExtension",
        "Constant",
        "ExternalDataProcessor",
        "ExternalReport",
        "FilterCriterion",
        "HTTPService",
        "Sequences",
        "SettingsStorage",
        "WebService",
    ],
)
def test_unproven_types_are_not_classified(
    tmp_path: Path, metadata_type: str
) -> None:
    put(tmp_path, metadata_type, "Alpha", f"{metadata_type}.obj.bsl")
    put(tmp_path, metadata_type, "Alpha", f"{metadata_type}.mgr.bsl")
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()


def test_record_set_obj_is_not_object(tmp_path: Path) -> None:
    register = "InformationRegister"
    put(tmp_path, register, "Alpha", f"{register}.obj.bsl")
    accumulation = "AccumulationRegister"
    put(tmp_path, accumulation, "Beta", f"{accumulation}.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert ids(index) == {
        "metadata_object:AccumulationRegister:Beta:manager",
        "metadata_object:InformationRegister:Alpha:manager",
    }
    assert index.filter(read_status="missing").total == 2
    assert index.filter(module_kind="object").total == 0
    assert index.filter(module_kind="record_set").total == 0


# --- правило missing -------------------------------------------------------


def test_inapplicable_kind_creates_no_missing(tmp_path: Path) -> None:
    for metadata_type in (
        "AccumulationRegister",
        "BusinessProcess",
        "ChartOfCharacteristicType",
        "DocumentJournal",
        "Enum",
        "InformationRegister",
        "Task",
    ):
        (tmp_path / metadata_type / "Alpha").mkdir(parents=True)
    index = scan_metadata_object_modules(tmp_path)
    assert ids(index) == {
        "metadata_object:AccumulationRegister:Alpha:manager",
        "metadata_object:BusinessProcess:Alpha:object",
        "metadata_object:ChartOfCharacteristicType:Alpha:object",
        "metadata_object:DocumentJournal:Alpha:manager",
        "metadata_object:Enum:Alpha:manager",
        "metadata_object:InformationRegister:Alpha:manager",
        "metadata_object:Task:Alpha:object",
    }
    for entry in index:
        assert entry.read_status == "missing"
        assert entry.size_bytes is None
        assert entry.sha256 is None


def test_missing_requires_existing_owner_dir(tmp_path: Path) -> None:
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()
    (tmp_path / "Catalog").mkdir()
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()
    (tmp_path / "Catalog" / "Readme.bsl").write_bytes(DATA)
    (tmp_path / "Document").write_bytes(DATA)
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()


def test_invalid_owner_dir_names_are_skipped(tmp_path: Path) -> None:
    for owner in ("1Alpha", "Alpha.bak", "Al-pha"):
        put(tmp_path, "Catalog", owner, "Catalog.obj.bsl")
    put(tmp_path, "Catalog", "Товары", "Catalog.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert {entry.owner_name for entry in index} == {"Товары"}


def test_case_insensitive_duplicate_owner_is_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / "Catalog" / "Alpha").mkdir(parents=True)
    (tmp_path / "Catalog" / "ALPHA").mkdir(exist_ok=True)
    if len(list((tmp_path / "Catalog").iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    with pytest.raises(ValueError, match="duplicate module_id"):
        scan_metadata_object_modules(tmp_path)


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    put(tmp_path, "Catalog", "Alpha", "Catalog.obj.bsl", b"")
    put(tmp_path, "Catalog", "Alpha", "Catalog.mgr.bsl", b" \r\n\t")
    put(tmp_path, "Document", "Beta", "Document.obj.bsl", b"\xff\xfe\xfa")
    put(tmp_path, "Enum", "Gamma", "Enum.obj.bsl", b"\xef\xbb\xbf")
    index = scan_metadata_object_modules(tmp_path)

    def status(module_id: str) -> str:
        entry = index.get(module_id)
        assert entry is not None
        return entry.read_status

    assert status("metadata_object:Catalog:Alpha:object") == "empty"
    assert status("metadata_object:Catalog:Alpha:manager") == (
        "whitespace_only"
    )
    assert status("metadata_object:Document:Beta:object") == "read_error"
    assert status("metadata_object:Enum:Gamma:manager") == "empty"
    missing = index.get("metadata_object:Document:Beta:manager")
    assert missing is not None
    assert missing.read_status == "missing"
    assert missing.relative_path == "Document/Beta/Document.mgr.bsl"
    assert missing.size_bytes is None
    assert missing.sha256 is None
    broken = index.get("metadata_object:Document:Beta:object")
    assert broken is not None
    assert broken.size_bytes == 3


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put(tmp_path, "Report", "Alpha", "Report.obj.bsl")
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_metadata_object_modules(tmp_path).get(
        "metadata_object:Report:Alpha:object"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    (tmp_path / "Task" / "Alpha" / "Task.obj.bsl").mkdir(parents=True)
    entry = scan_metadata_object_modules(tmp_path).get(
        "metadata_object:Task:Alpha:object"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None


def test_symlink_module_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "Catalog" / "Alpha").mkdir(parents=True)
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    try:
        os.symlink(outside, root / "Catalog" / "Alpha" / "Catalog.obj.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_metadata_object_modules(root).get(
        "metadata_object:Catalog:Alpha:object"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "Catalog").mkdir(parents=True)
    outside_owner = tmp_path / "outside_owner"
    outside_owner.mkdir()
    (outside_owner / "Catalog.obj.bsl").write_bytes(DATA)
    outside_type = tmp_path / "outside_type"
    (outside_type / "Beta").mkdir(parents=True)
    (outside_type / "Beta" / "Document.obj.bsl").write_bytes(DATA)
    try:
        os.symlink(
            outside_owner,
            root / "Catalog" / "Alpha",
            target_is_directory=True,
        )
        os.symlink(outside_type, root / "Document", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    assert scan_metadata_object_modules(root) == ModuleIndex()


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_metadata_object_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_metadata_object_modules(file_root)


# --- детерминированность, read-only, сериализация --------------------------


def test_traversal_order_does_not_change_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for owner in ("Zulu", "alpha", "Beta", "Товары"):
        populate(tmp_path, owner)
    baseline = scan_metadata_object_modules(tmp_path)
    original = Path.iterdir

    def reversed_iterdir(self: Path) -> Iterator[Path]:
        return iter(sorted(original(self), reverse=True))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reordered = scan_metadata_object_modules(tmp_path)
    assert reordered == baseline
    assert reordered.to_json() == baseline.to_json()
    assert baseline.total == 4 * 17


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    (tmp_path / "Catalog" / "Alpha" / "Catalog.mgr.bsl").unlink()
    (tmp_path / "Enum" / "Beta").mkdir()
    before = snapshot(tmp_path)
    first = scan_metadata_object_modules(tmp_path)
    second = scan_metadata_object_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    index = scan_metadata_object_modules(tmp_path)
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


def test_scanner_does_not_import_form_pipeline() -> None:
    source = Path(metadata_modules.__file__).read_text(encoding="utf-8")
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


def test_combines_with_configuration_modules(tmp_path: Path) -> None:
    populate(tmp_path)
    (tmp_path / "Configuration.app.bsl").write_bytes(DATA)
    objects = scan_metadata_object_modules(tmp_path)
    config = scan_configuration_modules(tmp_path)
    assert objects.filter(owner_kind="configuration").total == 0
    combined = ModuleIndex.from_entries([*objects, *config])
    assert combined.total == objects.total + config.total
