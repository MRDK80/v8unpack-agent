"""Единая fail-closed граница санитизации диагностики (issue #142).

Все пути и имена синтетические: реальные каталоги, хосты и пользователи не
используются. Canary-строки собираются из сегментов через ``SEP_NT`` и
``"/"``, а не через ``Path``, поэтому проверки не зависят от разделителя
текущей ОС.

Матрица каналов:

* public response — ``FormScanIndex``, ``RunOutcome``, ``FormContext``,
  LLM-фрагмент;
* log — записи logger ``v8unpack_agent.scan_forms`` и stderr/stdout CLI;
* persisted artifact — ``forms_scan_index.json`` и post-run report.
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

import pytest

import v8unpack_agent._safe_paths as safe_paths
import v8unpack_agent.scan_forms as sf
from v8unpack_agent import cli, runner
from v8unpack_agent._safe_paths import (
    SANITIZER_FAILED_TEXT,
    TRUNCATION_MARKER,
    safe_error_text,
    sanitize_diagnostic,
)
from v8unpack_agent.form_context import (
    DATA_PATH_LINE_PREFIX,
    FormContext,
    _data_path_status_line,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import (
    SCAN_WARNING_FORM_SCAN_ERROR,
    FormScanIndex,
    scan_forms,
    scan_warning_code,
)

#: Разделитель NT задан кодом символа: литерала в тексте теста нет.
SEP_NT = chr(92)
TAIL = ["private-root-142", "a", "b", "c", "d", "Form.json"]

POSIX_CANARY = "/" + "/".join(["home", "canary-user-142", *TAIL])
WINDOWS_CANARY = SEP_NT.join(["C:", "Users", "canary-user-142", *TAIL])
UNC_CANARY = SEP_NT * 2 + SEP_NT.join(["canary-host-142", "canary-share-142", *TAIL])

CANARIES = {
    "posix": POSIX_CANARY,
    "windows": WINDOWS_CANARY,
    "unc": UNC_CANARY,
}

#: Маркеры, которые не должны появиться ни в одном внешнем канале.
LOCAL_MARKERS = (
    "canary-user-142",
    "canary-host-142",
    "canary-share-142",
    "private-root-142",
    "/home/",
    "C:" + SEP_NT,
    SEP_NT * 2,
)

SAFE_RELATIVE = "Catalog/Obj/CatalogForm/Form"
SAFE_CODE = "binding_not_proven"


def _assert_clean(text: str, canary: str) -> None:
    assert canary not in text
    for marker in LOCAL_MARKERS:
        assert marker not in text, f"маркер {marker!r} найден в {text!r}"


def _canary_error(canary: str) -> OSError:
    """Синтетическое исключение: canary в сообщении и в traceback."""
    try:
        raise OSError(f"[Errno 2] No such file or directory: '{canary}'")
    except OSError as exc:
        error = exc
    rendered = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert canary in str(error)
    assert canary in rendered
    return error


def _make_config_form(root: Path) -> None:
    form_dir = root / "Catalog" / "Obj" / "CatalogForm" / "Form"
    form_dir.mkdir(parents=True)
    (form_dir / "CatalogForm.obj.bsl").write_text("// module\n", encoding="utf-8")
    (form_dir / "CatalogForm.json").write_text("{}", encoding="utf-8")


def _raiser(error: BaseException):
    def raise_error(*args: object, **kwargs: object) -> None:
        raise error

    return raise_error


def _broken(text: str) -> str:
    raise RuntimeError(text)


# --------------------------------------------------------------------------- #
# A. контракт sanitizer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_sanitizer_removes_absolute_paths(kind: str) -> None:
    canary = CANARIES[kind]
    for text in (
        f"[Errno 2] No such file or directory: '{canary}'",
        f"read {canary} failed",
        canary.replace(SEP_NT, SEP_NT * 2),
        canary.replace(SEP_NT, "/"),
        json.dumps({"warning": f"Не удалось прочитать {canary}"}, ensure_ascii=False),
    ):
        _assert_clean(sanitize_diagnostic(text), canary)


def test_sanitizer_replaces_home_and_local_root() -> None:
    assert sanitize_diagnostic("/home/canary-user-142") == TRUNCATION_MARKER
    assert sanitize_diagnostic("~/private-root-142/x.json") == ".../private-root-142/x.json"
    assert "canary-user-142" not in sanitize_diagnostic("C:/Users/canary-user-142/x.json")


def test_sanitizer_keeps_relative_paths_and_codes() -> None:
    text = (
        f"error scanning {SAFE_RELATIVE}: boom "
        f"[code={SCAN_WARNING_FORM_SCAN_ERROR}] reason: {SAFE_CODE} ./rel/x a/b"
    )
    assert sanitize_diagnostic(text) == text


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_sanitizer_is_idempotent_and_deterministic(kind: str) -> None:
    text = f"failed: '{CANARIES[kind]}' [code={SCAN_WARNING_FORM_SCAN_ERROR}]"
    first = sanitize_diagnostic(text)
    assert sanitize_diagnostic(text) == first
    assert sanitize_diagnostic(first) == first
    assert scan_warning_code(first) == SCAN_WARNING_FORM_SCAN_ERROR


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_sanitizer_accepts_exception_objects(kind: str) -> None:
    canary = CANARIES[kind]
    error = _canary_error(canary)
    _assert_clean(sanitize_diagnostic(error), canary)
    _assert_clean(safe_error_text(error), canary)


def test_sanitizer_failure_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_paths, "_sanitize_text", _broken)
    for canary in CANARIES.values():
        assert sanitize_diagnostic(f"x {canary}") == SANITIZER_FAILED_TEXT


def test_unprintable_object_is_neutral() -> None:
    class Unprintable:
        def __str__(self) -> str:
            raise ValueError(POSIX_CANARY)

    assert sanitize_diagnostic(Unprintable()) == SANITIZER_FAILED_TEXT
    assert safe_error_text(Unprintable()) == SANITIZER_FAILED_TEXT


# --------------------------------------------------------------------------- #
# B. scan_warnings: public response × log × persisted artifact
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_scan_error_channels(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = CANARIES[kind]
    export_root = tmp_path / "export"
    _make_config_form(export_root)

    monkeypatch.setattr(sf, "_scan_form_dir", _raiser(_canary_error(canary)))
    with caplog.at_level(logging.WARNING, logger=sf.__name__):
        index = scan_forms(export_root)

    warnings = [w for w in index.scan_warnings if w.startswith("error scanning")]
    assert warnings
    for warning in warnings:
        _assert_clean(warning, canary)
        assert warning.startswith(f"error scanning {SAFE_RELATIVE}: ")
        assert scan_warning_code(warning) == SCAN_WARNING_FORM_SCAN_ERROR

    assert caplog.records
    for record in caplog.records:
        _assert_clean(record.getMessage(), canary)

    saved = tmp_path / "forms_scan_index.json"
    index.save(saved)
    _assert_clean(saved.read_text(encoding="utf-8"), canary)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_persisted_index_sanitizes_foreign_warnings(kind: str) -> None:
    canary = CANARIES[kind]
    index = FormScanIndex(scan_warnings=[f"legacy warning {canary}"])

    _assert_clean(json.dumps(index.to_dict(), ensure_ascii=False), canary)
    assert index.scan_warnings == [f"legacy warning {canary}"], "внутренние данные не меняются"


# --------------------------------------------------------------------------- #
# C. runner / report / CLI
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_runner_fatal_message_is_clean(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = CANARIES[kind]
    monkeypatch.setattr(runner, "scan_forms", _raiser(_canary_error(canary)))

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    fatal = outcome.report.fatal_error
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_SCAN_FAILED
    _assert_clean(outcome.report.to_json(), canary)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_exception_outside_serializer_passes_boundary(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = CANARIES[kind]
    export_root = tmp_path / "export"
    _make_config_form(export_root)

    # Формы уже обработаны, объекты сформированы: исключение возникает вне
    # стадийных обработчиков и вне сериализатора отчёта.
    monkeypatch.setattr(runner, "_process_common_modules", _raiser(_canary_error(canary)))
    outcome = runner.run_pipeline(runner.RunOptions(export_root=export_root))

    fatal = outcome.report.fatal_error
    assert outcome.completed is False
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_INTERNAL_ERROR
    _assert_clean(outcome.report.to_json(), canary)
    for warning in outcome.scan_warnings:
        _assert_clean(warning, canary)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_cli_report_and_output_are_clean(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = CANARIES[kind]
    export_root = tmp_path / "export"
    _make_config_form(export_root)
    report_path = tmp_path / "report.json"

    monkeypatch.setattr(runner, "_process_common_modules", _raiser(_canary_error(canary)))
    code = cli.main([str(export_root), "--report-path", str(report_path)])
    captured = capsys.readouterr()

    assert code == cli.EXIT_FATAL
    _assert_clean(report_path.read_text(encoding="utf-8"), canary)
    _assert_clean(captured.out, canary)
    _assert_clean(captured.err, canary)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_cli_unexpected_crash_has_no_traceback(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = CANARIES[kind]
    export_root = tmp_path / "export"
    export_root.mkdir()

    monkeypatch.setattr(cli, "run_pipeline", _raiser(_canary_error(canary)))
    code = cli.main([str(export_root), "--report-path", str(tmp_path / "report.json")])
    captured = capsys.readouterr()

    assert code == cli.EXIT_FATAL
    assert "internal_error" in captured.err
    assert "Traceback" not in captured.err
    _assert_clean(captured.err, canary)
    _assert_clean(captured.out, canary)


def test_sanitizer_failure_through_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой sanitizer: наружу только нейтральный текст, canary не выходит."""
    export_root = tmp_path / "export"
    _make_config_form(export_root)
    boom = _raiser(_canary_error(POSIX_CANARY))

    monkeypatch.setattr(sf, "_scan_form_dir", boom)
    monkeypatch.setattr(safe_paths, "_sanitize_text", _broken)

    persisted = scan_forms(export_root).to_dict()
    assert persisted["scan_warnings"]
    assert all(w == SANITIZER_FAILED_TEXT for w in persisted["scan_warnings"])
    _assert_clean(json.dumps(persisted, ensure_ascii=False), POSIX_CANARY)

    monkeypatch.setattr(runner, "scan_forms", boom)
    outcome = runner.run_pipeline(runner.RunOptions(export_root=export_root))
    fatal = outcome.report.fatal_error
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_SCAN_FAILED
    assert fatal.message in (None, SANITIZER_FAILED_TEXT)
    _assert_clean(outcome.report.to_json(), POSIX_CANARY)


