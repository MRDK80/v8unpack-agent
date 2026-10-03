"""Интеграция индекса BSL-модулей в runner и CLI (issue #208)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner
from v8unpack_agent.run_report import (
    RunObjectKind,
    RunObjectStatus,
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


def _options(root: Path, **extra: object) -> RunOptions:
    return RunOptions(
        export_root=root,
        include_common_modules=False,
        include_skd=False,
        **extra,  # type: ignore[arg-type]
    )


def _by_id(outcome: runner.RunOutcome) -> dict[str, object]:
    return {item.object: item for item in outcome.report.objects}


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
        ("empty", RunObjectStatus.PARTIAL, "empty"),
        ("whitespace_only", RunObjectStatus.PARTIAL, "whitespace_only"),
        ("missing", RunObjectStatus.FAILED, "missing"),
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
    assert not [o for o in outcome.report.objects if o.object_kind.value.startswith("module_")]


def test_enabled_reports_module_statuses(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path, include_module_index=True))
    assert outcome.completed
    assert outcome.degraded
    objs = _by_id(outcome)

    ok = objs["Configuration.802.bsl"]
    assert ok.object_kind is RunObjectKind.MODULE_ORDINARY_APPLICATION
    assert ok.status is RunObjectStatus.COMPLETE

    empty = objs["Configuration.app.bsl"]
    assert empty.object_kind is RunObjectKind.MODULE_MANAGED_APPLICATION
    assert (empty.status, empty.stage, empty.reason_code) == (RunObjectStatus.PARTIAL, "modules", "empty")

    constant = objs["Constant/Limit/Constant.obj.bsl"]
    assert constant.object_kind is RunObjectKind.MODULE_VALUE_MANAGER
    assert constant.status is RunObjectStatus.COMPLETE

    missing = objs["HTTPService/Api/HTTPService.obj.bsl"]
    assert missing.object_kind is RunObjectKind.MODULE_SERVICE
    assert (missing.status, missing.reason_code) == (RunObjectStatus.FAILED, "missing")

    blank = objs["WebService/Ws/WebService.obj.bsl"]
    assert (blank.status, blank.reason_code) == (RunObjectStatus.PARTIAL, "whitespace_only")


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


def test_deterministic(tmp_path: Path) -> None:
    _build_export(tmp_path)
    first = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()["objects"]
    second = run_pipeline(_options(tmp_path, include_module_index=True)).report.to_dict()["objects"]
    assert first == second


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
    assert not [o for o in outcome.report.objects if o.object_kind.value.startswith("module_")]


def test_cli_flag(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _build_export(export)
    report = tmp_path / "report.json"
    code = cli.main(
        [str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules", "--include-module-index"]
    )
    assert code == cli.EXIT_DEGRADED
    kinds = {o["object_kind"] for o in json.loads(report.read_text(encoding="utf-8"))["objects"]}
    assert "module_value_manager" in kinds


def test_cli_default_has_no_module_objects(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _build_export(export)
    report = tmp_path / "report.json"
    code = cli.main([str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules"])
    assert code == cli.EXIT_OK
    assert json.loads(report.read_text(encoding="utf-8"))["objects"] == []
