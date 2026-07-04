"""Тесты CLI-entrypoint для scan_forms (issue #24).

Проверяют поведение `python -m v8unpack_agent.scan_forms` до и после
реализации CLI-обвязки. На момент создания все тесты должны быть RED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def cf_export_root(tmp_path: Path) -> Path:
    """Минимальная структура cf_export с одной формой.

    Layout (4-уровневый)::

        cf_export/
          Catalog/
            Склад/
              CatalogForm/
                ФормаСписка/
                  CatalogForm.obj.bsl
                  CatalogForm.json
    """
    form_dir = tmp_path / "Catalog" / "Склад" / "CatalogForm" / "ФормаСписка"
    form_dir.mkdir(parents=True)
    (form_dir / "CatalogForm.obj.bsl").write_text(
        "// stub bsl", encoding="utf-8"
    )
    (form_dir / "CatalogForm.json").write_text(
        '{"stub": true}', encoding="utf-8"
    )
    return tmp_path


def _run_cli(*args: str) -> tuple[int, str, str]:
    """Запустить `python -m v8unpack_agent.scan_forms` с аргументами.

    Возвращает (returncode, stdout, stderr).
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "v8unpack_agent.scan_forms", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_cli_with_save_creates_index(cf_export_root: Path) -> None:
    """--save создаёт forms_index.json в root."""
    index_path = cf_export_root / "forms_index.json"
    assert not index_path.exists(), "файл не должен существовать до запуска"

    rc, stdout, stderr = _run_cli(str(cf_export_root), "--save")

    assert rc == 0, f"ожидали returncode=0, получили {rc}\nstderr: {stderr}"
    assert index_path.exists(), "forms_index.json должен быть создан"

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert "total" in data, "индекс должен содержать ключ 'total'"
    assert "forms" in data, "индекс должен содержать ключ 'forms'"
    assert "scanned_at" in data, "индекс должен содержать ключ 'scanned_at'"
    assert data["total"] >= 1, "должна найтись хотя бы одна форма"


def test_cli_without_save_no_file(cf_export_root: Path) -> None:
    """Без --save файл forms_index.json не создаётся, returncode == 0."""
    index_path = cf_export_root / "forms_index.json"

    rc, stdout, stderr = _run_cli(str(cf_export_root))

    assert rc == 0, f"ожидали returncode=0, получили {rc}\nstderr: {stderr}"
    assert not index_path.exists(), "forms_index.json не должен создаваться без --save"


def test_cli_prints_form_count(cf_export_root: Path) -> None:
    """Вывод содержит строку с количеством найденных форм."""
    rc, stdout, stderr = _run_cli(str(cf_export_root), "--save")

    assert rc == 0, f"returncode={rc}\nstderr: {stderr}"
    assert "Найдено форм:" in stdout, (
        f"stdout должен содержать 'Найдено форм:', получили: {stdout!r}"
    )


def test_cli_prints_save_confirmation(cf_export_root: Path) -> None:
    """При --save вывод содержит подтверждение сохранения."""
    rc, stdout, stderr = _run_cli(str(cf_export_root), "--save")

    assert rc == 0, f"returncode={rc}\nstderr: {stderr}"
    assert "Индекс сохранён:" in stdout, (
        f"stdout должен содержать 'Индекс сохранён:', получили: {stdout!r}"
    )


def test_cli_no_args_returns_error() -> None:
    """Вызов без обязательного аргумента root → ненулевой returncode."""
    rc, stdout, stderr = _run_cli()

    assert rc != 0, (
        "ожидали ненулевой returncode при отсутствии обязательного аргумента"
    )


def test_cli_nonexistent_root(tmp_path: Path) -> None:
    """Несуществующий root → returncode == 0 (best-effort), 0 форм, нет краша."""
    nonexistent = tmp_path / "no_such_dir"

    rc, stdout, stderr = _run_cli(str(nonexistent))

    assert rc == 0, f"returncode={rc}\nstderr: {stderr}"
    assert "Найдено форм: 0" in stdout, (
        f"должно быть 'Найдено форм: 0', получили: {stdout!r}"
    )
