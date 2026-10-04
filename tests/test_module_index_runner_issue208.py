"""Интеграция индекса BSL-модулей в runner и CLI (issue #208)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner
from v8unpack_agent.modules import ModuleEntry
from v8unpack_agent.run_report import (
    ModuleStatusTable,
    RunObjectKind,
    RunObjectStatus,
    RunReportValidationError,
    module_object_kind,
    module_read_status,
)
from v8unpack_agent.runner import RunOptions, run_pipeline

MODULE_KINDS = (
    "command",
    "external_connection",
    "managed_application",
    "manager",
    "object",
    "ordinary_application",
    "record_set",
    "service",
    "session",
    "value_manager",
)

BROKEN_ID = "Constant/Broken/Constant.obj.bsl"


def _build_export(root: Path) -> None:
    (root / "Configuration.802.bsl").write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")
    (root / "Configuration.app.bsl").write_bytes(b"")
    constant = root / "Constant" / "Limit"
    constant.mkdir(parents=True)
    (constant / "Constant.obj.bsl").write_text("// модуль\n", encoding="utf-8")
    (root / "HTTPService" / "Api").mkdir(parents=True)
    web = root / "WebService" / "Ws"
    web.mkdir(parents=True)
    (web / "WebService.obj.bsl").write_text("  \n", encoding="utf-8")


def _add_broken_module(root: Path) -> None:
    broken = root / "Constant" / "Broken"
    broken.mkdir(parents=True)
    (broken / "Constant.obj.bsl").write_bytes(b"\xff\xfe\xfa")


def _options(root: Path, **extra: object) -> RunOptions:
    return RunOptions(
        export_root=root,
        include_common_modules=False,
        include_skd=False,
        **extra,  # type: ignore[arg-type]
    )


def _by_id(outcome: runner.RunOutcome) -> dict[str, object]:
    return {item.object: item for item in outcome.report.objects}


def _module_objects(outcome: runner.RunOutcome) -> list[object]:
    return [o for o in outcome.report.objects if o.object_kind.value.startswith("module_")]


def test_enum_has_ten_module_kinds() -> None:
    values = {kind.value for kind in RunObjectKind}
    assert {"form", "common_module", "skd_artifact"} <= values
    assert {f"module_{name}" for name in MODULE_KINDS} <= values
    assert len(values) == 13


@pytest.mark.parametrize("name", MODULE_KINDS)
def test_module_object_kind_mapping(name: str) -> None:
    assert module_object_kind(name).value == f"module_{name}"


@pytest.mark.parametrize("name", ["form", "common_module", "unknown"])
def test_module_object_kind_rejects_other(name: str) -> None:
    with pytest.raises(ValueError):
        module_object_kind(name)


@pytest.mark.parametrize(
    ("read_status", "status", "reason"),
    [
        ("ok", RunObjectStatus.COMPLETE, None),
        ("empty", RunObjectStatus.EXCLUDED, "empty"),
        ("whitespace_only", RunObjectStatus.EXCLUDED, "whitespace_only"),
        ("missing", RunObjectStatus.EXCLUDED, "missing"),
        ("read_error", RunObjectStatus.FAILED, "read_error"),
    ],
)
def test_module_read_status_mapping(read_status: str, status: RunObjectStatus, reason: str | None) -> None:
    assert module_read_status(read_status) == (status, reason)  # type: ignore[arg-type]


def test_module_read_status_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        module_read_status("bogus")  # type: ignore[arg-type]


def test_default_off_adds_no_module_objects(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path))
    assert outcome.completed
    assert not _module_objects(outcome)
    assert outcome.report.modules is None
    assert "modules" not in outcome.report.to_dict()["summary"]


def test_enabled_reports_module_statuses(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    assert outcome.completed
    assert not outcome.degraded
    objs = _by_id(outcome)

    ok = objs["Configuration.802.bsl"]
    assert ok.object_kind is RunObjectKind.MODULE_ORDINARY_APPLICATION
    assert ok.status is RunObjectStatus.COMPLETE

    empty = objs["Configuration.app.bsl"]
    assert empty.object_kind is RunObjectKind.MODULE_MANAGED_APPLICATION
    assert (empty.status, empty.stage, empty.reason_code) == (RunObjectStatus.EXCLUDED, "modules", "empty")

    constant = objs["Constant/Limit/Constant.obj.bsl"]
    assert constant.object_kind is RunObjectKind.MODULE_VALUE_MANAGER
    assert constant.status is RunObjectStatus.COMPLETE

    missing = objs["HTTPService/Api/HTTPService.obj.bsl"]
    assert missing.object_kind is RunObjectKind.MODULE_SERVICE
    assert (missing.status, missing.reason_code) == (RunObjectStatus.EXCLUDED, "missing")

    blank = objs["WebService/Ws/WebService.obj.bsl"]
    assert (blank.status, blank.reason_code) == (RunObjectStatus.EXCLUDED, "whitespace_only")


def test_read_error_degrades_run(tmp_path: Path) -> None:
    _build_export(tmp_path)
    _add_broken_module(tmp_path)
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    assert outcome.completed
    assert outcome.degraded
    broken = _by_id(outcome)[BROKEN_ID]
    assert broken.object_kind is RunObjectKind.MODULE_VALUE_MANAGER
    assert (broken.status, broken.stage, broken.reason_code) == (RunObjectStatus.FAILED, "modules", "read_error")
    assert outcome.report.summary.failed == 1


def test_no_common_module_or_form_kinds_from_module_index(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    kinds = {o.object_kind for o in outcome.report.objects}
    assert RunObjectKind.COMMON_MODULE not in kinds
    assert RunObjectKind.FORM not in kinds


def test_report_schema_unchanged(tmp_path: Path) -> None:
    _build_export(tmp_path)
    data = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()
    assert data["schema_version"] == 1
    assert set(data) == {"schema_version", "run", "summary", "objects", "fatal_error"}
    assert json.loads(json.dumps(data)) == data


def test_summary_modules_table(tmp_path: Path) -> None:
    _build_export(tmp_path)
    data = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()
    table = data["summary"]["modules"]
    assert set(table) == {"by_kind", "record_set_by_metadata_type", "note"}
    assert "missing" in table["note"]
    assert table["record_set_by_metadata_type"] == {}

    value_manager = table["by_kind"]["value_manager"]
    assert (value_manager["total"], value_manager["ok"]) == (1, 1)
    assert value_manager["owners_checked"] == 1

    service = table["by_kind"]["service"]
    assert service["total"] == 2
    assert (service["missing"], service["whitespace_only"], service["ok"]) == (1, 1, 0)
    assert service["owners_checked"] == 2


def test_summary_modules_counts_read_error(tmp_path: Path) -> None:
    _build_export(tmp_path)
    _add_broken_module(tmp_path)
    data = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()
    value_manager = data["summary"]["modules"]["by_kind"]["value_manager"]
    assert (value_manager["total"], value_manager["ok"], value_manager["read_error"]) == (2, 1, 1)


def test_module_table_must_match_objects(tmp_path: Path) -> None:
    _build_export(tmp_path)
    report = run_pipeline(_options(tmp_path, include_module_index=True)).report
    assert report.modules is not None
    with pytest.raises(RunReportValidationError):
        replace(report, modules=ModuleStatusTable.from_entries([]))


def test_deterministic(tmp_path: Path) -> None:
    _build_export(tmp_path)
    first = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()
    second = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()
    assert first["objects"] == second["objects"]
    assert first["summary"] == second["summary"]


def test_scanner_failure_is_atomic_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_export(tmp_path)

    def boom(_root: Path) -> list[object]:
        raise ValueError("duplicate module_id")

    monkeypatch.setattr(runner, "scan_service_modules", boom)
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    assert not outcome.completed
    fatal = outcome.report.fatal_error
    assert fatal is not None
    assert (fatal.reason_code, fatal.error_type) == ("modules_failed", "value_error")
    assert not _module_objects(outcome)
    assert outcome.report.modules is None


def test_duplicate_relative_path_is_atomic_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_export(tmp_path)
    clash = ModuleEntry(
        module_kind="session",
        owner_kind="configuration",
        metadata_type=None,
        owner_name=None,
        relative_path="Configuration.802.bsl",
        read_status="ok",
    )
    monkeypatch.setattr(runner, "scan_service_modules", lambda _root: [clash])
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    assert not outcome.completed
    fatal = outcome.report.fatal_error
    assert fatal is not None
    assert (fatal.reason_code, fatal.error_type) == ("modules_failed", "value_error")
    assert not _module_objects(outcome)
    assert outcome.report.modules is None


def test_cli_flag(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _build_export(export)
    report = tmp_path / "report.json"
    code = cli.main(
        [str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules", "--include-module-index"]
    )
    assert code == cli.EXIT_OK
    data = json.loads(report.read_text(encoding="utf-8"))
    kinds = {o["object_kind"] for o in data["objects"]}
    assert "module_value_manager" in kinds
    assert "modules" in data["summary"]


def test_cli_read_error_is_degraded(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _build_export(export)
    _add_broken_module(export)
    report = tmp_path / "report.json"
    code = cli.main(
        [str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules", "--include-module-index"]
    )
    assert code == cli.EXIT_DEGRADED


def test_cli_default_has_no_module_objects(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _build_export(export)
    report = tmp_path / "report.json"
    code = cli.main([str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules"])
    assert code == cli.EXIT_OK
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["objects"] == []
    assert "modules" not in data["summary"]
