"""Пример examples/search_export.py на синтетических выгрузках (#404).

Пример запускается отдельным процессом, как пользователь: проверяются код
возврата, состав и обезличенность вывода, детерминированность и
неизменность выгрузки. Ветвь «выгрузка изменилась» проверяется вызовом
``main`` с подменой построения отчёта.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "search_export.py"

CONFIG_FILES = {
    "Catalog/Товары/Catalog.obj.bsl": "Процедура ПередЗаписью(Отказ)\n  ЦенаТовара = 1;\nКонецПроцедуры\n",
    "Catalog/Товары/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриОткрытии()\n"
        + "".join(f"  ЦенаТовара{i} = {i};\n" for i in range(40))
        + "КонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.json": "{}",
    "Document/Заказ/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриСозданииНаСервере()\n  СуммаЗаказа = 0;\nКонецПроцедуры\n"
    ),
    "Document/Заказ/Form/ФормаЭлемента/Form.json": "{}",
    "CommonForm/ОбщаяФормаВыбора/CommonForm.obj.bsl": (
        "Процедура ВыбратьЗначение()\n  ВыбранноеЗначение = 1;\nКонецПроцедуры\n"
    ),
    "CommonForm/ОбщаяФормаВыбора/CommonForm.json": "{}",
    "CommonModule/Общий/CommonModule.obj.bsl": "Процедура ОбщаяПроцедура() Экспорт\nКонецПроцедуры\n",
}
EXTERNAL_FILES = {
    "External/ЗагрузкаЦен.epf/ExternalDataProcessor.json": json.dumps({"name": "ЗагрузкаЦен"}),
    "External/ЗагрузкаЦен.epf/ExternalDataProcessor.obj.bsl": "Процедура Загрузить() Экспорт\nКонецПроцедуры\n",
    "External/ЗагрузкаЦен.epf/Form/ФормаЗагрузки/Form.obj.bsl": (
        "Процедура КомандаЗагрузить()\n  ЗагрузитьЦены = Истина;\nКонецПроцедуры\n"
    ),
    "External/ЗагрузкаЦен.epf/Form/ФормаЗагрузки/Form.json": "{}",
}
SECRET = "secret-export-404"
ABSOLUTE = re.compile(r"(?<![\w.])/(?:tmp|home|var|usr|Users|private)(?:/|\b)|\b[A-Za-z]:[\\/]")


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONHASHSEED"] = "0"
    return env


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT), env=_env(), capture_output=True, text=True,
        encoding="utf-8", timeout=600, check=False,
    )


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("search_export_example", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("files", "mode", "kinds"),
    [(CONFIG_FILES, "config", {"form", "module", "owner"}),
     (EXTERNAL_FILES, "external", {"form", "module", "owner"})],
    ids=["config", "external"],
)
def test_example_runs_on_export(
    tmp_path: Path, files: dict[str, str], mode: str, kinds: set[str]
) -> None:
    root = _write(tmp_path / SECRET, files)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    args = (str(root),) if mode == "config" else (str(root), "--mode", mode)
    first, second = _run(*args), _run(*args)
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    report: dict[str, Any] = json.loads(first.stdout)
    assert report["checks"] == "ok"
    assert report["export_unchanged"] is True
    assert report["export_changes"] == {}
    assert report["package_source"] == "this_repo"
    assert report["mode"] == mode
    assert {key.split(":")[0] for key in report["corpus"]} == kinds
    assert report["sample"] > 0
    assert report["exact_by_id"]["other"] == 0
    assert report["exact_by_name"]["other"] == 0
    assert report["semantic_demo"]["other"] == 0
    assert report["snapshot"]["entries"] == report["fragments"]
    assert report["context_budget"]["within_budget"] == report["sample"]
    for text in (first.stdout, first.stderr):
        assert SECRET not in text
        assert str(tmp_path) not in text
        assert not ABSOLUTE.search(text)
        assert "\\" not in text
        assert text.isascii()
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_missing_arguments_and_bad_root_are_argparse_errors(tmp_path: Path) -> None:
    assert _run().returncode == 2
    result = _run(str(tmp_path / "no-such-dir"))
    assert result.returncode == 2
    assert result.stdout == ""
    assert _run(str(tmp_path), "--mode", "bogus").returncode == 2


def test_changed_export_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    root = _write(tmp_path / SECRET, CONFIG_FILES)
    original = module.build_report

    def build_and_touch(path: Path, mode: str) -> dict[str, Any]:
        report = original(path, mode)
        (path / "added-by-test.txt").write_text("x", encoding="utf-8")
        return report

    monkeypatch.setattr(module, "build_report", build_and_touch)
    assert module.main([str(root)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["export_unchanged"] is False
    assert report["export_changes"] == {"other:added": 1}
    assert report["checks"] == "failed"


def test_failed_mechanics_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    root = _write(tmp_path / SECRET, CONFIG_FILES)
    original = module.build_report

    def broken(path: Path, mode: str) -> dict[str, Any]:
        report = original(path, mode)
        report["exact_by_id"]["other"] = 1
        return report

    monkeypatch.setattr(module, "build_report", broken)
    assert module.main([str(root)]) == 1
    assert json.loads(capsys.readouterr().out)["checks"] == "failed"
