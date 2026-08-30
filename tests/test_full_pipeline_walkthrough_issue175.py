"""Интеграционные тесты сквозного примера публичного API — issue #175.

Проверяется не форматирование, а контракт walkthrough: детерминированные
структурные статусы, явная неприменимость шагов вместо падения и
обезличенность stdout. Production-код не импортируется напрямую: пример
запускается как отдельный процесс, ровно так, как его запускает пользователь.

Synthetic root собирается существующими хелперами ``tests/_managed_fixtures``
(``make_managed_form_elem_json`` / ``write_managed_form_elem``), второй
большой fixture полного layout не создаётся. Раскладка общих модулей взята из
доказанного контракта #151: ``CommonModule/{Name}/CommonModule.obj.bsl``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _managed_fixtures import (  # bootstrap sys.path выполнен выше
    make_managed_form_elem_json,
    write_managed_form_elem,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "full_pipeline_walkthrough.py"

OBJECT_TYPE = "Catalog"
OBJECT_NAME = "Альфа"
FORM_NAME = "ФормаЭлемента"
CONTAINER = "CatalogForm"
COMMON_MODULE_NAME = "Бета"

BSL_MARKER = "СинтетическийМаркерBSL"
QUERY_MARKER = "ВЫБРАТЬ СинтетическийЗапросСКД"
BSL_TEXT = f"// {BSL_MARKER}\nПроцедура ПриСозданииНаСервере()\n// {QUERY_MARKER}\nКонецПроцедуры\n"
COMMON_MODULE_BSL = f"// {BSL_MARKER}\nФункция Сложить(А, Б) Экспорт\nВозврат А + Б;\nКонецФункции\n"

ALLOWED_STATUSES = {"ok", "degraded", "not_applicable", "skipped"}
STEP_COUNT = 11


def build_export(root: Path, *, with_elem: bool = True, with_common: bool = False) -> Path:
    """Собрать минимальную синтетическую выгрузку config-layout."""
    payload = make_managed_form_elem_json(with_noise=False)
    elem_json = write_managed_form_elem(
        root,
        OBJECT_TYPE,
        OBJECT_NAME,
        FORM_NAME,
        payload,
    )
    form_dir = elem_json.parent
    (form_dir / f"{CONTAINER}.obj.bsl").write_text(BSL_TEXT, encoding="utf-8")
    if not with_elem:
        elem_json.unlink()

    if with_common:
        module_dir = root / "CommonModule" / COMMON_MODULE_NAME
        module_dir.mkdir(parents=True, exist_ok=True)
        (module_dir / "CommonModule.obj.bsl").write_text(
            COMMON_MODULE_BSL, encoding="utf-8"
        )
    return root


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def parse_steps(stdout: str) -> dict[int, dict[str, str]]:
    """Разобрать вывод в {номер шага: {ключ: значение}}."""
    steps: dict[int, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and "/" in line and "]" in line:
            number = int(line[1 : line.index("/")])
            current = {}
            steps[number] = current
            continue
        if current is not None and ": " in line:
            key, value = line.split(": ", 1)
            current[key] = value
    return steps


@pytest.fixture
def export_root(tmp_path: Path) -> Path:
    return build_export(tmp_path / "cf_export")


def test_help_exits_zero() -> None:
    result = run_script("--help")
    assert result.returncode == 0
    assert "EXPORT_ROOT" in result.stdout


def test_missing_root_fails_without_traceback(tmp_path: Path) -> None:
    result = run_script(str(tmp_path / "absent"))
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()


def test_script_compiles() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_synthetic_export_passes_end_to_end(export_root: Path) -> None:
    result = run_script(str(export_root))
    assert result.returncode == 0, result.stderr
    steps = parse_steps(result.stdout)
    assert sorted(steps) == list(range(1, STEP_COUNT + 1))
    assert all(step["status"] in ALLOWED_STATUSES for step in steps.values())
    assert steps[1]["status"] == "ok"
    assert int(steps[1]["forms"]) >= 1
    assert steps[3]["has_drift"] == "false"


def test_temporary_index_is_not_written_into_export(export_root: Path) -> None:
    result = run_script(str(export_root))
    steps = parse_steps(result.stdout)
    assert steps[2]["saved_inside_temp_dir"] == "true"
    assert steps[2]["saved_inside_export"] == "false"
    assert not list(export_root.glob("*.json"))


def test_owner_step_not_applicable_does_not_stop_pipeline(export_root: Path) -> None:
    result = run_script(str(export_root))
    steps = parse_steps(result.stdout)
    assert steps[5]["status"] == "not_applicable"
    assert steps[8]["status"] == "ok"
    assert steps[9]["status"] in ALLOWED_STATUSES
    assert 11 in steps


def test_missing_elem_json_is_not_applicable(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_elem=False)
    result = run_script(str(root))
    assert result.returncode == 0, result.stderr
    steps = parse_steps(result.stdout)
    assert steps[6]["status"] == "not_applicable"
    assert steps[6]["reason"] == "elem_json_missing"
    assert steps[7]["status"] == "not_applicable"


def test_absent_skd_gives_zero_aggregate(export_root: Path) -> None:
    result = run_script(str(export_root))
    steps = parse_steps(result.stdout)
    assert steps[10]["status"] == "not_applicable"
    assert steps[10]["batch_results"] == "0"
    assert steps[10]["queries_total"] == "0"


def test_absent_common_modules_gives_zero_aggregate(export_root: Path) -> None:
    result = run_script(str(export_root))
    steps = parse_steps(result.stdout)
    assert steps[11]["status"] == "not_applicable"
    assert steps[11]["modules_total"] == "0"


def test_common_module_branch_is_separate(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_common=True)
    result = run_script(str(root))
    assert result.returncode == 0, result.stderr
    steps = parse_steps(result.stdout)
    assert steps[11]["status"] == "ok"
    assert steps[11]["modules_total"] == "1"
    assert steps[11]["ok"] == "1"
    assert steps[11]["read_error"] == "0"
    assert steps[11]["demo_read_status"] == "ok"
    assert int(steps[11]["demo_bsl_chars"]) == len(COMMON_MODULE_BSL)


def test_skip_flags_report_skipped(export_root: Path) -> None:
    result = run_script(str(export_root), "--skip-skd", "--skip-common-modules")
    assert result.returncode == 0, result.stderr
    steps = parse_steps(result.stdout)
    assert steps[10]["status"] == "skipped"
    assert steps[11]["status"] == "skipped"
    assert sorted(steps) == list(range(1, STEP_COUNT + 1))


def test_stdout_has_no_absolute_paths(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_common=True)
    result = run_script(str(root))
    assert str(root) not in result.stdout
    assert str(tmp_path) not in result.stdout
    assert "v8unpack_walkthrough_" not in result.stdout


def test_stdout_has_no_bsl_or_query_text(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_common=True)
    result = run_script(str(root))
    assert BSL_MARKER not in result.stdout
    assert QUERY_MARKER not in result.stdout
    assert "Процедура" not in result.stdout
    assert "Экспорт" not in result.stdout


def test_stdout_has_no_local_names(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_common=True)
    result = run_script(str(root))
    assert OBJECT_NAME not in result.stdout
    assert FORM_NAME not in result.stdout
    assert COMMON_MODULE_NAME not in result.stdout
    assert CONTAINER not in result.stdout


def test_repeated_run_is_deterministic(tmp_path: Path) -> None:
    root = build_export(tmp_path / "cf_export", with_common=True)
    first = parse_steps(run_script(str(root)).stdout)
    second = parse_steps(run_script(str(root)).stdout)
    assert first == second


def test_max_prompt_chars_limits_fragment(export_root: Path) -> None:
    unlimited = parse_steps(run_script(str(export_root)).stdout)
    limited = parse_steps(run_script(str(export_root), "--max-prompt-chars", "64").stdout)
    assert int(limited[8]["prompt_chars"]) <= int(unlimited[8]["prompt_chars"])
    assert int(limited[8]["prompt_chars"]) > 0
