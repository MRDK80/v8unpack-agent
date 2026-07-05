"""Тесты FormScanIndex.load() — публичный classmethod, issue #36.

Покрывает:
- round-trip save() → load(): число форм, total, scanned_at, scan_warnings;
- восстановление Path-атрибутов (form_path, bsl_path, json_path);
- form_elem_path=None сохраняется и восстанавливается;
- form_elem_path не-None сохраняется и восстанавливается;
- отсутствующий файл → пустой индекс (не исключение);
- пустой индекс (forms=[]) round-trip;
- несколько форм round-trip сохраняет порядок и все поля.

Только синтетические данные — никаких реальных .cf, .epf, .erf.
"""
from __future__ import annotations

from pathlib import Path

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_entry(
    root: Path,
    object_type: str = "Catalog",
    object_name: str = "Склады",
    container_name: str = "CatalogForm",
    form_name: str = "ФормаЭлемента",
    bsl_mtime: float = 1718450000.0,
    form_elem_path: Path | None = None,
) -> FormEntry:
    """Создать синтетический FormEntry с корректными Path-значениями."""
    base = root / object_type / object_name / container_name / form_name
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container_name,
        form_name=form_name,
        form_path=base,
        bsl_path=base / f"{container_name}.obj.bsl",
        json_path=base / f"{container_name}.json",
        warnings=[],
        bsl_mtime=bsl_mtime,
        form_elem_path=form_elem_path,
    )


def _make_index(entries: list[FormEntry], scanned_at: str = "2024-06-15T10:00:00+00:00") -> FormScanIndex:
    return FormScanIndex(
        forms=entries,
        total=len(entries),
        scanned_at=scanned_at,
        scan_warnings=[],
    )


# ---------------------------------------------------------------------------
# тесты
# ---------------------------------------------------------------------------

def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    """Отсутствующий файл → пустой индекс без исключения."""
    result = FormScanIndex.load(tmp_path / "no_such_file.json")
    assert result.total == 0
    assert result.forms == []
    assert result.scan_warnings == []


def test_load_empty_index_round_trip(tmp_path: Path) -> None:
    """Пустой индекс сохраняется и восстанавливается без ошибок."""
    idx = _make_index([])
    out = tmp_path / "index.json"
    idx.save(out)
    loaded = FormScanIndex.load(out)
    assert loaded.total == 0
    assert loaded.forms == []
    assert loaded.scanned_at == idx.scanned_at


def test_load_single_entry_round_trip(tmp_path: Path) -> None:
    """Одна форма: все поля восстанавливаются корректно."""
    entry = _make_entry(tmp_path)
    idx = _make_index([entry])
    out = tmp_path / "index.json"
    idx.save(out)
    loaded = FormScanIndex.load(out)

    assert loaded.total == 1
    assert len(loaded.forms) == 1
    e = loaded.forms[0]
    assert e.object_type == entry.object_type
    assert e.object_name == entry.object_name
    assert e.container_name == entry.container_name
    assert e.form_name == entry.form_name
    assert e.bsl_mtime == entry.bsl_mtime
    assert e.form_elem_path is None


def test_load_restores_path_types(tmp_path: Path) -> None:
    """form_path, bsl_path, json_path восстанавливаются как Path (не str)."""
    entry = _make_entry(tmp_path)
    out = tmp_path / "index.json"
    _make_index([entry]).save(out)
    loaded = FormScanIndex.load(out)
    e = loaded.forms[0]
    assert isinstance(e.form_path, Path)
    assert isinstance(e.bsl_path, Path)
    assert isinstance(e.json_path, Path)


def test_load_form_elem_path_none(tmp_path: Path) -> None:
    """form_elem_path=None сохраняется и восстанавливается как None."""
    entry = _make_entry(tmp_path, form_elem_path=None)
    out = tmp_path / "index.json"
    _make_index([entry]).save(out)
    loaded = FormScanIndex.load(out)
    assert loaded.forms[0].form_elem_path is None


def test_load_form_elem_path_not_none(tmp_path: Path) -> None:
    """form_elem_path не-None сохраняется и восстанавливается как Path."""
    elem = tmp_path / "Ext" / "Form.elem"
    entry = _make_entry(tmp_path, form_elem_path=elem)
    out = tmp_path / "index.json"
    _make_index([entry]).save(out)
    loaded = FormScanIndex.load(out)
    loaded_elem = loaded.forms[0].form_elem_path
    assert isinstance(loaded_elem, Path)
    assert loaded_elem == elem


def test_load_multiple_entries_preserves_order(tmp_path: Path) -> None:
    """Несколько форм: порядок и form_name сохраняются."""
    names = ["ФормаА", "ФормаБ", "ФормаВ"]
    entries = [_make_entry(tmp_path, form_name=n) for n in names]
    out = tmp_path / "index.json"
    _make_index(entries).save(out)
    loaded = FormScanIndex.load(out)
    assert loaded.total == 3
    assert [e.form_name for e in loaded.forms] == names


def test_load_scan_warnings_round_trip(tmp_path: Path) -> None:
    """scan_warnings сохраняются и восстанавливаются."""
    idx = FormScanIndex(
        forms=[],
        total=0,
        scanned_at="2024-06-15T10:00:00+00:00",
        scan_warnings=["skipped: some/path", "error: another/path"],
    )
    out = tmp_path / "index.json"
    idx.save(out)
    loaded = FormScanIndex.load(out)
    assert loaded.scan_warnings == ["skipped: some/path", "error: another/path"]


def test_load_bsl_mtime_round_trip(tmp_path: Path) -> None:
    """bsl_mtime сохраняется и восстанавливается как float."""
    mtime = 1718450123.456
    entry = _make_entry(tmp_path, bsl_mtime=mtime)
    out = tmp_path / "index.json"
    _make_index([entry]).save(out)
    loaded = FormScanIndex.load(out)
    assert loaded.forms[0].bsl_mtime == mtime
