"""CLI production runner: владелец exit code и записи отчёта (issue #198).

Разделение ответственности
--------------------------

:mod:`v8unpack_agent.runner` — чистая библиотека без побочных эффектов.
Здесь сосредоточено всё, что взаимодействует с окружением: разбор аргументов,
запись post-run report и отображение состояния прогона в exit code.

Политика exit code
-------------------

=====  ==========================================================
Код    Условие
=====  ==========================================================
0      Все объекты ``complete``; отчёт записан.
2      Ошибка аргументов или ``export_root`` не каталог.
3      Degraded: есть ``partial``/``failed``, отчёт записан.
4      Управляемая фатальная ошибка пайплайна.
5      Ошибка записи отчёта.
6      Фатальная ошибка пайплайна и ошибка записи одновременно.
=====  ==========================================================

Degraded считается неуспешным завершением процесса: неполный LLM-контекст
не должен выглядеть как успех для вызывающей автоматизации.

Отчёт обязателен
----------------

``--report-path`` — обязательный аргумент без скрытого значения по умолчанию.
Прогон без записи отчёта невозможен: каталог-родитель должен существовать
заранее, иначе writer бросает ``RunReportWriteError`` и процесс завершается
кодом 5.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from v8unpack_agent.run_report import RunReportWriteError, write_post_run_report
from v8unpack_agent.runner import RunOptions, RunOutcome, run_pipeline

__all__ = [
    "EXIT_BAD_INPUT",
    "EXIT_DEGRADED",
    "EXIT_FATAL",
    "EXIT_FATAL_AND_WRITE_ERROR",
    "EXIT_OK",
    "EXIT_WRITE_ERROR",
    "main",
]

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_DEGRADED = 3
EXIT_FATAL = 4
EXIT_WRITE_ERROR = 5
EXIT_FATAL_AND_WRITE_ERROR = 6

_PROGRAM = "v8unpack-agent-run"


def build_parser() -> argparse.ArgumentParser:
    """Собрать разборщик аргументов production-прогона."""
    parser = argparse.ArgumentParser(
        prog=_PROGRAM,
        description=(
            "Обработать распакованную выгрузку и записать post-run report."
        ),
    )
    parser.add_argument(
        "export_root",
        type=Path,
        help="корень уже распакованной выгрузки",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        required=True,
        help="путь к JSON-файлу отчёта; значения по умолчанию нет",
    )
    parser.add_argument(
        "--mode",
        choices=("config", "external"),
        default="config",
        help="режим сканирования форм",
    )
    parser.add_argument(
        "--skip-common-modules",
        action="store_true",
        help="не обнаруживать и не читать общие модули",
    )
    parser.add_argument(
        "--skip-skd",
        action="store_true",
        help="не извлекать артефакты СКД",
    )
    parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=-1,
        help="лимит длины промпт-фрагмента; -1 без ограничения",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Выполнить прогон и вернуть exit code без завершения процесса.

    Функция пригодна для вызова из тестов: она не вызывает ``sys.exit``,
    а ошибка разбора аргументов превращается в код возврата.
    """
    _configure_output()
    parser = build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code_from_system_exit(exc)

    export_root = Path(args.export_root)
    if not export_root.is_dir():
        print(
            f"{_PROGRAM}: корень выгрузки не найден или не является каталогом: "
            f"{export_root.name}",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT

    options = RunOptions(
        export_root=export_root,
        mode=args.mode,
        include_common_modules=not args.skip_common_modules,
        include_skd=not args.skip_skd,
        max_prompt_chars=args.max_prompt_chars,
    )

    outcome = run_pipeline(options)
    write_error = _write_report(outcome, Path(args.report_path))

    _print_summary(outcome, write_error=write_error)
    return _resolve_exit_code(outcome, write_error=write_error)


def _write_report(outcome: RunOutcome, report_path: Path) -> RunReportWriteError | None:
    """Записать отчёт; вернуть ошибку записи, если она произошла."""
    try:
        write_post_run_report(outcome.report, report_path)
    except RunReportWriteError as exc:
        print(f"{_PROGRAM}: не удалось записать отчёт: {exc}", file=sys.stderr)
        return exc
    return None


def _resolve_exit_code(
    outcome: RunOutcome,
    *,
    write_error: RunReportWriteError | None,
) -> int:
    """Отобразить состояние прогона в exit code.

    Одновременные фатальная ошибка и отказ записи получают отдельный код:
    иначе вызывающая автоматизация не смогла бы отличить единичный отказ
    от полной потери наблюдаемости.
    """
    if not outcome.completed and write_error is not None:
        return EXIT_FATAL_AND_WRITE_ERROR
    if write_error is not None:
        return EXIT_WRITE_ERROR
    if not outcome.completed:
        return EXIT_FATAL
    if outcome.degraded:
        return EXIT_DEGRADED
    return EXIT_OK


def _print_summary(
    outcome: RunOutcome,
    *,
    write_error: RunReportWriteError | None,
) -> None:
    """Напечатать краткую сводку без путей и доменных данных."""
    summary = outcome.report.summary
    print(
        "обнаружено: "
        f"{summary.found}, complete: {summary.complete}, "
        f"partial: {summary.partial}, failed: {summary.failed}, "
        f"excluded: {summary.excluded}"
    )
    print(f"промпт-символов: {outcome.prompt_chars}")
    print(f"предупреждений сканера: {len(outcome.scan_warnings)}")

    fatal_error = outcome.report.fatal_error
    if fatal_error is not None:
        print(
            f"фатальная ошибка: {fatal_error.reason_code} "
            f"({fatal_error.error_type})",
            file=sys.stderr,
        )

    if write_error is None:
        print("отчёт записан")


def _exit_code_from_system_exit(exc: SystemExit) -> int:
    """Преобразовать ``SystemExit`` argparse в целочисленный код.

    ``--help`` и ``--version`` завершаются кодом 0, ошибка аргументов — кодом 2.
    """
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    return EXIT_BAD_INPUT


def _configure_output() -> None:
    """Перевести стандартные потоки в UTF-8 для Windows-консоли и CI."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
