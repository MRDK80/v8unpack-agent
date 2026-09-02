"""Сценарии production runner и политики exit code (issue #198).

Тесты не требуют платформы 1С и не вызывают распаковку: все входные
деревья собираются во временном каталоге, а фатальные отказы вносятся
подменой граничных функций в пространстве имён модуля runner.

Форматтер предупреждений сканера относится к его внутреннему API, поэтому
предупреждение строится через доступный форматтер, а при его отсутствии —
по документированному формату маркера. Корректность конструкции проверяется
публичной функцией ``scan_warning_code``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from v8unpack_agent import cli, runner, scan_forms
from v8unpack_agent.run_report import (
    ObjectRunResult,
    PostRunReport,
    RunFatalError,
    RunObjectKind,
    RunObjectStatus,
    RunSummary,
)

MACHINE_CODE_PATTERN = r"[a-z][a-z0-9_]*"
WINDOWS_SEPARATOR = chr(92)
FALLBACK_SCAN_CODES = (
    "FORM_MODULE_MISSING",
    "FORM_SCAN_ERROR",
    "SCAN_ROOT_INVALID",
)


def _known_scan_codes() -> Iterable[str]:
    """Получить набор кодов предупреждений сканера."""
    for name in ("SCAN_WARNING_CODES", "_SCAN_WARNING_CODES"):
        codes = getattr(scan_forms, name, None)
        if codes:
            return sorted(codes)
    return FALLBACK_SCAN_CODES


def _warning_with_code(code: str) -> str:
    """Собрать текст предупреждения с машинным кодом внутри."""
    for name in ("format_scan_warning", "_format_scan_warning"):
        formatter = getattr(scan_forms, name, None)
        if formatter is not None:
            return str(formatter(code, "sample message"))
    return f"sample message [code={code}]"


def _make_config_export(root: Path) -> Path:
    """Создать минимальное дерево выгрузки с одной формой."""
    form_dir = root / "Catalog" / "SampleObject" / "ItemForm" / "Variant1"
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_text("// module\n", encoding="utf-8")
    return form_dir


def _make_common_module_export(root: Path) -> Path:
    """Создать дерево выгрузки с одним общим модулем."""
    module_dir = root / "CommonModule" / "SharedHelpers"
    module_dir.mkdir(parents=True)
    (module_dir / "CommonModule.obj.bsl").write_text(
        "// shared\n",
        encoding="utf-8",
    )
    return module_dir


def _fake_outcome(*, completed: bool) -> runner.RunOutcome:
    """Собрать детерминированный результат для проверки exit code."""
    stamp = runner._utc_now()
    fatal = (
        None
        if completed
        else RunFatalError(
            reason_code=runner.FATAL_SCAN_FAILED,
            error_type="runtime_error",
        )
    )
    report = PostRunReport(
        schema_version=runner.SCHEMA_VERSION,
        completed=completed,
        started_at=stamp,
        finished_at=stamp,
        summary=RunSummary.from_objects(()),
        objects=(),
        fatal_error=fatal,
    )
    return runner.RunOutcome(report=report)


def test_empty_export_completes_without_objects(tmp_path: Path) -> None:
    """Пустой корень даёт успешный прогон без объектов."""
    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    assert outcome.completed is True
    assert outcome.degraded is False
    assert outcome.report.objects == ()
    assert outcome.report.summary.found == 0
    assert outcome.report.fatal_error is None


def test_config_form_run_is_degraded(tmp_path: Path) -> None:
    """Форма без elem-индекса попадает в отчёт как неполная."""
    _make_config_export(tmp_path)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    report = outcome.report

    assert report.completed is True
    assert outcome.degraded is True
    assert report.summary.found == 1

    item = report.objects[0]
    assert item.object == "Catalog/SampleObject/ItemForm/Variant1"
    assert item.object_kind is RunObjectKind.FORM
    assert item.status is not RunObjectStatus.COMPLETE
    assert item.stage in {runner.STAGE_BUILD_CONTEXT, runner.STAGE_PARSE_ELEM}
    assert re.fullmatch(MACHINE_CODE_PATTERN, item.reason_code or "")


def test_object_ids_are_portable(tmp_path: Path) -> None:
    """Идентификаторы объектов не содержат признаков конкретной ОС."""
    _make_config_export(tmp_path)
    _make_common_module_export(tmp_path)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    assert outcome.report.objects
    for item in outcome.report.objects:
        assert not item.object.startswith("/")
        assert WINDOWS_SEPARATOR not in item.object
        assert ":" not in item.object


def test_common_module_complete_has_no_stage(tmp_path: Path) -> None:
    """Успешный общий модуль не несёт деталей деградации."""
    _make_common_module_export(tmp_path)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    modules = [
        item
        for item in outcome.report.objects
        if item.object_kind is RunObjectKind.COMMON_MODULE
    ]

    assert len(modules) == 1
    assert modules[0].status is RunObjectStatus.COMPLETE
    assert modules[0].stage is None
    assert modules[0].reason_code is None
    assert modules[0].message is None


def test_skip_common_modules_removes_objects(tmp_path: Path) -> None:
    """Пропуск общих модулей убирает их из отчёта."""
    _make_common_module_export(tmp_path)

    options = runner.RunOptions(
        export_root=tmp_path,
        include_common_modules=False,
    )
    outcome = runner.run_pipeline(options)

    assert all(
        item.object_kind is not RunObjectKind.COMMON_MODULE
        for item in outcome.report.objects
    )


def test_max_prompt_chars_zero_yields_no_chars(tmp_path: Path) -> None:
    """Нулевой лимит промпта не накапливает символы."""
    _make_config_export(tmp_path)

    options = runner.RunOptions(export_root=tmp_path, max_prompt_chars=0)
    outcome = runner.run_pipeline(options)

    assert outcome.prompt_chars == 0


def test_run_is_deterministic(tmp_path: Path) -> None:
    """Повторный прогон даёт идентичные результаты объектов."""
    _make_config_export(tmp_path)
    _make_common_module_export(tmp_path)

    first = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    second = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    assert first.report.objects == second.report.objects
    assert first.report.summary == second.report.summary


def test_timestamps_are_utc_with_z(tmp_path: Path) -> None:
    """Отметки времени — UTC с суффиксом Z и разбираются ISO-8601."""
    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    for stamp in (outcome.report.started_at, outcome.report.finished_at):
        assert stamp.endswith("Z")
        parsed = datetime.fromisoformat(f"{stamp[:-1]}+00:00")
        assert parsed.tzinfo is not None


def test_scan_failure_is_managed_fatal(tmp_path: Path, monkeypatch) -> None:
    """Отказ стадии сканирования становится управляемым fatal_error."""

    def raiser(*args: object, **kwargs: object) -> object:
        raise RuntimeError("scan stage failed")

    monkeypatch.setattr(runner, "scan_forms", raiser)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    fatal = outcome.report.fatal_error

    assert outcome.completed is False
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_SCAN_FAILED
    assert fatal.error_type == "runtime_error"
    assert fatal.message == "scan stage failed"


def test_common_modules_failure_is_managed_fatal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Отказ обнаружения общих модулей — тоже управляемый fatal."""

    def raiser(*args: object, **kwargs: object) -> object:
        raise NotADirectoryError("container is a file")

    monkeypatch.setattr(runner, "scan_common_modules", raiser)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    fatal = outcome.report.fatal_error

    assert outcome.completed is False
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_COMMON_MODULES_FAILED
    assert re.fullmatch(MACHINE_CODE_PATTERN, fatal.error_type)
    assert "directory" in fatal.error_type


