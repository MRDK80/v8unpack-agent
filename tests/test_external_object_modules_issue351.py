"""Сканер модулей объекта внешних обработок и отчётов (issue #351).

Данные синтетические: имена артефактов и объектов условные.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from v8unpack_agent.external_object_modules import (
    EXTERNAL_METADATA_FILE,
    EXTERNAL_OBJECT_MODULE_FILE,
    EXTERNAL_REPORT_CONTAINER,
    scan_external_object_modules,
)
from v8unpack_agent.metadata_modules import scan_metadata_object_modules
from v8unpack_agent.modules import ModuleEntry, ModuleIndex

TEXT = "Процедура Тест()\n\tВозврат;\nКонецПроцедуры"
DATA = TEXT.encode("utf-8")
BS = chr(92)


def artifact(
    root: Path,
    directory: str,
    name: str | None = "Обработка1",
    *,
    module: bytes | None = DATA,
    container: str | None = None,
    metadata: bytes | None = None,
) -> Path:
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    if metadata is not None:
        (path / EXTERNAL_METADATA_FILE).write_bytes(metadata)
    elif name is not None:
        payload = {"uuid": "u", "name": name, "name2": {"ru": "Синоним"}}
        (path / EXTERNAL_METADATA_FILE).write_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
    if module is not None:
        (path / EXTERNAL_OBJECT_MODULE_FILE).write_bytes(module)
    if container is not None:
        form = path / container / "Форма"
        form.mkdir(parents=True)
        (form / f"{container}.obj.bsl").write_bytes(b"// form\n")
    return path


def only(index: ModuleIndex) -> ModuleEntry:
    assert index.total == 1
    return next(iter(index))


# --- позитивные случаи -----------------------------------------------------


def test_data_processor_object_module(tmp_path: Path) -> None:
    artifact(tmp_path, "ext__Обработка 1 (копия).epf", container="Form")
    entry = only(scan_external_object_modules(tmp_path))
    assert entry.module_kind == "object"
    assert entry.owner_kind == "external_data_processor"
    assert entry.metadata_type == "ExternalDataProcessor"
    assert entry.owner_name == "Обработка1"
    assert entry.read_status == "ok"
    assert entry.relative_path == (
        "ext__Обработка 1 (копия).epf/ExternalDataProcessor.obj.bsl"
    )
    assert entry.module_id == (
        "external_data_processor:ExternalDataProcessor:Обработка1:object"
    )
    assert entry.size_bytes == len(DATA)
    assert entry.sha256 == hashlib.sha256(DATA).hexdigest()


def test_report_is_detected_by_report_form_container(tmp_path: Path) -> None:
    artifact(tmp_path, "Отчёт", "Отчет1", container=EXTERNAL_REPORT_CONTAINER)
    entry = only(scan_external_object_modules(tmp_path))
    assert entry.owner_kind == "external_report"
    assert entry.metadata_type == "ExternalReport"
    assert entry.relative_path == "Отчёт/ExternalDataProcessor.obj.bsl"
    assert entry.module_id == "external_report:ExternalReport:Отчет1:object"


def test_report_without_forms_is_detected_by_erf_suffix(tmp_path: Path) -> None:
    artifact(tmp_path, "Отчёт.erf", "Отчет1")
    assert only(scan_external_object_modules(tmp_path)).owner_kind == (
        "external_report"
    )


def test_processor_without_forms_is_detected_by_epf_suffix(
    tmp_path: Path,
) -> None:
    artifact(tmp_path, "Обработка.EPF", "Обработка1")
    assert only(scan_external_object_modules(tmp_path)).owner_kind == (
        "external_data_processor"
    )


def test_report_form_container_wins_over_epf_suffix(tmp_path: Path) -> None:
    artifact(tmp_path, "Смешанный.epf", "Отчет1", container="ReportForm")
    assert only(scan_external_object_modules(tmp_path)).owner_kind == (
        "external_report"
    )


def test_external_subdirectory_is_the_base(tmp_path: Path) -> None:
    artifact(tmp_path / "External", "Обработка.epf", "Обработка1")
    artifact(tmp_path, "Корневая.epf", "Обработка2")
    entry = only(scan_external_object_modules(tmp_path))
    assert entry.owner_name == "Обработка1"
    assert entry.relative_path == (
        "External/Обработка.epf/ExternalDataProcessor.obj.bsl"
    )


def test_processor_and_report_with_same_name_do_not_conflict(
    tmp_path: Path,
) -> None:
    artifact(tmp_path, "А.epf", "Общее")
    artifact(tmp_path, "Б.erf", "Общее")
    index = scan_external_object_modules(tmp_path)
    assert {e.module_id for e in index} == {
        "external_data_processor:ExternalDataProcessor:Общее:object",
        "external_report:ExternalReport:Общее:object",
    }


def test_module_text_is_not_normalized(tmp_path: Path) -> None:
    payload = b"\xef\xbb\xbf\t\xd0\x90\r\n|\"\"\r\n"
    artifact(tmp_path, "А.epf", module=payload)
    entry = only(scan_external_object_modules(tmp_path))
    assert entry.size_bytes == len(payload)
    assert entry.sha256 == hashlib.sha256(payload).hexdigest()


# --- граничные статусы -----------------------------------------------------


def test_missing_module_file_is_missing(tmp_path: Path) -> None:
    artifact(tmp_path, "А.erf", "Отчет1", module=None, container="ReportForm")
    entry = only(scan_external_object_modules(tmp_path))
    assert entry.read_status == "missing"
    assert entry.size_bytes is None and entry.sha256 is None


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (b"", "empty"),
        (b"\xef\xbb\xbf", "empty"),
        (b" \t\n", "whitespace_only"),
        (b"\xff\xfe\xfa", "read_error"),
    ],
)
def test_classified_statuses(tmp_path: Path, payload: bytes, status: str) -> None:
    artifact(tmp_path, "А.epf", module=payload)
    assert only(scan_external_object_modules(tmp_path)).read_status == status


def test_wrong_case_module_file_is_missing(tmp_path: Path) -> None:
    path = artifact(tmp_path, "А.epf", module=None)
    (path / "externaldataprocessor.obj.bsl").write_bytes(DATA)
    assert only(scan_external_object_modules(tmp_path)).read_status == "missing"


def test_directory_in_place_of_module_is_read_error(tmp_path: Path) -> None:
    path = artifact(tmp_path, "А.epf", module=None)
    (path / EXTERNAL_OBJECT_MODULE_FILE).mkdir()
    assert only(scan_external_object_modules(tmp_path)).read_status == (
        "read_error"
    )


def test_symlink_module_is_read_error(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(DATA)
    root = tmp_path / "root"
    path = artifact(root, "А.epf", module=None)
    try:
        os.symlink(outside, path / EXTERNAL_OBJECT_MODULE_FILE)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = only(scan_external_object_modules(root))
    assert entry.read_status == "read_error"
    assert entry.sha256 is None


# --- случаи без записей ----------------------------------------------------


def test_empty_root_gives_empty_index(tmp_path: Path) -> None:
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_external_object_modules(tmp_path / "nope")


def test_directory_without_metadata_gives_no_entry(tmp_path: Path) -> None:
    artifact(tmp_path, "А.epf", name=None)
    (tmp_path / "Пустой").mkdir()
    (tmp_path / "file.epf").write_bytes(DATA)
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


@pytest.mark.parametrize(
    "metadata",
    [
        b"{",
        b"[]",
        b"\xff\xfe",
        json.dumps({"uuid": "u"}).encode(),
        json.dumps({"name": 7}).encode(),
        json.dumps({"name": "Имя с пробелом"}, ensure_ascii=False).encode(),
        json.dumps({"name": "Имя.Точка"}, ensure_ascii=False).encode(),
        json.dumps({"name": ""}).encode(),
    ],
)
def test_unusable_metadata_gives_no_entry(tmp_path: Path, metadata: bytes) -> None:
    artifact(tmp_path, "А.epf", metadata=metadata)
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


def test_wrong_case_metadata_file_gives_no_entry(tmp_path: Path) -> None:
    path = artifact(tmp_path, "А.epf", name=None)
    (path / "externaldataprocessor.json").write_bytes(b'{"name": "Name"}')
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


def test_unknown_kind_gives_no_entry(tmp_path: Path) -> None:
    artifact(tmp_path, "Без суффикса", container="Form")
    artifact(tmp_path, "Другой.cfe", "Обработка2")
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


def test_ambiguous_owner_gives_no_entry(tmp_path: Path) -> None:
    artifact(tmp_path, "Версия1.epf", "Обработка1")
    artifact(tmp_path, "Версия2.epf", "обработка1")
    artifact(tmp_path, "Другая.epf", "Обработка2")
    index = scan_external_object_modules(tmp_path)
    assert {e.owner_name for e in index} == {"Обработка2"}


def test_invalid_path_segment_gives_no_entry(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("name is not creatable on Windows")
    artifact(tmp_path, "Имя.epf.", "Обработка1")
    assert scan_external_object_modules(tmp_path) == ModuleIndex()


# --- границы сканера -------------------------------------------------------


def test_form_modules_are_not_indexed(tmp_path: Path) -> None:
    artifact(tmp_path, "А.epf", container="Form")
    artifact(tmp_path, "Б.erf", "Отчет1", container="ReportForm")
    paths = {e.relative_path for e in scan_external_object_modules(tmp_path)}
    assert all(p.endswith("/" + EXTERNAL_OBJECT_MODULE_FILE) for p in paths)
    assert len(paths) == 2


def test_configuration_layout_is_not_claimed(tmp_path: Path) -> None:
    obj = tmp_path / "DataProcessor" / "Обработка1"
    obj.mkdir(parents=True)
    (obj / "DataProcessor.obj.bsl").write_bytes(DATA)
    (obj / "DataProcessor.json").write_bytes(b'{"name": "x"}')
    assert scan_external_object_modules(tmp_path) == ModuleIndex()
    artifact(tmp_path / "ext", "А.epf")
    assert scan_metadata_object_modules(tmp_path / "ext") == ModuleIndex()


def test_scanner_is_read_only(tmp_path: Path) -> None:
    artifact(tmp_path, "А.epf", container="Form")
    artifact(tmp_path, "Б.erf", "Отчет1", module=None, container="ReportForm")
    before = {
        p.relative_to(tmp_path).as_posix(): (p.lstat().st_size, p.lstat().st_mtime_ns)
        for p in sorted(tmp_path.rglob("*"))
    }
    first = scan_external_object_modules(tmp_path)
    second = scan_external_object_modules(tmp_path)
    after = {
        p.relative_to(tmp_path).as_posix(): (p.lstat().st_size, p.lstat().st_mtime_ns)
        for p in sorted(tmp_path.rglob("*"))
    }
    assert before == after
    assert first.to_json() == second.to_json()
    assert BS not in first.to_json()
    assert str(tmp_path) not in first.to_json()
