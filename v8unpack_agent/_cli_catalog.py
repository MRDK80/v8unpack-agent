"""Машиночитаемый каталог CLI-контракта (issue #259).

Каталог собирается из фактических parser ``cli.build_parser`` и
``search_cli.build_parser`` и типизированных реестров этого модуля: exit
codes, побочные эффекты и коды ошибок JSON-режима (#258). Ручного списка
параметров нет — состав, типы, required, choices и defaults берутся из
``argparse``. Код ошибки разбора аргументов общий для всех команд и задан
один раз в ``usage_error``, а не в реестрах команд.

Модуль внутренний: публичный интерфейс — флаг ``--describe-cli`` команды
``v8unpack-agent-run`` (docs/cli_introspection.md).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from v8unpack_agent import cli, search_cli
from v8unpack_agent.runner import MODULE_GROUPS

CATALOG_SCHEMA_VERSION = "1.0"
CATALOG_KIND = "v8unpack-agent.cli-catalog"
DESCRIBE_FLAG = "--describe-cli"

#: Код процесса при ошибке разбора аргументов argparse — общий для всех команд.
USAGE_ERROR_EXIT_CODE = cli.EXIT_BAD_INPUT


@dataclass(frozen=True)
class ExitCodeEntry:
    """Код процесса команды и его смысл, кроме общей ошибки разбора."""

    code: int
    status: str
    condition: str


@dataclass(frozen=True)
class SideEffect:
    """Побочный эффект команды, привязанный к параметру parser."""

    action: str  # read | write
    parameter: str
    target: str


@dataclass(frozen=True)
class CommandSpec:
    """Типизированная запись реестра для одной конечной формы команды."""

    name: str
    invocation: tuple[str, ...]
    exit_codes: tuple[ExitCodeEntry, ...]
    side_effects: tuple[SideEffect, ...]
    json_mode: bool


_ROOT_INVALID = ExitCodeEntry(
    cli.EXIT_BAD_INPUT, "bad_input", "export_root не существует или не каталог"
)

COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="v8unpack-agent-run",
        invocation=("v8unpack-agent-run",),
        exit_codes=(
            ExitCodeEntry(cli.EXIT_OK, "ok", "все объекты обработаны полностью"),
            _ROOT_INVALID,
            ExitCodeEntry(cli.EXIT_DEGRADED, "degraded", "есть partial или failed объекты"),
            ExitCodeEntry(cli.EXIT_FATAL, "fatal", "фатальная ошибка пайплайна или внутренняя ошибка"),
            ExitCodeEntry(cli.EXIT_WRITE_ERROR, "write_error", "отчёт не записан"),
            ExitCodeEntry(
                cli.EXIT_FATAL_AND_WRITE_ERROR,
                "fatal_and_write_error",
                "фатальная ошибка и отказ записи отчёта",
            ),
        ),
        side_effects=(
            SideEffect("read", "export_root", "дерево выгрузки"),
            SideEffect("write", "--report-path", "JSON-отчёт run_report"),
        ),
        json_mode=True,
    ),
    CommandSpec(
        name="search_cli build",
        invocation=("python", "-m", "v8unpack_agent.search_cli", "build"),
        exit_codes=(
            ExitCodeEntry(cli.EXIT_OK, "ok", "снимок построен и записан"),
            _ROOT_INVALID,
            ExitCodeEntry(cli.EXIT_FATAL, "error", "сбой корпуса, эмбеддера или внутренняя ошибка"),
            ExitCodeEntry(cli.EXIT_WRITE_ERROR, "error", "snapshot_write_failed"),
        ),
        side_effects=(
            SideEffect("read", "export_root", "дерево выгрузки"),
            SideEffect("read", "--model", "каталог локальной модели"),
            SideEffect("write", "--snapshot", "снимок search_index"),
            SideEffect("write", "--object-snapshot", "снимок уровня объектов"),
            SideEffect("write", "--cache-dir", "кэш векторов"),
        ),
        json_mode=False,
    ),
    CommandSpec(
        name="search_cli query",
        invocation=("python", "-m", "v8unpack_agent.search_cli", "query"),
        exit_codes=(
            ExitCodeEntry(cli.EXIT_OK, "ok", "результат и контекст без замечаний"),
            _ROOT_INVALID,
            ExitCodeEntry(cli.EXIT_DEGRADED, "degraded", "индекс не готов, снимок непригоден или бюджет недостаточен"),
            ExitCodeEntry(cli.EXIT_FATAL, "error", "сбой корпуса, эмбеддера или внутренняя ошибка"),
        ),
        side_effects=(
            SideEffect("read", "export_root", "дерево выгрузки"),
            SideEffect("read", "--snapshot", "снимок search_index"),
            SideEffect("read", "--object-snapshot", "снимок уровня объектов"),
            SideEffect("read", "--model", "каталог локальной модели"),
            SideEffect("write", "--cache-dir", "кэш векторов"),
        ),
        json_mode=False,
    ),
)


#: Допустимые значения, которые проверяет код команды, а не ``choices``
#: argparse. Источник — тот же реестр, что использует проверка.
VALUE_REGISTRIES: dict[str, tuple[str, ...]] = {
    "--module-group": MODULE_GROUPS,
    "--skip-module-group": MODULE_GROUPS,
}


def _parsers() -> dict[str, argparse.ArgumentParser]:
    """Конечные parser каждой команды по имени из реестра."""
    result: dict[str, argparse.ArgumentParser] = {"v8unpack-agent-run": cli.build_parser()}
    root = search_cli.build_parser()
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in sorted(action.choices.items()):
                result[f"search_cli {name}"] = sub
    return result


def parser_commands() -> tuple[str, ...]:
    """Имена конечных команд, найденные обходом фактических parser."""
    return tuple(sorted(_parsers()))


def _type_name(action: argparse.Action) -> str:
    if action.nargs == 0:
        return "boolean"
    if action.type is Path:
        return "path"
    if action.type is int:
        return "integer"
    if action.type is float:
        return "number"
    return "string"


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple):
        return list(value)
    return value


def _choices(action: argparse.Action, name: str) -> list[str] | None:
    if action.choices is not None:
        return sorted(str(choice) for choice in action.choices)
    if name in VALUE_REGISTRIES:
        return sorted(VALUE_REGISTRIES[name])
    return None


def describe_parameters(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    """Параметры parser в машинной форме, без справочного текста."""
    params: list[dict[str, Any]] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        positional = not action.option_strings
        name = action.dest if positional else max(action.option_strings, key=len)
        params.append(
            {
                "name": name,
                "kind": "positional" if positional else "option",
                "required": bool(action.required) or positional,
                "type": _type_name(action),
                "repeatable": isinstance(action, argparse._AppendAction),
                "choices": _choices(action, name),
                "default": None if action.nargs == 0 else _json_value(action.default),
            }
        )
    return sorted(params, key=lambda item: (item["kind"] != "positional", item["name"]))


def build_catalog() -> dict[str, Any]:
    """Собрать каталог из фактических parser и реестров."""
    parsers = _parsers()
    commands = []
    for spec in sorted(COMMANDS, key=lambda item: item.name):
        commands.append(
            {
                "name": spec.name,
                "invocation": list(spec.invocation),
                "parameters": describe_parameters(parsers[spec.name]),
                "side_effects": [
                    {"action": e.action, "parameter": e.parameter, "target": e.target}
                    for e in spec.side_effects
                ],
                "exit_codes": [
                    {"code": e.code, "status": e.status, "condition": e.condition}
                    for e in sorted(spec.exit_codes, key=lambda item: item.code)
                ],
                "json_mode": (
                    {"flag": "--json", "error_codes": sorted(cli.JSON_ERROR_CODES)}
                    if spec.json_mode
                    else None
                ),
            }
        )
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "kind": CATALOG_KIND,
        "json_contract": {
            "kind": cli.JSON_KIND,
            "schema_version": cli.JSON_SCHEMA_VERSION,
        },
        "usage_error": {"exit_code": USAGE_ERROR_EXIT_CODE, "source": "argparse"},
        "commands": commands,
    }


def render_catalog() -> str:
    """Каталог одной JSON-строкой с завершающим переводом строки."""
    return json.dumps(build_catalog(), ensure_ascii=False, sort_keys=True) + "\n"