def test_skd_failure_is_managed_fatal(tmp_path: Path, monkeypatch) -> None:
    """Отказ извлечения СКД не выходит из runner исключением."""

    def raiser(*args: object, **kwargs: object) -> object:
        raise ValueError("skd stage failed")

    monkeypatch.setattr(runner, "extract_all_skd_queries", raiser)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    fatal = outcome.report.fatal_error

    assert outcome.completed is False
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_SKD_FAILED
    assert fatal.error_type == "value_error"


def test_object_error_does_not_leak_traceback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ошибка объекта не срывает прогон и не приносит traceback в JSON."""
    _make_config_export(tmp_path)

    def raiser(*args: object, **kwargs: object) -> object:
        raise RuntimeError('File "module.py", line 1')

    monkeypatch.setattr(runner, "build_form_context", raiser)

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))
    item = outcome.report.objects[0]
    payload = outcome.report.to_json()

    assert outcome.completed is True
    assert item.status is RunObjectStatus.FAILED
    assert item.stage == runner.STAGE_BUILD_CONTEXT
    assert item.reason_code == runner.REASON_CONTEXT_BUILD_ERROR
    assert item.message is None
    assert "Traceback" not in payload
    assert 'File "' not in payload


def test_scan_warning_codes_are_normalised() -> None:
    """Все коды сканера пригодны для отчёта после нормализации."""
    codes = list(_known_scan_codes())
    assert codes

    for code in codes:
        warning = _warning_with_code(code)
        assert scan_forms.scan_warning_code(warning) == code

        reason_code = runner._scan_reason_code(warning)
        assert reason_code == code.lower()
        assert re.fullmatch(MACHINE_CODE_PATTERN, reason_code)

        result = ObjectRunResult(
            object="Catalog/SampleObject/ItemForm/Variant1",
            object_kind=RunObjectKind.FORM,
            status=RunObjectStatus.PARTIAL,
            stage=runner.STAGE_SCAN,
            reason_code=reason_code,
        )
        assert result.reason_code == reason_code


def test_legacy_scan_warning_uses_fallback_code() -> None:
    """Предупреждение без маркера получает fallback-код."""
    assert (
        runner._scan_reason_code("legacy warning without marker")
        == runner.REASON_SCAN_WARNING_UNCLASSIFIED
    )
    assert (
        runner._scan_reason_code("broken marker [code=NOT_A_KNOWN_CODE]")
        == runner.REASON_SCAN_WARNING_UNCLASSIFIED
    )


def test_machine_code_rejects_invalid_value() -> None:
    """Некорректное значение заменяется fallback-кодом."""
    assert runner._machine_code("good_code", "fallback") == "good_code"
    assert runner._machine_code("BAD_CODE", "fallback") == "fallback"
    assert runner._machine_code("1_leading_digit", "fallback") == "fallback"
    assert runner._machine_code(None, "fallback") == "fallback"


def test_safe_object_id_strips_roots() -> None:
    """Корневой префикс и разделители NT удаляются."""
    windows_value = f"C:{WINDOWS_SEPARATOR}dir{WINDOWS_SEPARATOR}ItemForm"

    assert runner._safe_object_id(windows_value) == "dir/ItemForm"
    assert runner._safe_object_id("/srv/dir/ItemForm") == "srv/dir/ItemForm"
    assert runner._safe_object_id("dir/ItemForm") == "dir/ItemForm"
    assert runner._safe_object_id("   ") == "unknown_object"


def test_safe_message_is_single_line_and_bounded() -> None:
    """Сообщение сводится к одной ограниченной строке."""
    multiline = runner._safe_message(RuntimeError("first\nsecond\tthird"))
    assert multiline == "first second third"

    long_message = runner._safe_message(RuntimeError("x" * 400))
    assert long_message is not None
    assert len(long_message) == 180

    assert runner._safe_message(None) is None


def test_error_type_is_machine_code() -> None:
    """Имя класса исключения превращается в машинный код."""
    assert runner._error_type(ValueError("x")) == "value_error"
    assert runner._error_type(OSError("x")) == "os_error"
    assert re.fullmatch(
        MACHINE_CODE_PATTERN,
        runner._error_type(NotADirectoryError("x")),
    )


def test_cli_success_writes_report(tmp_path: Path) -> None:
    """Успешный прогон даёт код 0 и валидный UTF-8 JSON."""
    export_root = tmp_path / "export"
    export_root.mkdir()
    report_path = tmp_path / "report.json"

    code = cli.main([str(export_root), "--report-path", str(report_path)])
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert code == cli.EXIT_OK
    assert payload["schema_version"] == 1
    assert payload["run"]["completed"] is True
    assert payload["summary"]["found"] == 0
    assert payload["objects"] == []
    assert payload["fatal_error"] is None


def test_cli_degraded_exit_code(tmp_path: Path) -> None:
    """Degraded прогон завершается кодом 3, отчёт записан."""
    export_root = tmp_path / "export"
    _make_config_export(export_root)
    report_path = tmp_path / "report.json"

    code = cli.main([str(export_root), "--report-path", str(report_path)])

    assert code == cli.EXIT_DEGRADED
    assert report_path.is_file()


def test_cli_missing_report_path_is_argument_error(tmp_path: Path) -> None:
    """Без --report-path прогон невозможен."""
    export_root = tmp_path / "export"
    export_root.mkdir()

    assert cli.main([str(export_root)]) == cli.EXIT_BAD_INPUT


def test_cli_missing_export_root_is_argument_error(tmp_path: Path) -> None:
    """Несуществующий корень даёт код 2 и не создаёт отчёт."""
    report_path = tmp_path / "report.json"

    code = cli.main(
        [str(tmp_path / "absent"), "--report-path", str(report_path)]
    )

    assert code == cli.EXIT_BAD_INPUT
    assert not report_path.exists()


def test_cli_write_error_exit_code(tmp_path: Path) -> None:
    """Отсутствующий каталог отчёта даёт код 5."""
    export_root = tmp_path / "export"
    export_root.mkdir()
    report_path = tmp_path / "absent" / "report.json"

    code = cli.main([str(export_root), "--report-path", str(report_path)])

    assert code == cli.EXIT_WRITE_ERROR
    assert not report_path.exists()


def test_cli_fatal_exit_code(tmp_path: Path, monkeypatch) -> None:
    """Фатальная ошибка с записанным отчётом даёт код 4."""
    export_root = tmp_path / "export"
    export_root.mkdir()
    report_path = tmp_path / "report.json"

    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda options: _fake_outcome(completed=False),
    )

    code = cli.main([str(export_root), "--report-path", str(report_path)])
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert code == cli.EXIT_FATAL
    assert payload["run"]["completed"] is False
    assert payload["fatal_error"]["reason_code"] == runner.FATAL_SCAN_FAILED


def test_cli_fatal_and_write_error_exit_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Фатальная ошибка вместе с отказом записи даёт код 6."""
    export_root = tmp_path / "export"
    export_root.mkdir()
    report_path = tmp_path / "absent" / "report.json"

    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda options: _fake_outcome(completed=False),
    )

    code = cli.main([str(export_root), "--report-path", str(report_path)])

    assert code == cli.EXIT_FATAL_AND_WRITE_ERROR
    assert not report_path.exists()


def test_cli_help_returns_ok() -> None:
    """Справка возвращает нулевой код и не завершает процесс."""
    assert cli.main(["--help"]) == cli.EXIT_OK


def test_exit_code_from_system_exit_variants() -> None:
    """SystemExit любого вида превращается в целочисленный код."""
    assert cli._exit_code_from_system_exit(SystemExit(None)) == cli.EXIT_OK
    assert cli._exit_code_from_system_exit(SystemExit(2)) == cli.EXIT_BAD_INPUT
    assert (
        cli._exit_code_from_system_exit(SystemExit("message"))
        == cli.EXIT_BAD_INPUT
    )


def test_resolve_exit_code_matrix() -> None:
    """Таблица exit code покрыта явно."""
    success = _fake_outcome(completed=True)
    fatal = _fake_outcome(completed=False)

    assert cli._resolve_exit_code(success, write_error=None) == cli.EXIT_OK
    assert cli._resolve_exit_code(fatal, write_error=None) == cli.EXIT_FATAL


def test_console_script_is_registered() -> None:
    """Production entry point объявлен в метаданных пакета."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    assert "[project.scripts]" in text
    assert 'v8unpack-agent-run = "v8unpack_agent.cli:main"' in text
