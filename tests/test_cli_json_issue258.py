"""Контракт JSON-режима ``v8unpack-agent-run --json`` (issue #258).

Characterization-часть фиксирует текстовый вывод и exit codes без флага;
contract-часть проверяет envelope ``schema_version`` 1.0. Входные деревья
синтетические, фатальные отказы вносятся подменой границ модуля ``cli``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner
from v8unpack_agent.run_report import (
    PostRunReport,
    RunFatalError,
    RunSummary,
)

ENVELOPE_KEYS = {"schema_version", "kind", "ok", "status", "exit_code", "error", "data"}
WINDOWS_SEPARATOR = chr(92)


def _make_degraded_export(root: Path) -> None:
    """Дерево с одной формой без разметки: прогон даёт degraded."""
    form_dir = root / "Catalog" / "ОбразецОбъекта" / "ItemForm" / "Variant1"
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_text("// модуль\n", encoding="utf-8")


def _fatal_outcome() -> runner.RunOutcome:
    stamp = runner._utc_now()
    report = PostRunReport(
        schema_version=runner.SCHEMA_VERSION,
        completed=False,
        started_at=stamp,
        finished_at=stamp,
        summary=RunSummary.from_objects(()),
        objects=(),
        fatal_error=RunFatalError(
            reason_code=runner.FATAL_SCAN_FAILED,
            error_type="runtime_error",
        ),
    )
    return runner.RunOutcome(report=report)


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _envelope(out: str) -> dict[str, object]:
    assert out.endswith("\n") and out.count("\n") == 1, "stdout — не одна JSON-строка"
    payload = json.loads(out)
    assert isinstance(payload, dict)
    assert set(payload) == ENVELOPE_KEYS
    assert payload["schema_version"] == "1.0"
    assert payload["kind"] == "v8unpack-agent-run.result"
    return payload


# --- characterization: поведение без флага ---------------------------------


def test_text_summary_without_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "export"
    root.mkdir()
    code, out, err = _run(capsys, [str(root), "--report-path", str(tmp_path / "r.json")])
    assert code == cli.EXIT_OK
    assert out == (
        "обнаружено: 0, complete: 0, partial: 0, failed: 0, excluded: 0\n"
        "промпт-символов: 0\n"
        "предупреждений сканера: 0\n"
        "отчёт записан\n"
    )
    assert err == ""


def test_text_bad_root_without_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(
        capsys, [str(tmp_path / "absent"), "--report-path", str(tmp_path / "r.json")]
    )
    assert code == cli.EXIT_BAD_INPUT
    assert out == ""
    assert err.startswith("v8unpack-agent-run: корень выгрузки не найден")


def test_exit_code_constants_unchanged() -> None:
    assert (cli.EXIT_OK, cli.EXIT_BAD_INPUT, cli.EXIT_DEGRADED, cli.EXIT_FATAL) == (0, 2, 3, 4)
    assert (cli.EXIT_WRITE_ERROR, cli.EXIT_FATAL_AND_WRITE_ERROR) == (5, 6)


def test_help_lists_json_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, ["--help"])
    assert code == cli.EXIT_OK
    assert "--json" in out
    assert "{" not in out.splitlines()[0]


# --- контракт JSON-режима ----------------------------------------------------


def test_json_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "export"
    root.mkdir()
    code, out, err = _run(capsys, [str(root), "--report-path", str(tmp_path / "r.json"), "--json"])
    payload = _envelope(out)
    assert code == cli.EXIT_OK
    assert payload["ok"] is True and payload["status"] == "ok" and payload["exit_code"] == 0
    assert payload["error"] is None
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["summary"] == {"found": 0, "complete": 0, "partial": 0, "failed": 0, "excluded": 0}
    assert data["report"] == {"file_name": "r.json", "written": True, "schema_version": 1}
    assert data["fatal_error"] is None
    assert err == ""


def test_json_degraded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "export"
    _make_degraded_export(root)
    code, out, _ = _run(capsys, [str(root), "--report-path", str(tmp_path / "r.json"), "--json"])
    payload = _envelope(out)
    assert code == cli.EXIT_DEGRADED
    assert payload["status"] == "degraded" and payload["ok"] is False
    assert payload["error"] is None


@pytest.mark.parametrize(
    "argv_tail",
    [
        [],  # нет --report-path
        ["--report-path", "r.json", "--mode", "unknown"],
        ["--report-path", "r.json", "--module-group", "configuration"],
    ],
)
def test_json_arguments_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv_tail: list[str]
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    code, out, err = _run(capsys, [str(root), "--json", *argv_tail])
    payload = _envelope(out)
    assert code == cli.EXIT_BAD_INPUT
    assert payload["status"] == "bad_input"
    assert payload["error"] == {"code": cli.ERROR_ARGUMENTS_INVALID}
    assert payload["data"] is None
    assert err  # usage argparse остаётся в stderr


def test_json_export_root_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(
        capsys, [str(tmp_path / "absent"), "--report-path", str(tmp_path / "r.json"), "--json"]
    )
    payload = _envelope(out)
    assert code == cli.EXIT_BAD_INPUT
    assert payload["error"] == {"code": cli.ERROR_EXPORT_ROOT_INVALID}


def test_json_report_write_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "export"
    root.mkdir()
    code, out, _ = _run(
        capsys, [str(root), "--report-path", str(tmp_path / "absent" / "r.json"), "--json"]
    )
    payload = _envelope(out)
    assert code == cli.EXIT_WRITE_ERROR
    assert payload["error"] == {"code": cli.ERROR_REPORT_WRITE_FAILED}
    data = payload["data"]
    assert isinstance(data, dict) and data["report"]["written"] is False


def test_json_pipeline_fatal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    monkeypatch.setattr(cli, "run_pipeline", lambda options: _fatal_outcome())
    code, out, err = _run(capsys, [str(root), "--report-path", str(tmp_path / "r.json"), "--json"])
    payload = _envelope(out)
    assert code == cli.EXIT_FATAL
    assert payload["error"] == {"code": cli.ERROR_PIPELINE_FATAL}
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["fatal_error"] == {"reason_code": runner.FATAL_SCAN_FAILED, "error_type": "runtime_error"}
    assert "фатальная ошибка" in err


def test_json_fatal_and_write_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    monkeypatch.setattr(cli, "run_pipeline", lambda options: _fatal_outcome())
    code, out, _ = _run(
        capsys, [str(root), "--report-path", str(tmp_path / "absent" / "r.json"), "--json"]
    )
    payload = _envelope(out)
    assert code == cli.EXIT_FATAL_AND_WRITE_ERROR
    assert payload["error"] == {"code": cli.ERROR_PIPELINE_FATAL_AND_REPORT_WRITE_FAILED}


def test_json_internal_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "export"
    root.mkdir()

    def boom(options: object) -> runner.RunOutcome:
        raise RuntimeError(str(tmp_path / "secret"))

    monkeypatch.setattr(cli, "run_pipeline", boom)
    code, out, err = _run(capsys, [str(root), "--report-path", str(tmp_path / "r.json"), "--json"])
    payload = _envelope(out)
    assert code == cli.EXIT_FATAL
    assert payload["error"] == {"code": cli.ERROR_INTERNAL}
    assert str(tmp_path) not in out + err


def test_error_codes_independent_of_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Машинный код не зависит от человеческой формулировки stderr."""
    monkeypatch.setattr(cli, "_PROGRAM", "переименованная-программа")
    code, out, err = _run(
        capsys, [str(tmp_path / "absent"), "--report-path", str(tmp_path / "r.json"), "--json"]
    )
    assert code == cli.EXIT_BAD_INPUT
    assert "переименованная-программа" in err
    assert _envelope(out)["error"] == {"code": cli.ERROR_EXPORT_ROOT_INVALID}


