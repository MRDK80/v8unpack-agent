"""Выбор групп модулей при включённом индексе модулей (issue #346)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner
from v8unpack_agent.modules import ModuleEntry
from v8unpack_agent.runner import MODULE_GROUPS, RunOptions, run_pipeline

_FAKES = {
    "configuration": (
        "scan_configuration_modules",
        {
            "module_kind": "ordinary_application",
            "owner_kind": "configuration",
            "metadata_type": None,
            "owner_name": None,
            "relative_path": "Configuration.802.bsl",
        },
    ),
    "metadata": (
        "scan_metadata_object_modules",
        {
            "module_kind": "object",
            "owner_kind": "metadata_object",
            "metadata_type": "Catalog",
            "owner_name": "Goods",
            "relative_path": "Catalog/Goods/Ext/ObjectModule.bsl",
        },
    ),
    "record-sets": (
        "scan_record_set_modules",
        {
            "module_kind": "record_set",
            "owner_kind": "metadata_object",
            "metadata_type": "InformationRegister",
            "owner_name": "Prices",
            "relative_path": "InformationRegister/Prices/Ext/RecordSetModule.bsl",
        },
    ),
    "commands": (
        "scan_command_modules",
        {
            "module_kind": "command",
            "owner_kind": "metadata_object_command",
            "metadata_type": "Catalog",
            "owner_name": "Goods.Print",
            "relative_path": "Catalog/Goods/Commands/Print/Ext/CommandModule.bsl",
        },
    ),
    "value-managers": (
        "scan_value_manager_modules",
        {
            "module_kind": "value_manager",
            "owner_kind": "metadata_object",
            "metadata_type": "Constant",
            "owner_name": "Limit",
            "relative_path": "Constant/Limit/Constant.obj.bsl",
        },
    ),
    "services": (
        "scan_service_modules",
        {
            "module_kind": "service",
            "owner_kind": "metadata_object",
            "metadata_type": "WebService",
            "owner_name": "Ws",
            "relative_path": "WebService/Ws/WebService.obj.bsl",
        },
    ),
}
_KIND = {group: f"module_{spec[1]['module_kind']}" for group, spec in _FAKES.items()}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    for group, (name, spec) in _FAKES.items():
        entry = ModuleEntry(read_status="ok", **spec)  # type: ignore[arg-type]

        def scanner(_root: Path, _group: str = group, _entry: ModuleEntry = entry) -> list[ModuleEntry]:
            seen.append(_group)
            return [_entry]

        monkeypatch.setattr(runner, name, scanner)
    return seen


def _options(root: Path, **extra: object) -> RunOptions:
    return RunOptions(
        export_root=root,
        include_common_modules=False,
        include_skd=False,
        include_module_index=True,
        **extra,  # type: ignore[arg-type]
    )


def _kinds(outcome: runner.RunOutcome) -> list[str]:
    return sorted(o.object_kind.value for o in outcome.report.objects)


def _expected(groups: tuple[str, ...]) -> list[str]:
    return sorted(_KIND[g] for g in groups)


def _ordered(groups: set[str] | tuple[str, ...]) -> list[str]:
    return [g for g in MODULE_GROUPS if g in groups]


def test_group_names_are_fixed() -> None:
    assert MODULE_GROUPS == (
        "configuration",
        "metadata",
        "record-sets",
        "commands",
        "value-managers",
        "services",
    )
    assert set(_FAKES) == set(MODULE_GROUPS)


def test_default_runs_all_groups(tmp_path: Path, calls: list[str]) -> None:
    options = _options(tmp_path)
    assert options.module_groups is None
    outcome = run_pipeline(options)
    assert outcome.completed
    assert calls == list(MODULE_GROUPS)
    assert _kinds(outcome) == _expected(MODULE_GROUPS)


@pytest.mark.parametrize(
    "groups",
    [
        ("services",),
        ("configuration", "services"),
        ("commands", "metadata"),
        ("record-sets", "value-managers", "commands"),
        MODULE_GROUPS,
    ],
)
def test_selected_groups_only(tmp_path: Path, calls: list[str], groups: tuple[str, ...]) -> None:
    outcome = run_pipeline(_options(tmp_path, module_groups=groups))
    assert outcome.completed
    assert calls == _ordered(groups)
    assert _kinds(outcome) == _expected(groups)
    by_kind = outcome.report.to_dict()["summary"]["modules"]["by_kind"]
    assert set(by_kind) == {_KIND[g].removeprefix("module_") for g in groups}


def test_order_and_repeats_are_normalized(tmp_path: Path, calls: list[str]) -> None:
    options = _options(tmp_path, module_groups=("services", "commands", "services"))
    assert options.module_groups == ("commands", "services")
    run_pipeline(options)
    assert calls == ["commands", "services"]


def test_result_does_not_depend_on_option_order(tmp_path: Path, calls: list[str]) -> None:
    first = run_pipeline(_options(tmp_path, module_groups=("metadata", "services")))
    second = run_pipeline(_options(tmp_path, module_groups=("services", "metadata")))
    assert first.report.to_dict()["objects"] == second.report.to_dict()["objects"]
    assert first.report.to_dict()["summary"] == second.report.to_dict()["summary"]


def test_report_schema_unchanged(tmp_path: Path, calls: list[str]) -> None:
    data = run_pipeline(_options(tmp_path, module_groups=("services",))).report.to_dict()
    assert data["schema_version"] == 1
    assert set(data) == {"schema_version", "run", "summary", "objects", "fatal_error"}
    assert json.loads(json.dumps(data)) == data


def test_module_scanners_selection() -> None:
    assert len(runner._module_scanners()) == 6
    assert runner._module_scanners(("services",)) == (runner.scan_service_modules,)
    assert runner._module_scanners(("services", "configuration")) == (
        runner.scan_configuration_modules,
        runner.scan_service_modules,
    )


@pytest.mark.parametrize("bad", [(), ("bogus",), ("services", "bogus"), "services"])
def test_invalid_groups_rejected(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError):
        _options(tmp_path, module_groups=bad)


def test_unknown_group_value_is_not_echoed(tmp_path: Path) -> None:
    secret = str(tmp_path / "secret_dir")
    with pytest.raises(ValueError) as info:
        _options(tmp_path, module_groups=(secret,))
    assert secret not in str(info.value)
    assert "secret_dir" not in str(info.value)


def test_groups_require_module_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RunOptions(export_root=tmp_path, include_module_index=False, module_groups=("services",))


def _run_cli(tmp_path: Path, *flags: str) -> tuple[int, Path]:
    export = tmp_path / "export"
    export.mkdir(exist_ok=True)
    report = tmp_path / "report.json"
    argv = [str(export), "--report-path", str(report), "--skip-skd", "--skip-common-modules", *flags]
    return cli.main(argv), report


def _cli_kinds(report: Path) -> set[str]:
    data = json.loads(report.read_text(encoding="utf-8"))
    return {o["object_kind"] for o in data["objects"]}


def test_cli_without_group_flags_runs_all(tmp_path: Path, calls: list[str]) -> None:
    code, report = _run_cli(tmp_path, "--include-module-index")
    assert code == cli.EXIT_OK
    assert calls == list(MODULE_GROUPS)
    assert _cli_kinds(report) == set(_expected(MODULE_GROUPS))


def test_cli_module_group_is_repeatable(tmp_path: Path, calls: list[str]) -> None:
    code, report = _run_cli(
        tmp_path, "--include-module-index", "--module-group", "services", "--module-group", "configuration"
    )
    assert code == cli.EXIT_OK
    assert calls == ["configuration", "services"]
    assert _cli_kinds(report) == set(_expected(("configuration", "services")))


def test_cli_skip_module_group(tmp_path: Path, calls: list[str]) -> None:
    code, report = _run_cli(
        tmp_path, "--include-module-index", "--skip-module-group", "services", "--skip-module-group", "services"
    )
    assert code == cli.EXIT_OK
    assert calls == _ordered(set(MODULE_GROUPS) - {"services"})
    assert _cli_kinds(report) == set(_expected(tuple(g for g in MODULE_GROUPS if g != "services")))


_SKIP_ALL = [arg for g in MODULE_GROUPS for arg in ("--skip-module-group", g)]


@pytest.mark.parametrize(
    "flags",
    [
        ["--module-group", "services"],
        ["--skip-module-group", "services"],
        ["--include-module-index", "--module-group", "services", "--skip-module-group", "commands"],
        ["--include-module-index", "--module-group", "bogus"],
        ["--include-module-index", "--skip-module-group", "bogus"],
        ["--include-module-index", "--module-group", ""],
        ["--include-module-index", *_SKIP_ALL],
    ],
)
def test_cli_usage_errors(tmp_path: Path, calls: list[str], flags: list[str]) -> None:
    code, report = _run_cli(tmp_path, *flags)
    assert code == cli.EXIT_BAD_INPUT
    assert not report.exists()
    assert calls == []


def test_cli_error_does_not_echo_value(tmp_path: Path, calls: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    secret = tmp_path / "secret_dir"
    code, _ = _run_cli(tmp_path, "--include-module-index", "--module-group", str(secret))
    err = capsys.readouterr().err
    assert code == cli.EXIT_BAD_INPUT
    assert str(tmp_path) not in err
    assert "secret_dir" not in err
    assert "services" in err
