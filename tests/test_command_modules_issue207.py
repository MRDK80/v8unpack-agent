"""Сканер модулей команд (issue #207)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from v8unpack_agent import command_modules
from v8unpack_agent.command_modules import (
    COMMON_COMMAND_MODULE_FILE,
    COMMON_COMMAND_TYPE,
    OBJECT_COMMAND_CONTAINERS,
    scan_command_modules,
)
from v8unpack_agent.configuration_modules import scan_configuration_modules
from v8unpack_agent.metadata_modules import scan_metadata_object_modules
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.record_set_modules import scan_record_set_modules

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
TEXT = "Процедура ОбработкаКоманды(Параметр)\nКонецПроцедуры\n"
DATA = TEXT.encode("utf-8")
MARKER = "МаркерТекстаКоторогоНетВИндексе"
BS = chr(92)
PROVEN = "designer_content_match_A"


def common_dir(root: Path, command: str) -> Path:
    directory = root / COMMON_COMMAND_TYPE / command
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def put_common(root: Path, command: str, data: bytes = DATA) -> Path:
    path = common_dir(root, command) / COMMON_COMMAND_MODULE_FILE
    path.write_bytes(data)
    return path


def object_command_dir(
    root: Path, metadata_type: str, owner: str, command: str
) -> Path:
    container = OBJECT_COMMAND_CONTAINERS[metadata_type]
    directory = root / metadata_type / owner / container / command
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def put_object(
    root: Path,
    metadata_type: str,
    owner: str,
    command: str,
    data: bytes = DATA,
) -> Path:
    container = OBJECT_COMMAND_CONTAINERS[metadata_type]
    directory = object_command_dir(root, metadata_type, owner, command)
    path = directory / f"{container}.obj.bsl"
    path.write_bytes(data)
    return path


def populate(root: Path, owner: str = "Alpha", data: bytes = DATA) -> None:
    put_common(root, owner, data)
    for metadata_type in OBJECT_COMMAND_CONTAINERS:
        put_object(root, metadata_type, owner, "Run", data)


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


def test_layout_matches_issue202_proven_command_rows() -> None:
    proven = {
        row["path_pattern"]: (row["owner_kind"], row["metadata_type"])
        for row in research_rows()
        if row["module_kind"] == "command"
        and row["semantic_evidence"] == PROVEN
    }
    actual = {
        f"{COMMON_COMMAND_TYPE}/{{Name}}/{COMMON_COMMAND_MODULE_FILE}": (
            "common_command",
            COMMON_COMMAND_TYPE,
        )
    }
    for metadata_type, container in OBJECT_COMMAND_CONTAINERS.items():
        pattern = (
            f"{metadata_type}/{{Name}}/{container}/{{Name}}/"
            f"{container}.obj.bsl"
        )
        actual[pattern] = ("metadata_object_command", metadata_type)
    assert actual == proven
    assert len(proven) == 6


def test_all_command_rows_in_research_are_proven() -> None:
    evidence = {
        row["semantic_evidence"]
        for row in research_rows()
        if row["module_kind"] == "command"
    }
    assert evidence == {PROVEN}
    owner_kinds = {
        row["owner_kind"]
        for row in research_rows()
        if row["module_kind"] == "command"
    }
    assert owner_kinds == {"common_command", "metadata_object_command"}


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        OBJECT_COMMAND_CONTAINERS["Task"] = "x"  # type: ignore[index]
    assert all(
        container == f"{metadata_type}Command"
        for metadata_type, container in OBJECT_COMMAND_CONTAINERS.items()
    )


# --- позитивные fixtures ---------------------------------------------------


def test_common_command_is_detected(tmp_path: Path) -> None:
    put_common(tmp_path, "Alpha")
    index = scan_command_modules(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    assert entry.module_kind == "command"
    assert entry.owner_kind == "common_command"
    assert entry.metadata_type == "CommonCommand"
    assert entry.owner_name == "Alpha"
    assert entry.relative_path == "CommonCommand/Alpha/CommonCommand.obj.bsl"
    assert entry.module_id == "common_command:CommonCommand:Alpha:command"
    assert entry.read_status == "ok"
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


@pytest.mark.parametrize("metadata_type", sorted(OBJECT_COMMAND_CONTAINERS))
def test_each_proven_object_command_class_is_detected(
    tmp_path: Path, metadata_type: str
) -> None:
    put_object(tmp_path, metadata_type, "Alpha", "Run")
    index = scan_command_modules(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    container = OBJECT_COMMAND_CONTAINERS[metadata_type]
    assert entry.module_kind == "command"
    assert entry.owner_kind == "metadata_object_command"
    assert entry.metadata_type == metadata_type
    assert entry.owner_name == "Alpha.Run"
    assert entry.relative_path == (
        f"{metadata_type}/Alpha/{container}/Run/{container}.obj.bsl"
    )
    assert entry.module_id == (
        f"metadata_object_command:{metadata_type}:Alpha.Run:command"
    )
    assert entry.read_status == "ok"
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_all_proven_classes_together(tmp_path: Path) -> None:
    populate(tmp_path)
    index = scan_command_modules(tmp_path)
    assert index.total == 6
    assert index.filter(module_kind="command").total == 6
    assert index.filter(owner_kind="common_command").total == 1
    assert index.filter(owner_kind="metadata_object_command").total == 5
    assert index.filter(owner_kind="configuration").total == 0
    assert index.filter(owner_kind="metadata_object").total == 0
    assert index.filter(read_status="ok").total == 6


def test_owner_names_with_cyrillic_identifiers(tmp_path: Path) -> None:
    put_common(tmp_path, "ОткрытьЖурнал")
    put_object(tmp_path, "Catalog", "Товары", "Печать")
    index = scan_command_modules(tmp_path)
    assert ids(index) == {
        "common_command:CommonCommand:ОткрытьЖурнал:command",
        "metadata_object_command:Catalog:Товары.Печать:command",
    }


# --- идентичность одноимённых команд -----------------------------------------


def test_same_command_name_under_different_owners_does_not_conflict(
    tmp_path: Path,
) -> None:
    put_object(tmp_path, "Catalog", "Alpha", "Run", b"// a\n")
    put_object(tmp_path, "Catalog", "Beta", "Run", b"// b\n")
    put_object(tmp_path, "Document", "Alpha", "Run", b"// c\n")
    put_common(tmp_path, "Run", b"// d\n")
    index = scan_command_modules(tmp_path)
    assert index.total == 4
    assert ids(index) == {
        "metadata_object_command:Catalog:Alpha.Run:command",
        "metadata_object_command:Catalog:Beta.Run:command",
        "metadata_object_command:Document:Alpha.Run:command",
        "common_command:CommonCommand:Run:command",
    }
    assert len({entry.sha256 for entry in index}) == 4


def test_identity_does_not_collide_with_other_module_kinds(
    tmp_path: Path,
) -> None:
    put_object(tmp_path, "Catalog", "Alpha", "Run")
    (tmp_path / "Catalog" / "Alpha" / "Catalog.obj.bsl").write_bytes(DATA)
    commands = scan_command_modules(tmp_path)
    objects = scan_metadata_object_modules(tmp_path)
    combined = ModuleIndex.from_entries([*commands, *objects])
    assert combined.get("metadata_object:Catalog:Alpha:object") is not None
    assert combined.get(
        "metadata_object_command:Catalog:Alpha.Run:command"
    ) is not None
    assert combined.filter(module_kind="command").total == 1


def test_case_insensitive_duplicate_command_is_rejected(
    tmp_path: Path,
) -> None:
    object_command_dir(tmp_path, "Catalog", "Alpha", "Run")
    object_command_dir(tmp_path, "Catalog", "Alpha", "RUN")
    container = tmp_path / "Catalog" / "Alpha" / "CatalogCommand"
    if len(list(container.iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    with pytest.raises(ValueError, match="duplicate module_id"):
        scan_command_modules(tmp_path)


# --- отрицательные случаи ----------------------------------------------------


def test_other_bsl_kinds_are_not_commands(tmp_path: Path) -> None:
    (tmp_path / "Configuration.app.bsl").write_bytes(DATA)
    owner = tmp_path / "Catalog" / "Alpha"
    owner.mkdir(parents=True)
    (owner / "Catalog.obj.bsl").write_bytes(DATA)
    (owner / "Catalog.mgr.bsl").write_bytes(DATA)
    form = owner / "CatalogForm" / "ItemForm"
    form.mkdir(parents=True)
    (form / "CatalogForm.obj.bsl").write_bytes(DATA)
    common_form = tmp_path / "CommonForm" / "Alpha"
    common_form.mkdir(parents=True)
    (common_form / "CommonForm.obj.bsl").write_bytes(DATA)
    common_module = tmp_path / "CommonModule" / "Alpha"
    common_module.mkdir(parents=True)
    (common_module / "CommonModule.obj.bsl").write_bytes(DATA)
    register = tmp_path / "InformationRegister" / "Alpha"
    register.mkdir(parents=True)
    (register / "InformationRegister.obj.bsl").write_bytes(DATA)
    assert scan_command_modules(tmp_path) == ModuleIndex()


@pytest.mark.parametrize(
    "metadata_type",
    [
        "AccumulationRegister",
        "BusinessProcess",
        "ChartOfAccounts",
        "ChartOfCharacteristicType",
        "Constant",
        "DocumentJournal",
        "Enum",
        "ExchangePlan",
        "ExternalDataProcessor",
        "ExternalReport",
        "FilterCriterion",
        "Task",
    ],
)
def test_unproven_owner_types_give_no_entries(
    tmp_path: Path, metadata_type: str
) -> None:
    directory = (
        tmp_path / metadata_type / "Alpha" / f"{metadata_type}Command" / "Run"
    )
    directory.mkdir(parents=True)
    (directory / f"{metadata_type}Command.obj.bsl").write_bytes(DATA)
    assert scan_command_modules(tmp_path) == ModuleIndex()


def test_foreign_container_name_is_not_classified(tmp_path: Path) -> None:
    wrong = tmp_path / "Catalog" / "Alpha" / "DocumentCommand" / "Run"
    wrong.mkdir(parents=True)
    (wrong / "DocumentCommand.obj.bsl").write_bytes(DATA)
    (wrong / "CatalogCommand.obj.bsl").write_bytes(DATA)
    assert scan_command_modules(tmp_path) == ModuleIndex()


def test_lowercase_container_name_is_not_classified(tmp_path: Path) -> None:
    lower = tmp_path / "Catalog" / "Beta" / "catalogcommand" / "Run"
    lower.mkdir(parents=True)
    (lower / "CatalogCommand.obj.bsl").write_bytes(DATA)
    if (tmp_path / "Catalog" / "Beta" / "CatalogCommand").exists():
        pytest.skip("case-insensitive file system")
    assert scan_command_modules(tmp_path) == ModuleIndex()


def test_wrong_file_name_in_command_dir_is_missing(tmp_path: Path) -> None:
    directory = object_command_dir(tmp_path, "Report", "Alpha", "Run")
    (directory / "Report.obj.bsl").write_bytes(DATA)
    (directory / "ReportCommand.mgr.bsl").write_bytes(DATA)
    (directory / "ReportCommand.obj.BSL").write_bytes(DATA)
    index = scan_command_modules(tmp_path)
    entry = index.get("metadata_object_command:Report:Alpha.Run:command")
    assert entry is not None
    if entry.read_status != "missing":
        pytest.skip("case-insensitive file system")
    assert entry.size_bytes is None


def test_designer_layout_is_not_classified(tmp_path: Path) -> None:
    ext = tmp_path / "Catalogs" / "Alpha" / "Commands" / "Run" / "Ext"
    ext.mkdir(parents=True)
    (ext / "CommandModule.bsl").write_bytes(DATA)
    common = tmp_path / "CommonCommands" / "Alpha" / "Ext"
    common.mkdir(parents=True)
    (common / "CommandModule.bsl").write_bytes(DATA)
    assert scan_command_modules(tmp_path) == ModuleIndex()


def test_files_and_invalid_names_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "CommonCommand").mkdir()
    (tmp_path / "CommonCommand" / "Readme.bsl").write_bytes(DATA)
    for name in ("1Alpha", "Alpha.bak", "Al-pha", "CON", "Alpha Beta"):
        (tmp_path / "CommonCommand" / name).mkdir()
    container = tmp_path / "Catalog" / "Alpha" / "CatalogCommand"
    container.mkdir(parents=True)
    (container / "Stray.bsl").write_bytes(DATA)
    for name in ("1Run", "Run.bak", "NUL"):
        (container / name).mkdir()
    (tmp_path / "Catalog" / "1Owner" / "CatalogCommand" / "Run").mkdir(
        parents=True
    )
    (tmp_path / "Document").write_bytes(DATA)
    assert scan_command_modules(tmp_path) == ModuleIndex()


# --- правило missing -------------------------------------------------------


def test_missing_requires_existing_command_dir(tmp_path: Path) -> None:
    assert scan_command_modules(tmp_path) == ModuleIndex()
    (tmp_path / "CommonCommand").mkdir()
    (tmp_path / "Catalog" / "Alpha").mkdir(parents=True)
    assert scan_command_modules(tmp_path) == ModuleIndex()
    (tmp_path / "Catalog" / "Alpha" / "CatalogCommand").mkdir()
    assert scan_command_modules(tmp_path) == ModuleIndex()


def test_existing_command_dir_without_bsl_is_missing_not_error(
    tmp_path: Path,
) -> None:
    common_dir(tmp_path, "Alpha")
    directory = object_command_dir(tmp_path, "Document", "Beta", "Post")
    (directory / "DocumentCommand.json").write_bytes(b"{}")
    index = scan_command_modules(tmp_path)
    assert index.total == 2
    for entry in index:
        assert entry.read_status == "missing"
        assert entry.size_bytes is None
        assert entry.sha256 is None
    assert index.filter(read_status="read_error").total == 0
    entry = index.get("metadata_object_command:Document:Beta.Post:command")
    assert entry is not None
    assert entry.relative_path == (
        "Document/Beta/DocumentCommand/Post/DocumentCommand.obj.bsl"
    )


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    put_common(tmp_path, "Empty", b"")
    put_common(tmp_path, "Bom", b"\xef\xbb\xbf")
    put_common(tmp_path, "Spaces", b" \r\n\t")
    put_object(tmp_path, "Catalog", "Alpha", "Broken", b"\xff\xfe\xfa")
    put_object(tmp_path, "Catalog", "Alpha", "Text")
    object_command_dir(tmp_path, "Catalog", "Alpha", "Absent")
    index = scan_command_modules(tmp_path)

    def entry_of(module_id: str) -> tuple[str, int | None]:
        entry = index.get(module_id)
        assert entry is not None
        return entry.read_status, entry.size_bytes

    common = "common_command:CommonCommand:{}:command"
    obj = "metadata_object_command:Catalog:Alpha.{}:command"
    assert entry_of(common.format("Empty")) == ("empty", 0)
    assert entry_of(common.format("Bom")) == ("empty", 3)
    assert entry_of(common.format("Spaces")) == ("whitespace_only", 4)
    assert entry_of(obj.format("Broken")) == ("read_error", 3)
    assert entry_of(obj.format("Text")) == ("ok", len(DATA))
    assert entry_of(obj.format("Absent")) == ("missing", None)
    assert index.total == 6


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put_object(tmp_path, "DataProcessor", "Alpha", "Run")
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_command_modules(tmp_path).get(
        "metadata_object_command:DataProcessor:Alpha.Run:command"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    (common_dir(tmp_path, "Alpha") / COMMON_COMMAND_MODULE_FILE).mkdir()
    entry = scan_command_modules(tmp_path).get(
        "common_command:CommonCommand:Alpha:command"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None


def test_symlink_module_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    directory = object_command_dir(root, "Catalog", "Alpha", "Run")
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    try:
        os.symlink(outside, directory / "CatalogCommand.obj.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_command_modules(root).get(
        "metadata_object_command:Catalog:Alpha.Run:command"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "Catalog" / "Alpha" / "CatalogCommand").mkdir(parents=True)
    (root / "Document" / "Beta").mkdir(parents=True)
    (root / "CommonCommand").mkdir()
    outside_command = tmp_path / "outside_command"
    outside_command.mkdir()
    (outside_command / "CatalogCommand.obj.bsl").write_bytes(DATA)
    (outside_command / "CommonCommand.obj.bsl").write_bytes(DATA)
    outside_container = tmp_path / "outside_container"
    (outside_container / "Run").mkdir(parents=True)
    (outside_container / "Run" / "DocumentCommand.obj.bsl").write_bytes(DATA)
    outside_type = tmp_path / "outside_type"
    (outside_type / "Alpha" / "ReportCommand" / "Run").mkdir(parents=True)
    (
        outside_type / "Alpha" / "ReportCommand" / "Run"
        / "ReportCommand.obj.bsl"
    ).write_bytes(DATA)
    try:
        os.symlink(
            outside_command,
            root / "Catalog" / "Alpha" / "CatalogCommand" / "Run",
            target_is_directory=True,
        )
        os.symlink(
            outside_command,
            root / "CommonCommand" / "Alpha",
            target_is_directory=True,
        )
        os.symlink(
            outside_container,
            root / "Document" / "Beta" / "DocumentCommand",
            target_is_directory=True,
        )
        os.symlink(outside_type, root / "Report", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    assert scan_command_modules(root) == ModuleIndex()


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_command_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_command_modules(file_root)


# --- детерминированность, read-only, сериализация --------------------------


def test_traversal_order_does_not_change_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for owner in ("Zulu", "alpha", "Beta", "Товары"):
        populate(tmp_path, owner)
        put_object(tmp_path, "Catalog", owner, "Second")
    object_command_dir(tmp_path, "Document", "Gamma", "Absent")
    baseline = scan_command_modules(tmp_path)
    original = Path.iterdir

    def reversed_iterdir(self: Path) -> Iterator[Path]:
        return iter(sorted(original(self), reverse=True))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reordered = scan_command_modules(tmp_path)
    assert reordered == baseline
    assert reordered.to_json() == baseline.to_json()
    assert baseline.total == 4 * 7 + 1


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    object_command_dir(tmp_path, "Report", "Beta", "Absent")
    put_common(tmp_path, "Broken", b"\xff")
    before = snapshot(tmp_path)
    first = scan_command_modules(tmp_path)
    second = scan_command_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    index = scan_command_modules(tmp_path)
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
        assert item["module_kind"] == "command"
    assert ModuleIndex.from_dict(data) == index


# --- совместимость ---------------------------------------------------------


def test_scanner_imports_only_module_contract() -> None:
    source = Path(command_modules.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    project = {name for name in imported if name.startswith("v8unpack_agent")}
    assert project == {"v8unpack_agent.modules"}


def test_combines_with_other_scanners(tmp_path: Path) -> None:
    populate(tmp_path)
    (tmp_path / "Configuration.app.bsl").write_bytes(DATA)
    (tmp_path / "Catalog" / "Alpha" / "Catalog.obj.bsl").write_bytes(DATA)
    (tmp_path / "Catalog" / "Alpha" / "Catalog.mgr.bsl").write_bytes(DATA)
    (
        tmp_path / "InformationRegister" / "Alpha"
        / "InformationRegister.obj.bsl"
    ).write_bytes(DATA)
    commands = scan_command_modules(tmp_path)
    config = scan_configuration_modules(tmp_path)
    objects = scan_metadata_object_modules(tmp_path)
    record_sets = scan_record_set_modules(tmp_path)
    assert config.filter(module_kind="command").total == 0
    assert objects.filter(module_kind="command").total == 0
    assert record_sets.filter(module_kind="command").total == 0
    combined = ModuleIndex.from_entries(
        [*commands, *config, *objects, *record_sets]
    )
    assert combined.total == (
        commands.total + config.total + objects.total + record_sets.total
    )
    assert combined.filter(module_kind="command").total == 6
