"""Контракт примеров: блок категории в module docstring (issue #246)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"

REQUIRED_FIELDS = (
    "Категория:",
    "Входные данные:",
    "Ожидаемый результат:",
    "Поведение без данных:",
)


def _example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.py"))


def test_examples_directory_is_not_empty() -> None:
    assert _example_files(), "в examples/ не найдено ни одного .py"


@pytest.mark.parametrize(
    "path", _example_files(), ids=lambda p: p.name
)
def test_example_declares_run_contract(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree)
    assert docstring, f"{path.name}: отсутствует module docstring"
    missing = [field for field in REQUIRED_FIELDS if field not in docstring]
    assert not missing, f"{path.name}: в docstring нет полей {missing}"
