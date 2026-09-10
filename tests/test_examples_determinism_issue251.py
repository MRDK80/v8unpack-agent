"""Контрактный тест issue #251: детерминизм вывода самодостаточных примеров.

Каждый пример запускается дважды в отдельном процессе. Проверяется, что
stdout побайтово одинаков и не содержит имён временных каталогов,
абсолютных путей и литеральных Windows-разделителей.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"

SELF_CONTAINED = (
    "basic_usage.py",
    "chain_form_bindings.py",
    "coverage_metric.py",
    "form_bindings.py",
    "form_context.py",
    "reference_types.py",
    "zero_binding_reasons.py",
    "unindexed_forms_report.py",
)

CASES: list[tuple[str, tuple[str, ...]]] = [(name, ()) for name in SELF_CONTAINED]
CASES.append(("reference_only_compare.py", ("--selftest",)))

IDS = [name if not args else f"{name} {' '.join(args)}" for name, args in CASES]

TMP_DIR_PATTERNS = (
    re.compile(r"/tmp/tmp[0-9A-Za-z_]{6,}"),
    re.compile(r"/var/folders/"),
    re.compile(r"[Tt]emp[\\/]+tmp[0-9A-Za-z_]{6,}"),
    re.compile(r"\btmp[0-9a-z]{8}\b"),
)
ABSOLUTE_POSIX = re.compile(r"(?<![\w.])/(?:tmp|home|var|usr|Users|private)(?:/|\b)")
DRIVE_LETTER = re.compile(r"\b[A-Za-z]:[\\/]")
WINDOWS_SEP = re.compile(r"[^\s\"']*\\[^\s\"']+")

_CACHE: dict[tuple[str, tuple[str, ...]], tuple[str, str]] = {}


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONHASHSEED"] = "0"
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        env.pop(name, None)
    env["LANG"] = "C.UTF-8"
    return env


def _run_twice(name: str, args: tuple[str, ...]) -> tuple[str, str]:
    key = (name, args)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    script = EXAMPLES_DIR / name
    assert script.is_file(), f"пример не найден: {script}"

    outputs: list[str] = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(REPO_ROOT),
            env=_child_env(),
            capture_output=True,
            timeout=600,
            check=False,
        )
        assert proc.returncode == 0, (
            f"{name} завершился с RC={proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:500]}"
        )
        outputs.append(proc.stdout.decode("utf-8"))

    _CACHE[key] = (outputs[0], outputs[1])
    return _CACHE[key]


def _first_difference(first: str, second: str) -> str:
    left = first.splitlines()
    right = second.splitlines()
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else "<нет строки>"
        b = right[index] if index < len(right) else "<нет строки>"
        if a != b:
            return f"строка {index + 1}:\n  run1: {a}\n  run2: {b}"
    return "длина вывода различается"


@pytest.mark.parametrize(("name", "args"), CASES, ids=IDS)
def test_output_is_byte_identical(name: str, args: tuple[str, ...]) -> None:
    first, second = _run_twice(name, args)
    assert first == second, (
        f"{name}: два запуска дали разный stdout\n{_first_difference(first, second)}"
    )


@pytest.mark.parametrize(("name", "args"), CASES, ids=IDS)
def test_output_has_no_temp_dir_names(name: str, args: tuple[str, ...]) -> None:
    first, _ = _run_twice(name, args)
    for pattern in TMP_DIR_PATTERNS:
        match = pattern.search(first)
        assert match is None, (
            f"{name}: в stdout попало имя временного каталога: {match.group(0)!r}"
        )


@pytest.mark.parametrize(("name", "args"), CASES, ids=IDS)
def test_output_has_no_absolute_paths(name: str, args: tuple[str, ...]) -> None:
    first, _ = _run_twice(name, args)
    posix_hit = ABSOLUTE_POSIX.search(first)
    assert posix_hit is None, (
        f"{name}: в stdout попал абсолютный путь: {posix_hit.group(0)!r}"
    )
    drive_hit = DRIVE_LETTER.search(first)
    assert drive_hit is None, (
        f"{name}: в stdout попал путь с буквой диска: {drive_hit.group(0)!r}"
    )


@pytest.mark.parametrize(("name", "args"), CASES, ids=IDS)
def test_output_has_no_windows_separators(name: str, args: tuple[str, ...]) -> None:
    first, _ = _run_twice(name, args)
    match = WINDOWS_SEP.search(first)
    assert match is None, (
        f"{name}: в stdout попал литеральный Windows-разделитель: {match.group(0)!r}"
    )
