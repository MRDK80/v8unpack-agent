"""Отчёт не зависит от порядка обхода каталогов (issue #208)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from v8unpack_agent.runner import RunOptions, run_pipeline

_TIMESTAMP_RE = re.compile(r'"(started_at|finished_at)": "[^"]+"')


class _ListScandir:
    def __init__(self, entries: list[os.DirEntry[str]]) -> None:
        self._iterator = iter(entries)

    def __enter__(self) -> _ListScandir:  # noqa: PYI034
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def __iter__(self) -> _ListScandir:
        return self

    def __next__(self) -> os.DirEntry[str]:
        return next(self._iterator)

    def close(self) -> None:
        return None


def _reverse_directory_order(patch: pytest.MonkeyPatch) -> None:
    real_scandir = os.scandir
    real_listdir = os.listdir

    def reversed_scandir(path: str | os.PathLike[str] = ".") -> _ListScandir:
        with real_scandir(path) as iterator:
            entries = list(iterator)
        entries.reverse()
        return _ListScandir(entries)

    def reversed_listdir(path: str | os.PathLike[str] = ".") -> list[str]:
        return list(reversed(real_listdir(path)))

    patch.setattr(os, "scandir", reversed_scandir)
    patch.setattr(os, "listdir", reversed_listdir)


def _build_export(root: Path) -> None:
    (root / "Configuration.802.bsl").write_text(
        "Процедура А()\nКонецПроцедуры\n", encoding="utf-8"
    )
    (root / "Configuration.app.bsl").write_bytes(b"")
    for name in ("Alpha", "Beta", "Gamma"):
        folder = root / "Constant" / name
        folder.mkdir(parents=True)
        (folder / "Constant.obj.bsl").write_text("// модуль\n", encoding="utf-8")
    broken = root / "Constant" / "Broken"
    broken.mkdir(parents=True)
    (broken / "Constant.obj.bsl").write_bytes(b"\xff\xfe\xfa")
    for name in ("Api", "Backup"):
        (root / "HTTPService" / name).mkdir(parents=True)
    for name in ("Ws", "Xs"):
        folder = root / "WebService" / name
        folder.mkdir(parents=True)
        (folder / "WebService.obj.bsl").write_text("  \n", encoding="utf-8")


def _options(root: Path) -> RunOptions:
    return RunOptions(
        export_root=root,
        include_common_modules=False,
        include_skd=False,
        include_module_index=True,
    )


def _normalized_json(root: Path) -> str:
    outcome = run_pipeline(_options(root))
    return _TIMESTAMP_RE.sub(r'"\1": "T"', outcome.report.to_json())


def test_reversal_changes_directory_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_export(tmp_path)
    with os.scandir(tmp_path) as iterator:
        normal = [entry.name for entry in iterator]
    with monkeypatch.context() as patch:
        _reverse_directory_order(patch)
        with os.scandir(tmp_path) as iterator:
            reversed_names = [entry.name for entry in iterator]
    assert len(normal) > 1
    assert reversed_names == list(reversed(normal))


def test_repeated_runs_give_same_json(tmp_path: Path) -> None:
    _build_export(tmp_path)
    first = _normalized_json(tmp_path)
    second = _normalized_json(tmp_path)
    assert "module_value_manager" in first
    assert first == second


def test_json_independent_of_directory_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_export(tmp_path)
    direct = _normalized_json(tmp_path)
    with monkeypatch.context() as patch:
        _reverse_directory_order(patch)
        reversed_json = _normalized_json(tmp_path)
    assert reversed_json == direct
