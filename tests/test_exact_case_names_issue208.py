"""Строгий регистр имён в сканерах BSL-модулей (issue #208)."""

from __future__ import annotations

from pathlib import Path

import pytest

from v8unpack_agent._exact_names import exact_child, exact_child_or_none
from v8unpack_agent.command_modules import scan_command_modules
from v8unpack_agent.configuration_modules import scan_configuration_modules
from v8unpack_agent.metadata_modules import scan_metadata_object_modules
from v8unpack_agent.record_set_modules import scan_record_set_modules
from v8unpack_agent.service_modules import scan_service_modules
from v8unpack_agent.value_manager_modules import scan_value_manager_modules

BSL = "// module\n"


def _write(path: Path, text: str = BSL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _case_sensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "CaseProbe"
    probe.mkdir()
    return not (tmp_path / "caseprobe").exists()


def test_exact_child_matches_only_exact_name(tmp_path: Path) -> None:
    (tmp_path / "Catalog").mkdir()
    assert exact_child(tmp_path, "Catalog") == tmp_path / "Catalog"
    assert exact_child(tmp_path, "catalog") is None
    assert exact_child(tmp_path, "Absent") is None


def test_exact_child_raises_for_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        exact_child(tmp_path / "absent", "Catalog")
    assert exact_child_or_none(tmp_path / "absent", "Catalog") is None


def test_metadata_type_dir_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "catalog" / "Item" / "Catalog.obj.bsl")
    assert scan_metadata_object_modules(tmp_path).total == 0


def test_metadata_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "Catalog" / "Item" / "catalog.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert index.total == 2
    assert {entry.read_status for entry in index} == {"missing"}


def test_metadata_exact_case_still_reads(tmp_path: Path) -> None:
    _write(tmp_path / "Catalog" / "Item" / "Catalog.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    statuses = {entry.module_kind: entry.read_status for entry in index}
    assert statuses == {"object": "ok", "manager": "missing"}


def test_configuration_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "configuration.app.bsl")
    assert scan_configuration_modules(tmp_path).total == 0


def test_configuration_wrong_case_next_to_exact_file_is_missing(tmp_path: Path) -> None:
    _write(tmp_path / "Configuration.802.bsl")
    _write(tmp_path / "configuration.app.bsl")
    statuses = {entry.module_kind: entry.read_status for entry in scan_configuration_modules(tmp_path)}
    assert statuses["ordinary_application"] == "ok"
    assert statuses["managed_application"] == "missing"


def test_record_set_type_dir_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "informationregister" / "Reg" / "InformationRegister.obj.bsl")
    assert scan_record_set_modules(tmp_path).total == 0


def test_record_set_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "InformationRegister" / "Reg" / "informationregister.obj.bsl")
    index = scan_record_set_modules(tmp_path)
    assert [entry.read_status for entry in index] == ["missing"]


def test_service_type_dir_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "httpservice" / "Api" / "HTTPService.obj.bsl")
    assert scan_service_modules(tmp_path).total == 0


def test_service_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "WebService" / "Ws" / "webservice.obj.bsl")
    assert [entry.read_status for entry in scan_service_modules(tmp_path)] == ["missing"]


def test_value_manager_type_dir_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "constant" / "Limit" / "Constant.obj.bsl")
    assert scan_value_manager_modules(tmp_path).total == 0


def test_value_manager_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "Constant" / "Limit" / "constant.obj.bsl")
    assert [entry.read_status for entry in scan_value_manager_modules(tmp_path)] == ["missing"]


def test_common_command_type_dir_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "commoncommand" / "Cmd" / "CommonCommand.obj.bsl")
    assert scan_command_modules(tmp_path).total == 0


def test_common_command_file_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "CommonCommand" / "Cmd" / "commoncommand.obj.bsl")
    assert [entry.read_status for entry in scan_command_modules(tmp_path)] == ["missing"]


def test_object_command_container_case_is_strict(tmp_path: Path) -> None:
    _write(tmp_path / "Catalog" / "Item" / "catalogcommand" / "Cmd" / "CatalogCommand.obj.bsl")
    assert scan_command_modules(tmp_path).total == 0


def test_object_command_exact_case_still_reads(tmp_path: Path) -> None:
    _write(tmp_path / "Catalog" / "Item" / "CatalogCommand" / "Cmd" / "CatalogCommand.obj.bsl")
    assert [entry.read_status for entry in scan_command_modules(tmp_path)] == ["ok"]


def test_two_spellings_on_case_sensitive_fs_use_only_exact(tmp_path: Path) -> None:
    if not _case_sensitive(tmp_path):
        pytest.skip("filesystem is case-insensitive")
    _write(tmp_path / "Catalog" / "Item" / "Catalog.obj.bsl")
    _write(tmp_path / "catalog" / "Other" / "Catalog.obj.bsl")
    index = scan_metadata_object_modules(tmp_path)
    assert {entry.owner_name for entry in index} == {"Item"}
