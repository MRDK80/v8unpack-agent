"""Портируемая сериализация FormScanIndex — issue #239.

Все деревья синтетические (``tmp_path``): доменных имён, реальных путей,
UUID и содержимого выгрузки в тестах нет.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from v8unpack_agent.drift_checker import check_drift
from v8unpack_agent.scan_forms import (
    INDEX_SCHEMA_VERSION,
    FormScanIndex,
    scan_forms,
)

BACKSLASH = chr(92)

PATH_FIELDS = (
    "form_path",
    "bsl_path",
    "json_path",
    "form_elem_path",
    "elem_json_path",
)

BSL_TEXT = "Процедура ПриОткрытии()\nКонецПроцедуры\n"


def _make_form(
    root: Path,
    object_name: str,
    form_name: str,
    *,
    with_elem: bool = False,
) -> Path:
    form_dir = root / "Catalog" / object_name / "CatalogForm" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "CatalogForm.obj.bsl").write_text(BSL_TEXT, encoding="utf-8")
    (form_dir / "CatalogForm.json").write_text("{}", encoding="utf-8")
    if with_elem:
        (form_dir / "CatalogForm.elem.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _make_form(root, "Alpha", "FormOne", with_elem=True)
    _make_form(root, "Beta", "FormTwo")
    return root


def _path_values(payload: dict) -> list[str]:
    values: list[str] = []
    for row in payload["forms"]:
        for name in PATH_FIELDS:
            value = row.get(name)
            if value is not None:
                values.append(value)
    return values


def test_to_dict_has_no_absolute_root(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    payload = scan_forms(root).to_dict()
    dumped = json.dumps(payload, ensure_ascii=False)

    assert root.as_posix() not in dumped
    assert str(root) not in dumped
    assert BACKSLASH not in dumped


def test_path_fields_are_relative_posix(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    payload = scan_forms(root).to_dict()

    values = _path_values(payload)
    assert values
    for value in values:
        assert not value.startswith("/")
        assert ":" not in value
        assert BACKSLASH not in value
        assert ".." not in Path(value).parts


def test_schema_version_is_serialized(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    payload = scan_forms(root).to_dict()

    assert payload["schema_version"] == INDEX_SCHEMA_VERSION


def test_optional_elem_json_path_is_preserved(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    payload = scan_forms(root).to_dict()

    by_form = {row["form_name"]: row for row in payload["forms"]}
    assert by_form["FormTwo"]["elem_json_path"] is None
    assert by_form["FormOne"]["elem_json_path"] is not None


def test_save_writes_portable_json(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    index_path = tmp_path / "forms_scan_index.json"
    scan_forms(root).save(index_path)

    text = index_path.read_text(encoding="utf-8")
    assert root.as_posix() not in text
    assert BACKSLASH not in text


def test_scan_forms_save_to_is_portable(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    index_path = tmp_path / "saved.json"
    scan_forms(root, save_to=index_path)

    text = index_path.read_text(encoding="utf-8")
    assert root.as_posix() not in text


def test_load_rehydrates_against_new_root(tmp_path: Path) -> None:
    root_a = _make_tree(tmp_path / "export_a")
    root_b = tmp_path / "export_b"
    shutil.copytree(root_a, root_b)

    index_path = tmp_path / "index.json"
    scan_forms(root_a).save(index_path)

    loaded = FormScanIndex.load(index_path, root=root_b)
    assert loaded.forms
    for entry in loaded.forms:
        assert entry.bsl_path.is_absolute()
        assert entry.bsl_path.exists()
        assert root_b.resolve() in entry.bsl_path.parents
        assert root_a.resolve() not in entry.bsl_path.parents


def test_round_trip_payload_is_deterministic(tmp_path: Path) -> None:
    root_a = _make_tree(tmp_path / "export_a")
    index_path = tmp_path / "index.json"
    original = scan_forms(root_a)
    original.save(index_path)

    reloaded = FormScanIndex.load(index_path, root=root_a)
    assert reloaded.to_dict()["forms"] == original.to_dict()["forms"]


def test_index_moves_from_root_a_to_root_b(tmp_path: Path) -> None:
    root_a = _make_tree(tmp_path / "export_a")
    index_path = tmp_path / "index.json"
    scan_forms(root_a, save_to=index_path)

    root_b = tmp_path / "export_b"
    shutil.copytree(root_a, root_b)

    report = check_drift(root_b, index_path)
    assert report.added == []
    assert report.removed == []
    assert report.modified == []
    assert report.stale_extractions == []
    assert report.has_drift is False


def test_real_change_is_still_detected(tmp_path: Path) -> None:
    root_a = _make_tree(tmp_path / "export_a")
    index_path = tmp_path / "index.json"
    scan_forms(root_a, save_to=index_path)

    root_b = tmp_path / "export_b"
    shutil.copytree(root_a, root_b)
    changed = root_b / "Catalog" / "Alpha" / "CatalogForm" / "FormOne"
    (changed / "CatalogForm.obj.bsl").write_text(
        BSL_TEXT + "// изменение\n", encoding="utf-8"
    )

    report = check_drift(root_b, index_path)
    assert report.modified
    assert report.has_drift is True


def test_legacy_index_is_read_as_absolute(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    live = scan_forms(root)
    legacy = live.to_dict()
    legacy.pop("schema_version")
    for row in legacy["forms"]:
        for name in ("form_path", "bsl_path", "json_path"):
            row[name] = (root / row[name]).as_posix()

    index_path = tmp_path / "legacy.json"
    index_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    loaded = FormScanIndex.load(index_path, root=tmp_path / "other")
    assert loaded.forms
    for entry in loaded.forms:
        assert entry.bsl_path.exists()
        assert root.resolve() in entry.bsl_path.parents


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    index_path = tmp_path / "future.json"
    index_path.write_text(
        json.dumps({"schema_version": 99, "forms": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="schema version"):
        FormScanIndex.load(index_path)


def test_to_dict_requires_scan_root_for_absolute_paths(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    index = scan_forms(root)
    index.scan_root = None

    with pytest.raises(ValueError, match="scan_root"):
        index.to_dict()


def test_scan_warnings_have_no_absolute_root(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "export_a")
    empty_form = root / "Catalog" / "Alpha" / "CatalogForm" / "FormEmpty"
    empty_form.mkdir(parents=True, exist_ok=True)

    index = scan_forms(root)
    assert index.scan_warnings
    dumped = json.dumps(index.to_dict(), ensure_ascii=False)
    assert root.as_posix() not in dumped


def test_unicode_components_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "export_a"
    root.mkdir(parents=True, exist_ok=True)
    _make_form(root, "Объект", "ФормаЭлемента")

    index_path = tmp_path / "index.json"
    scan_forms(root, save_to=index_path)
    loaded = FormScanIndex.load(index_path, root=root)

    names = {entry.form_name for entry in loaded.forms}
    assert "ФормаЭлемента" in names
    for entry in loaded.forms:
        assert entry.bsl_path.exists()


def test_invalid_root_contract_is_preserved(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        scan_forms(tmp_path / "missing")
