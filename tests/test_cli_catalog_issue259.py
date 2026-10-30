"""Эквивалентность каталога CLI фактическому parser и поведению (issue #259).

Каталог проверяется не сверкой с самим собой: required, типы и choices
подтверждаются разбором аргументов фактическими parser, а exit codes —
реальными вызовами ``main`` на синтетических деревьях.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import _cli_catalog as catalog
from v8unpack_agent import cli, runner, search_api, search_cli
from v8unpack_agent.run_report import PostRunReport, RunFatalError, RunSummary

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS_SEPARATOR = chr(92)


def _catalog() -> dict[str, Any]:
    return catalog.build_catalog()


def _command(name: str) -> dict[str, Any]:
    for item in _catalog()["commands"]:
        if item["name"] == name:
            return item
    raise AssertionError(name)


def _parser(name: str) -> argparse.ArgumentParser:
    return catalog._parsers()[name]


def _parses(parser: argparse.ArgumentParser, argv: list[str]) -> bool:
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            old = sys.stderr
            sys.stderr = sink
            try:
                parser.parse_args(argv)
            finally:
                sys.stderr = old
    except SystemExit:
        return False
    return True


def _minimal_argv(name: str, params: list[dict[str, Any]]) -> list[str]:
    """Минимальный набор аргументов по каталогу: required со значениями."""
    argv: list[str] = []
    for param in params:
        if not param["required"]:
            continue
        if param["kind"] == "positional":
            argv.append("root")
        else:
            value = param["choices"][0] if param["choices"] else "v"
            argv += [param["name"], value]
    return argv


# --- форма каталога ------------------------------------------------------------


def test_catalog_envelope_and_contract_link() -> None:
    data = _catalog()
    assert data["schema_version"] == "1.0"
    assert data["kind"] == "v8unpack-agent.cli-catalog"
    assert data["json_contract"] == {
        "kind": cli.JSON_KIND,
        "schema_version": cli.JSON_SCHEMA_VERSION,
    }
    assert data["usage_error"] == {"exit_code": cli.EXIT_BAD_INPUT, "source": "argparse"}


def test_catalog_lists_exactly_parser_commands() -> None:
    names = [item["name"] for item in _catalog()["commands"]]
    assert sorted(names) == list(catalog.parser_commands())
    assert len(names) == len(set(names))


def test_new_parser_command_breaks_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    original = search_cli.build_parser

    def extended() -> argparse.ArgumentParser:
        parser = original()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                action.add_parser("extra")
        return parser

    monkeypatch.setattr(search_cli, "build_parser", extended)
    registry = {spec.name for spec in catalog.COMMANDS}
    assert "search_cli extra" in catalog.parser_commands()
    assert set(catalog.parser_commands()) != registry


def test_registry_covers_parser_commands() -> None:
    assert set(catalog.parser_commands()) == {spec.name for spec in catalog.COMMANDS}


# --- эквивалентность параметров поведению parser ---------------------------------


@pytest.mark.parametrize("name", catalog.parser_commands())
def test_parameter_names_match_parser(name: str) -> None:
    parser = _parser(name)
    expected = set()
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        expected.add(action.dest if not action.option_strings else max(action.option_strings, key=len))
    assert {p["name"] for p in _command(name)["parameters"]} == expected


@pytest.mark.parametrize("name", catalog.parser_commands())
def test_required_flags_match_parser_behaviour(name: str) -> None:
    params = _command(name)["parameters"]
    parser = _parser(name)
    base = _minimal_argv(name, params)
    assert _parses(parser, base), "минимальный набор по каталогу не разбирается"
    for param in params:
        if not param["required"]:
            continue
        if param["kind"] == "positional":
            reduced = [arg for arg in base if arg != "root"]
        else:
            idx = base.index(param["name"])
            reduced = base[:idx] + base[idx + 2 :]
        assert not _parses(parser, reduced), f"{param['name']} не обязателен в parser"


@pytest.mark.parametrize("name", catalog.parser_commands())
def test_types_and_choices_match_parser_behaviour(name: str) -> None:
    params = _command(name)["parameters"]
    parser = _parser(name)
    base = _minimal_argv(name, params)
    for param in params:
        if param["kind"] == "positional" or param["required"]:
            continue
        flag = param["name"]
        if param["type"] == "boolean":
            assert _parses(parser, [*base, flag])
            assert not _parses(parser, [*base, flag + "=x"])
            continue
        if param["type"] in ("integer", "number"):
            assert _parses(parser, [*base, flag, "3"])
            assert not _parses(parser, [*base, flag, "not-a-number"])
        if param["choices"] and flag not in catalog.VALUE_REGISTRIES:
            for value in param["choices"]:
                assert _parses(parser, [*base, flag, value])
            assert not _parses(parser, [*base, flag, "no-such-choice"])


def test_value_registries_are_enforced_by_cli(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    report = str(tmp_path / "r.json")
    base = [str(root), "--report-path", report, "--include-module-index"]
    for group in _command("v8unpack-agent-run")["parameters"]:
        if group["name"] == "--module-group":
            assert group["choices"] == sorted(runner.MODULE_GROUPS)
    assert cli.main([*base, "--module-group", "no-such-group"]) == cli.EXIT_BAD_INPUT


@pytest.mark.parametrize("name", catalog.parser_commands())
def test_side_effects_reference_parser_parameters(name: str) -> None:
    names = {p["name"] for p in _command(name)["parameters"]}
    for effect in _command(name)["side_effects"]:
        assert effect["action"] in ("read", "write")
        assert effect["parameter"] in names


# --- exit codes реальными вызовами ---------------------------------------------


def _fatal_outcome() -> runner.RunOutcome:
    stamp = runner._utc_now()
    return runner.RunOutcome(
        report=PostRunReport(
            schema_version=runner.SCHEMA_VERSION,
            completed=False,
            started_at=stamp,
            finished_at=stamp,
            summary=RunSummary.from_objects(()),
            objects=(),
            fatal_error=RunFatalError(reason_code=runner.FATAL_SCAN_FAILED, error_type="runtime_error"),
        )
    )


def _run_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[int]:
    root = tmp_path / "run-export"
    root.mkdir()
    degraded = tmp_path / "run-degraded"
    form_dir = degraded / "Catalog" / "Образец" / "ItemForm" / "Variant1"
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_text("// модуль\n", encoding="utf-8")
    ok_report = str(tmp_path / "r.json")
    bad_report = str(tmp_path / "absent" / "r.json")
    codes = {
        cli.main([str(root), "--report-path", ok_report]),
        cli.main([str(tmp_path / "absent-root"), "--report-path", ok_report]),
        cli.main([str(degraded), "--report-path", ok_report]),
        cli.main([str(root), "--report-path", bad_report]),
    }
    with monkeypatch.context() as patch:
        patch.setattr(cli, "run_pipeline", lambda options: _fatal_outcome())
        codes.add(cli.main([str(root), "--report-path", ok_report]))
        codes.add(cli.main([str(root), "--report-path", bad_report]))
    return codes


def _search_export(root: Path) -> Path:
    module = root / "CommonModule" / "ОбщийМодуль" / "Ext"
    module.mkdir(parents=True)
    (module / "Module.bsl").write_bytes("Процедура Тест() Экспорт\nКонецПроцедуры\n".encode())
    return root


def _build_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[int]:
    root = _search_export(tmp_path / "build-export")
    demo = ["--embedder", "hashed-bow-demo"]
    codes = {
        search_cli.main(["build", str(root), "--snapshot", str(tmp_path / "s.json"), *demo]),
        search_cli.main(["build", str(tmp_path / "absent"), "--snapshot", str(tmp_path / "s2.json"), *demo]),
        search_cli.main(["build", str(root), "--snapshot", str(tmp_path / "absent" / "s.json"), *demo]),
    }
    with monkeypatch.context() as patch:

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("boom")

        patch.setattr(search_api, "scan_forms", boom)
        codes.add(search_cli.main(["build", str(root), "--snapshot", str(tmp_path / "s3.json"), *demo]))
    return codes


def _query_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[int]:
    root = _search_export(tmp_path / "query-export")
    codes = {
        search_cli.main(["query", str(root), "--query", "ОбщийМодуль", "--search-mode", "exact"]),
        search_cli.main(["query", str(tmp_path / "absent"), "--query", "x"]),
        search_cli.main(["query", str(root), "--query", "смысл", "--search-mode", "semantic"]),
    }
    with monkeypatch.context() as patch:

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("boom")

        patch.setattr(search_api, "scan_forms", boom)
        codes.add(search_cli.main(["query", str(root), "--query", "x"]))
    return codes


@pytest.mark.parametrize(
    ("name", "invoke"),
    [
        ("v8unpack-agent-run", _run_codes),
        ("search_cli build", _build_codes),
        ("search_cli query", _query_codes),
    ],
)
def test_exit_codes_match_real_invocations(
    name: str,
    invoke: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed = invoke(tmp_path, monkeypatch)
    capsys.readouterr()
    declared = {entry["code"] for entry in _command(name)["exit_codes"]}
    assert observed == declared


@pytest.mark.parametrize("name", catalog.parser_commands())
def test_usage_error_is_universal(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--no-such-flag"]
    if name.startswith("search_cli"):
        code = search_cli.main([name.split()[1], *argv])
    else:
        code = cli.main(argv)
    capsys.readouterr()
    assert code == _catalog()["usage_error"]["exit_code"]


def test_registries_do_not_duplicate_usage_semantics() -> None:
    for spec in catalog.COMMANDS:
        for entry in spec.exit_codes:
            assert entry.status != "usage"
            assert "аргумент" not in entry.condition
        bad_input = [e for e in spec.exit_codes if e.code == catalog.USAGE_ERROR_EXIT_CODE]
        assert bad_input == [catalog._ROOT_INVALID]


def test_json_error_codes_only_for_json_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for item in _catalog()["commands"]:
        if item["name"] != "v8unpack-agent-run":
            assert item["json_mode"] is None
    declared = set(_command("v8unpack-agent-run")["json_mode"]["error_codes"])
    root = tmp_path / "export"
    root.mkdir()
    seen: set[str] = set()

    def code_of(argv: list[str]) -> None:
        cli.main([*argv, "--json"])
        error = json.loads(capsys.readouterr().out)["error"]
        if error:
            seen.add(error["code"])

    code_of([str(root)])
    code_of([str(tmp_path / "absent"), "--report-path", str(tmp_path / "r.json")])
    code_of([str(root), "--report-path", str(tmp_path / "absent" / "r.json")])
    with monkeypatch.context() as patch:
        patch.setattr(cli, "run_pipeline", lambda options: _fatal_outcome())
        code_of([str(root), "--report-path", str(tmp_path / "r.json")])
        code_of([str(root), "--report-path", str(tmp_path / "absent" / "r.json")])

        def boom(options: object) -> runner.RunOutcome:
            raise RuntimeError("boom")

        patch.setattr(cli, "run_pipeline", boom)
        code_of([str(root), "--report-path", str(tmp_path / "r.json")])
    assert seen == declared


# --- интерфейс и инварианты ----------------------------------------------------


def test_describe_flag_outputs_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--describe-cli"]) == cli.EXIT_OK
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.endswith("\n") and out.out.count("\n") == 1
    assert json.loads(out.out) == _catalog()


def test_help_unchanged_by_introspection(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--help"])
    help_text = capsys.readouterr().out
    assert "--describe-cli" not in help_text
    assert help_text == cli.build_parser().format_help()


def test_introspection_is_side_effect_free_and_deterministic(tmp_path: Path) -> None:
    outputs = []
    for cwd, lang in ((tmp_path, "C"), (REPO_ROOT, "ru_RU.UTF-8")):
        before = sorted(p.name for p in Path(cwd).iterdir())
        env = {**os.environ, "LANG": lang, "LC_ALL": lang, "PYTHONPATH": str(REPO_ROOT)}
        proc = subprocess.run(
            [sys.executable, "-m", "v8unpack_agent.cli", "--describe-cli"],
            cwd=cwd,
            env=env,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0
        assert proc.stderr == b""
        assert sorted(p.name for p in Path(cwd).iterdir()) == before
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1]


def test_catalog_has_no_paths_or_internal_api() -> None:
    text = catalog.render_catalog()
    assert str(REPO_ROOT) not in text
    assert WINDOWS_SEPARATOR not in text
    assert "_cli_catalog" not in text
    assert "help" not in json.dumps([p for c in _catalog()["commands"] for p in c["parameters"]])
