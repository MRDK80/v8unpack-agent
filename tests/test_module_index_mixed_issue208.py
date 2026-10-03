"""Смешанная конфигурация и политика кодов возврата индекса модулей (issue #208).

Тесты дополняют ``test_module_index_runner_issue208.py``: выгрузка содержит
форму, общий модуль и модули индекса одновременно, чтобы проверить, что один
файл не учитывается дважды, а код возврата CLI следует политике владельца.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner
from v8unpack_agent.run_report import RunObjectKind, RunObjectStatus
from v8unpack_agent.runner import RunOptions, run_pipeline

FORM_ID = "Catalog/SampleObject/ItemForm/Variant1"
COMMON_MODULE_ID = "CommonModule/SharedHelpers/CommonModule.obj.bsl"
BROKEN_ID = "Constant/Broken/Constant.obj.bsl"


def _add_module_index_files(root: Path) -> None:
    (root / "Configuration.802.bsl").write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")
    (root / "Configuration.app.bsl").write_bytes(b"")
    constant = root / "Constant" / "Limit"
    constant.mkdir(parents=True)
    (constant / "Constant.obj.bsl").write_text("// модуль\n", encoding="utf-8")
    (root / "HTTPService" / "Api").mkdir(parents=True)
    web = root / "WebService" / "Ws"
    web.mkdir(parents=True)
    (web / "WebService.obj.bsl").write_text("  \n", encoding="utf-8")


def _add_form(root: Path) -> None:
    form_dir = root / "Catalog" / "SampleObject" / "ItemForm" / "Variant1"
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_text("// module\n", encoding="utf-8")


def _add_common_module(root: Path) -> None:
    module_dir = root / "CommonModule" / "SharedHelpers"
    module_dir.mkdir(parents=True)
    (module_dir / "CommonModule.obj.bsl").write_text("// shared\n", encoding="utf-8")


def _add_broken_module(root: Path) -> None:
    broken = root / "Constant" / "Broken"
    broken.mkdir(parents=True)
    (broken / "Constant.obj.bsl").write_bytes(b"\xff\xfe\xfa")


def _mixed_export(root: Path) -> None:
    _add_form(root)
    _add_common_module(root)
    _add_module_index_files(root)


def _options(root: Path, **extra: object) -> RunOptions:
    return RunOptions(
        export_root=root,
        include_skd=False,
        include_module_index=True,
        **extra,  # type: ignore[arg-type]
    )


def _module_objects(outcome: runner.RunOutcome) -> list[runner.ObjectRunResult]:
    return [o for o in outcome.report.objects if o.object_kind.value.startswith("module_")]


def test_mixed_export_keeps_every_record_once(tmp_path: Path) -> None:
    _mixed_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path))
    assert outcome.completed

    ids = [o.object for o in outcome.report.objects]
    assert len(ids) == len(set(ids))

    forms = [o for o in outcome.report.objects if o.object_kind is RunObjectKind.FORM]
    commons = [o for o in outcome.report.objects if o.object_kind is RunObjectKind.COMMON_MODULE]
    assert [o.object for o in forms] == [FORM_ID]
    assert [o.object for o in commons] == [COMMON_MODULE_ID]

    module_ids = {o.object for o in _module_objects(outcome)}
    assert COMMON_MODULE_ID not in module_ids
    assert not any(path.startswith(FORM_ID) for path in module_ids)


def test_mixed_export_summary_modules_ignores_form_and_common_module(tmp_path: Path) -> None:
    _mixed_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path))
    table = outcome.report.to_dict()["summary"]["modules"]

    assert "form" not in table["by_kind"]
    assert "common_module" not in table["by_kind"]
    counted = sum(row["total"] for row in table["by_kind"].values())
    assert counted == len(_module_objects(outcome))


def test_mixed_export_summary_counts_add_up(tmp_path: Path) -> None:
    _mixed_export(tmp_path)
    outcome = run_pipeline(_options(tmp_path))
    summary = outcome.report.summary

    assert summary.found == summary.complete + summary.partial + summary.failed
    assert summary.found + summary.excluded == len(outcome.report.objects)
    assert len(outcome.report.objects) == 1 + 1 + len(_module_objects(outcome))


def test_read_error_adds_exactly_one_failed_next_to_form_and_common_module(tmp_path: Path) -> None:
    _mixed_export(tmp_path)
    baseline = run_pipeline(_options(tmp_path)).report.summary
    _add_broken_module(tmp_path)
    outcome = run_pipeline(_options(tmp_path))

    assert outcome.completed
    assert outcome.degraded
    assert outcome.report.summary.failed == baseline.failed + 1
    assert outcome.report.summary.found == baseline.found + 1
    broken = [o for o in outcome.report.objects if o.object == BROKEN_ID]
    assert [(o.status, o.reason_code) for o in broken] == [(RunObjectStatus.FAILED, "read_error")]


def test_cli_excluded_statuses_and_common_module_give_exit_ok(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _add_common_module(export)
    _add_module_index_files(export)
    report = tmp_path / "report.json"

    code = cli.main([str(export), "--report-path", str(report), "--skip-skd", "--include-module-index"])

    assert code == cli.EXIT_OK
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["excluded"] >= 3
    assert data["summary"]["failed"] == 0


def test_cli_read_error_with_common_module_gives_degraded(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _add_common_module(export)
    _add_module_index_files(export)
    _add_broken_module(export)
    report = tmp_path / "report.json"

    code = cli.main([str(export), "--report-path", str(report), "--skip-skd", "--include-module-index"])

    assert code == cli.EXIT_DEGRADED


def test_cli_scanner_failure_gives_fatal_exit_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    export = tmp_path / "export"
    export.mkdir()
    _add_module_index_files(export)
    report = tmp_path / "report.json"

    def boom(_root: Path) -> list[object]:
        raise ValueError("scanner failed")

    monkeypatch.setattr(runner, "scan_service_modules", boom)
    code = cli.main([str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules", "--include-module-index"])

    assert code == cli.EXIT_FATAL
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["fatal_error"]["reason_code"] == "modules_failed"
    assert data["objects"] == []
    assert "modules" not in data["summary"]
