"""Сканер модулей HTTP- и Web-сервисов (issue #337)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from v8unpack_agent import service_modules
from v8unpack_agent.command_modules import scan_command_modules
from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)
from v8unpack_agent.modules import ModuleIndex
from v8unpack_agent.record_set_modules import (
    RECORD_SET_MODULE_FILES,
    scan_record_set_modules,
)
from v8unpack_agent.service_modules import (
    SERVICE_MODULE_FILES,
    scan_service_modules,
)
from v8unpack_agent.value_manager_modules import scan_value_manager_modules

RESEARCH_JSON = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "bsl_module_inventory_issue202.json"
)
HTTP = "HTTPService"
WEB = "WebService"
TEXT = "Функция Обработчик(Запрос)\n\tВозврат Неопределено;\nКонецФункции\n"
DATA = TEXT.encode("utf-8")
MARKER = "МаркерТекстаКоторогоНетВИндексе"
BS = chr(92)
PROVEN = "designer_content_match_A"


def put(
    root: Path,
    metadata_type: str,
    owner: str,
    data: bytes = DATA,
    *,
    name: str | None = None,
) -> Path:
    directory = root / metadata_type / owner
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"{metadata_type}.obj.bsl")
    path.write_bytes(data)
    return path


def populate(root: Path, owner: str = "Alpha", data: bytes = DATA) -> None:
    for metadata_type in SERVICE_MODULE_FILES:
        put(root, metadata_type, owner, data)


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


def test_files_match_issue202_proven_service_rows() -> None:
    proven = {
        row["metadata_type"]: (row["owner_kind"], row["path_pattern"])
        for row in research_rows()
        if row["module_kind"] == "service"
    }
    actual = {
        metadata_type: (
            "metadata_object",
            f"{metadata_type}/{{Name}}/{file_name}",
        )
        for metadata_type, file_name in SERVICE_MODULE_FILES.items()
    }
    assert actual == proven
    assert sorted(proven) == [HTTP, WEB]
    evidence = {
        row["semantic_evidence"]
        for row in research_rows()
        if row["module_kind"] == "service"
    }
    assert evidence == {PROVEN}


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        SERVICE_MODULE_FILES["Catalog"] = "x.bsl"  # type: ignore[index]


def test_neighbour_scanners_do_not_claim_services() -> None:
    for metadata_type in SERVICE_MODULE_FILES:
        assert metadata_type not in METADATA_OBJECT_MODULE_LAYOUTS
        assert metadata_type not in RECORD_SET_MODULE_FILES
    for layout in METADATA_OBJECT_MODULE_LAYOUTS.values():
        assert "service" not in layout


# --- позитивные fixtures ---------------------------------------------------


@pytest.mark.parametrize("metadata_type", sorted(SERVICE_MODULE_FILES))
def test_each_proven_service_type_is_detected(
    tmp_path: Path, metadata_type: str
) -> None:
    file_name = SERVICE_MODULE_FILES[metadata_type]
    put(tmp_path, metadata_type, "Alpha")
    index = scan_service_modules(tmp_path)
    assert index.total == 1
    entry = next(iter(index))
    assert entry.module_kind == "service"
    assert entry.owner_kind == "metadata_object"
    assert entry.metadata_type == metadata_type
    assert entry.owner_name == "Alpha"
    assert entry.read_status == "ok"
    assert entry.relative_path == f"{metadata_type}/Alpha/{file_name}"
    assert entry.module_id == f"metadata_object:{metadata_type}:Alpha:service"
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_http_and_web_are_distinguishable_in_index(tmp_path: Path) -> None:
    put(tmp_path, HTTP, "Api", b"// http\n")
    put(tmp_path, WEB, "Exchange", b"// web\n")
    index = scan_service_modules(tmp_path)
    assert index.total == 2
    assert index.filter(module_kind="service").total == 2
    http = index.filter(metadata_type=HTTP)
    web = index.filter(metadata_type=WEB)
    assert http.total == 1 and web.total == 1
    assert ids(http) == {"metadata_object:HTTPService:Api:service"}
    assert ids(web) == {"metadata_object:WebService:Exchange:service"}
    data = json.loads(index.to_json())
    assert {item["metadata_type"] for item in data["entries"]} == {HTTP, WEB}


def test_same_service_name_in_both_types_does_not_conflict(
    tmp_path: Path,
) -> None:
    put(tmp_path, HTTP, "Alpha", b"// http\n")
    put(tmp_path, WEB, "Alpha", b"// web\n")
    index = scan_service_modules(tmp_path)
    assert ids(index) == {
        "metadata_object:HTTPService:Alpha:service",
        "metadata_object:WebService:Alpha:service",
    }
    assert len({entry.sha256 for entry in index}) == 2
    assert len({entry.relative_path for entry in index}) == 2


def test_service_is_not_object_manager_or_form(tmp_path: Path) -> None:
    populate(tmp_path)
    index = scan_service_modules(tmp_path)
    assert index.filter(module_kind="object").total == 0
    assert index.filter(module_kind="manager").total == 0
    assert index.filter(module_kind="form").total == 0
    assert index.filter(module_kind="value_manager").total == 0
    assert scan_metadata_object_modules(tmp_path) == ModuleIndex()
    assert scan_value_manager_modules(tmp_path) == ModuleIndex()


# --- неприменимые владельцы и иные layout ----------------------------------


def test_nested_dirs_and_designer_layout_are_not_scanned(
    tmp_path: Path,
) -> None:
    owner = tmp_path / HTTP / "Alpha"
    ext = owner / "Ext"
    ext.mkdir(parents=True)
    (ext / "Module.bsl").write_bytes(DATA)
    template = owner / "URLTemplate" / "Root"
    template.mkdir(parents=True)
    (template / "URLTemplate.obj.bsl").write_bytes(DATA)
    (owner / "HTTPService.mgr.bsl").write_bytes(DATA)
    designer = tmp_path / "WebServices" / "Beta" / "Ext"
    designer.mkdir(parents=True)
    (designer / "Module.bsl").write_bytes(DATA)
    index = scan_service_modules(tmp_path)
    assert ids(index) == {"metadata_object:HTTPService:Alpha:service"}
    entry = next(iter(index))
    assert entry.read_status == "missing"
    assert entry.relative_path == "HTTPService/Alpha/HTTPService.obj.bsl"


@pytest.mark.parametrize(
    "metadata_type",
    [
        "AccumulationRegister",
        "Catalog",
        "CommonModule",
        "Constant",
        "DataProcessor",
        "Document",
        "Enum",
        "ExchangePlan",
        "HttpService",
        "HTTPServices",
        "InformationRegister",
        "Report",
        "Task",
        "WebServices",
        "WSReference",
    ],
)
def test_unproven_owner_types_give_no_entries(
    tmp_path: Path, metadata_type: str
) -> None:
    put(tmp_path, metadata_type, "Alpha")
    (tmp_path / metadata_type / "Beta").mkdir()
    index = scan_service_modules(tmp_path)
    if metadata_type == "HttpService" and (tmp_path / HTTP).exists():
        pytest.skip("case-insensitive file system")
    assert index == ModuleIndex()


def test_wrong_file_name_in_service_dir_is_missing(tmp_path: Path) -> None:
    put(tmp_path, WEB, "Alpha", name="HTTPService.obj.bsl")
    put(tmp_path, WEB, "Alpha", name="WebService.obj.json")
    index = scan_service_modules(tmp_path)
    entry = index.get("metadata_object:WebService:Alpha:service")
    assert entry is not None
    assert entry.read_status == "missing"
    assert entry.size_bytes is None


# --- правило missing -------------------------------------------------------


def test_missing_requires_existing_owner_dir(tmp_path: Path) -> None:
    assert scan_service_modules(tmp_path) == ModuleIndex()
    (tmp_path / HTTP).mkdir()
    (tmp_path / WEB).mkdir()
    assert scan_service_modules(tmp_path) == ModuleIndex()
    (tmp_path / HTTP / "Readme.bsl").write_bytes(DATA)
    (tmp_path / WEB / "WebService.obj.bsl").write_bytes(DATA)
    assert scan_service_modules(tmp_path) == ModuleIndex()


def test_existing_owner_without_file_is_missing_not_error(
    tmp_path: Path,
) -> None:
    for metadata_type in SERVICE_MODULE_FILES:
        (tmp_path / metadata_type / "Alpha").mkdir(parents=True)
    index = scan_service_modules(tmp_path)
    assert index.total == 2
    for entry in index:
        assert entry.read_status == "missing"
        assert entry.module_kind == "service"
        assert entry.size_bytes is None
        assert entry.sha256 is None
    assert index.filter(read_status="read_error").total == 0


def test_invalid_owner_dir_names_are_skipped(tmp_path: Path) -> None:
    for owner in ("1Alpha", "Alpha.bak", "Al-pha", "Alpha Beta"):
        put(tmp_path, HTTP, owner)
    (tmp_path / HTTP / "NUL").mkdir()
    put(tmp_path, HTTP, "ОбменДанными")
    index = scan_service_modules(tmp_path)
    assert {entry.owner_name for entry in index} == {"ОбменДанными"}


def test_case_insensitive_duplicate_owner_is_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / WEB / "Alpha").mkdir(parents=True)
    (tmp_path / WEB / "ALPHA").mkdir(exist_ok=True)
    if len(list((tmp_path / WEB).iterdir())) < 2:
        pytest.skip("case-insensitive file system")
    with pytest.raises(ValueError, match="duplicate module_id"):
        scan_service_modules(tmp_path)


# --- статусы чтения --------------------------------------------------------


def test_statuses_are_distinct(tmp_path: Path) -> None:
    put(tmp_path, HTTP, "Empty", b"")
    put(tmp_path, HTTP, "Bom", b"\xef\xbb\xbf")
    put(tmp_path, HTTP, "Spaces", b" \r\n\t")
    put(tmp_path, WEB, "Broken", b"\xff\xfe\xfa")
    put(tmp_path, WEB, "Text")
    (tmp_path / WEB / "Absent").mkdir()
    index = scan_service_modules(tmp_path)

    def entry_of(metadata_type: str, owner: str) -> tuple[str, int | None]:
        entry = index.get(f"metadata_object:{metadata_type}:{owner}:service")
        assert entry is not None
        return entry.read_status, entry.size_bytes

    assert entry_of(HTTP, "Empty") == ("empty", 0)
    assert entry_of(HTTP, "Bom") == ("empty", 3)
    assert entry_of(HTTP, "Spaces") == ("whitespace_only", 4)
    assert entry_of(WEB, "Broken") == ("read_error", 3)
    assert entry_of(WEB, "Text") == ("ok", len(DATA))
    assert entry_of(WEB, "Absent") == ("missing", None)
    assert index.total == 6


def test_os_error_is_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put(tmp_path, WEB, "Alpha")
    original = Path.read_bytes

    def fail(candidate: Path) -> bytes:
        if candidate == target:
            raise PermissionError("synthetic")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail)
    entry = scan_service_modules(tmp_path).get(
        "metadata_object:WebService:Alpha:service"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    (tmp_path / HTTP / "Alpha" / "HTTPService.obj.bsl").mkdir(parents=True)
    entry = scan_service_modules(tmp_path).get(
        "metadata_object:HTTPService:Alpha:service"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None


def test_symlink_module_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    owner = root / HTTP / "Alpha"
    owner.mkdir(parents=True)
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    try:
        os.symlink(outside, owner / "HTTPService.obj.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = scan_service_modules(root).get(
        "metadata_object:HTTPService:Alpha:service"
    )
    assert entry is not None
    assert entry.read_status == "read_error"
    assert entry.size_bytes is None
    assert entry.sha256 is None


def test_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / WEB).mkdir(parents=True)
    outside_owner = tmp_path / "outside_owner"
    outside_owner.mkdir()
    (outside_owner / "WebService.obj.bsl").write_bytes(DATA)
    outside_type = tmp_path / "outside_type"
    (outside_type / "Beta").mkdir(parents=True)
    (outside_type / "Beta" / "HTTPService.obj.bsl").write_bytes(DATA)
    try:
        os.symlink(
            outside_owner, root / WEB / "Alpha", target_is_directory=True
        )
        os.symlink(outside_type, root / HTTP, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    assert scan_service_modules(root) == ModuleIndex()


def test_invalid_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_service_modules(tmp_path / "absent")
    file_root = tmp_path / "file"
    file_root.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_service_modules(file_root)


# --- детерминированность, read-only, сериализация --------------------------


def test_traversal_order_does_not_change_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for owner in ("Zulu", "alpha", "Beta", "Обмен"):
        populate(tmp_path, owner)
    (tmp_path / HTTP / "Gamma").mkdir()
    baseline = scan_service_modules(tmp_path)
    original = Path.iterdir

    def reversed_iterdir(self: Path) -> Iterator[Path]:
        return iter(sorted(original(self), reverse=True))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reordered = scan_service_modules(tmp_path)
    assert reordered == baseline
    assert reordered.to_json() == baseline.to_json()
    assert baseline.total == 4 * 2 + 1


def test_repeated_scan_is_identical_and_read_only(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    (tmp_path / WEB / "Beta").mkdir()
    put(tmp_path, HTTP, "Broken", b"\xff")
    before = snapshot(tmp_path)
    first = scan_service_modules(tmp_path)
    second = scan_service_modules(tmp_path)
    assert first == second
    assert first.to_json() == second.to_json()
    assert snapshot(tmp_path) == before


def test_json_has_no_bsl_text_and_relative_paths(tmp_path: Path) -> None:
    populate(tmp_path, data=DATA + ("// " + MARKER + "\n").encode())
    index = scan_service_modules(tmp_path)
    payload = index.to_json()
    assert MARKER not in payload
    assert "Функция" not in payload
    assert BS not in payload
    assert str(tmp_path) not in payload
    data = json.loads(payload)
    assert data["schema"] == "module_index/1"
    for item in data["entries"]:
        value = item["relative_path"]
        assert not PurePosixPath(value).is_absolute()
        assert not PureWindowsPath(value).drive
        assert not PureWindowsPath(value).root
        assert item["module_kind"] == "service"
        assert item["metadata_type"] in (HTTP, WEB)
    assert ModuleIndex.from_dict(data) == index


# --- совместимость ---------------------------------------------------------


def test_scanner_imports_only_module_contract() -> None:
    source = Path(service_modules.__file__).read_text(encoding="utf-8")
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


def test_combines_with_other_scanners(tmp_path: Path) -> None:
    populate(tmp_path)
    put(tmp_path, "Constant", "Alpha")
    put(tmp_path, "Catalog", "Alpha")
    put(tmp_path, "Catalog", "Alpha", name="Catalog.mgr.bsl")
    put(tmp_path, "InformationRegister", "Alpha")
    command = tmp_path / "CommonCommand" / "Alpha"
    command.mkdir(parents=True)
    (command / "CommonCommand.obj.bsl").write_bytes(DATA)
    services = scan_service_modules(tmp_path)
    value_managers = scan_value_manager_modules(tmp_path)
    objects = scan_metadata_object_modules(tmp_path)
    record_sets = scan_record_set_modules(tmp_path)
    commands = scan_command_modules(tmp_path)
    for other in (value_managers, objects, record_sets, commands):
        assert other.filter(module_kind="service").total == 0
    combined = ModuleIndex.from_entries(
        [*services, *value_managers, *objects, *record_sets, *commands]
    )
    assert combined.total == (
        services.total
        + value_managers.total
        + objects.total
        + record_sets.total
        + commands.total
    )
    assert combined.filter(module_kind="service").total == 2
    assert combined.filter(module_kind="value_manager").total == 1
    assert combined.filter(metadata_type=HTTP).total == 1
    assert combined.filter(metadata_type=WEB).total == 1