def test_registry_lists_only_reachable_codes() -> None:
    """Реестр совпадает с набором кодов, проверяемых тестами этого модуля."""
    assert set(cli.JSON_ERROR_CODES) == {
        cli.ERROR_ARGUMENTS_INVALID,
        cli.ERROR_EXPORT_ROOT_INVALID,
        cli.ERROR_INTERNAL,
        cli.ERROR_PIPELINE_FATAL,
        cli.ERROR_PIPELINE_FATAL_AND_REPORT_WRITE_FAILED,
        cli.ERROR_REPORT_WRITE_FAILED,
    }
    for code in cli.JSON_ERROR_CODES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", code)


@pytest.mark.parametrize("report_name", ["отчёт.json", "report.json"])
def test_json_has_no_paths_and_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], report_name: str
) -> None:
    root = tmp_path / "выгрузка"
    _make_degraded_export(root)
    argv = [str(root), "--report-path", str(tmp_path / report_name), "--json"]
    _, first, _ = _run(capsys, argv)
    _, second, _ = _run(capsys, argv)

    def stable(text: str) -> dict[str, object]:
        return _envelope(text)

    assert stable(first) == stable(second)
    assert first == second
    for out in (first,):
        assert str(tmp_path) not in out
        assert WINDOWS_SEPARATOR not in out
        assert "/" not in out
        assert _envelope(out)["data"]["report"]["file_name"] == report_name  # type: ignore[index]


def test_json_windows_style_report_name_is_file_name_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Имя отчёта берётся как последний сегмент pathlib на любой ОС."""
    root = tmp_path / "export"
    root.mkdir()
    report = tmp_path / "sub" / "r.json"
    report.parent.mkdir()
    code, out, _ = _run(capsys, [str(root), "--report-path", str(report), "--json"])
    assert code == cli.EXIT_OK
    assert _envelope(out)["data"]["report"]["file_name"] == "r.json"  # type: ignore[index]