# --------------------------------------------------------------------------- #
# D. LLM-фрагмент и #141
# --------------------------------------------------------------------------- #
def _context(warnings: list[str]) -> FormContext:
    entries = [
        {
            "scope": "element",
            "element": "Поле1",
            "data_path": None,
            "status": "unresolved",
            "reason": SAFE_CODE,
        },
        {
            "scope": "form",
            "element": None,
            "data_path": None,
            "status": "unknown_layout",
            "reason": "layout_not_recognized",
        },
    ]
    return FormContext(
        form_name="Form",
        container_name="CatalogForm",
        object_type="Catalog",
        object_name="Obj",
        bsl_text="// module",
        summary=FormSummary(warnings=warnings),
        metadata={},
        unresolved_data_paths=entries,
    )


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_llm_fragment_is_clean(kind: str) -> None:
    canary = CANARIES[kind]
    context = _context([f"Не удалось прочитать {canary}", f"форма {SAFE_RELATIVE}"])

    fragment = to_llm_prompt_fragment(context)

    _assert_clean(fragment, canary)
    assert SAFE_RELATIVE in fragment
    for limit in (1, 50, 200, len(fragment)):
        part = to_llm_prompt_fragment(context, max_chars=limit)
        assert len(part) <= limit
        assert fragment.startswith(part)


def test_issue141_lines_pass_boundary_unchanged() -> None:
    context = _context([f"Не удалось прочитать {POSIX_CANARY}"])
    fragment = to_llm_prompt_fragment(context)
    lines = [
        line for line in fragment.split("\n") if line.startswith(DATA_PATH_LINE_PREFIX)
    ]

    expected = [_data_path_status_line(entry) for entry in context.unresolved_data_paths]
    assert lines == expected
    assert lines == [sanitize_diagnostic(line) for line in lines]
    assert "status: unresolved; reason: binding_not_proven" in lines[0]
    assert "status: unknown_layout; reason: layout_not_recognized" in lines[1]
    assert all(entry["data_path"] is None for entry in context.unresolved_data_paths)
    assert context.summary.warnings == [f"Не удалось прочитать {POSIX_CANARY}"]
