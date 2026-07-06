"""Синтетические тесты для issue #40: elem_sha256 и structure_modified.

Все тесты используют только in-memory / tmp-фикстуры.
Реальные конфигурации и живые пути не используются.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import (
    FormScanIndex,
    scan_forms,
)
from v8unpack_agent.drift_checker import DriftReport, check_drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COSMETIC_KEYS = ("left", "top", "width", "height", "color", "font", "guid")


def _write_bsl(form_dir: Path, container: str, content: str = "// bsl") -> Path:
    bsl = form_dir / f"{container}.obj.bsl"
    bsl.write_text(content, encoding="utf-8")
    return bsl


def _write_elem_json(form_dir: Path, elements: list[dict], extra: dict | None = None) -> Path:
    """Записать минимальный *.elem.json, имитирующий структуру платформы.

    ``extra`` — словарь дополнительных полей верхнего уровня (косметика).
    """
    payload: dict = {"elements": elements}
    if extra:
        payload.update(extra)
    p = form_dir / "CatalogForm.elem.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _make_config_tree(root: Path, obj_type: str, obj_name: str, container: str, form_name: str) -> Path:
    """Создать минимальную 4-уровневую структуру cf_export."""
    form_dir = root / obj_type / obj_name / container / form_name
    form_dir.mkdir(parents=True)
    return form_dir


# ---------------------------------------------------------------------------
# test_scan_forms_records_elem_sha256
# ---------------------------------------------------------------------------

def test_scan_forms_records_elem_sha256(tmp_path: Path) -> None:
    """scan_forms() должен заполнять elem_sha256 для форм конфигурации
    при наличии *.elem.json рядом с *.obj.bsl."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    _write_bsl(form_dir, "CatalogForm")
    elements = [
        {"name": "Button1", "type": "Button", "parent": None,
         "parent_path": None, "path": "Button1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements)

    index = scan_forms(tmp_path)
    assert len(index.forms) == 1
    entry = index.forms[0]
    assert entry.elem_sha256 is not None, "elem_sha256 должен быть вычислен"
    assert len(entry.elem_sha256) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# test_elem_sha256_ignores_cosmetic_visual_props
# ---------------------------------------------------------------------------

def test_elem_sha256_ignores_cosmetic_visual_props(tmp_path: Path) -> None:
    """Хэш нормализованного дерева не должен меняться при изменении
    визуальных свойств (координаты, цвета, шрифты, GUID) — только
    структурно значимые поля: name, type, path, parent, parent_path,
    page, source, data_path, handler."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    _write_bsl(form_dir, "CatalogForm")

    base_elements = [
        {"name": "Field1", "type": "InputField", "parent": None,
         "parent_path": None, "path": "Field1", "page": None, "source": "data"},
    ]

    # Версия 1: без косметики
    _write_elem_json(form_dir, base_elements)
    index1 = scan_forms(tmp_path)
    hash1 = index1.forms[0].elem_sha256

    # Версия 2: добавляем косметику
    cosmetic_extra = {"left": 10, "top": 20, "color": "#FF0000", "guid": "abc-123"}
    _write_elem_json(form_dir, base_elements, extra=cosmetic_extra)
    index2 = scan_forms(tmp_path)
    hash2 = index2.forms[0].elem_sha256

    assert hash1 == hash2, (
        "Косметические правки (координаты/цвет/GUID) не должны менять elem_sha256"
    )


# ---------------------------------------------------------------------------
# test_load_elem_sha256_round_trip
# ---------------------------------------------------------------------------

def test_load_elem_sha256_round_trip(tmp_path: Path) -> None:
    """save() / load() FormScanIndex сохраняет и восстанавливает elem_sha256."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    _write_bsl(form_dir, "CatalogForm")
    elements = [
        {"name": "Button1", "type": "Button", "parent": None,
         "parent_path": None, "path": "Button1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements)

    index = scan_forms(tmp_path)
    original_hash = index.forms[0].elem_sha256
    assert original_hash is not None

    save_path = tmp_path / "idx.json"
    index.save(save_path)
    loaded = FormScanIndex.load(save_path)

    assert len(loaded.forms) == 1
    assert loaded.forms[0].elem_sha256 == original_hash


# ---------------------------------------------------------------------------
# test_old_index_without_elem_sha256_no_structure_drift
# ---------------------------------------------------------------------------

def test_old_index_without_elem_sha256_no_structure_drift(tmp_path: Path) -> None:
    """Старый индекс (нет поля elem_sha256) не порождает structure_modified."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    _write_bsl(form_dir, "CatalogForm", content="// v1")
    elements = [
        {"name": "Field1", "type": "InputField", "parent": None,
         "parent_path": None, "path": "Field1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements)

    # Собираем индекс, затем вручную убираем elem_sha256 → имитируем старый формат
    index = scan_forms(tmp_path)
    index_dict = index.to_dict()
    for row in index_dict["forms"]:
        row.pop("elem_sha256", None)

    save_path = tmp_path / "idx_old.json"
    save_path.write_text(json.dumps(index_dict, ensure_ascii=False), encoding="utf-8")

    report = check_drift(tmp_path, save_path)
    assert report.structure_modified == [], (
        "Старый индекс без elem_sha256 не должен давать structure_modified"
    )


# ---------------------------------------------------------------------------
# test_structure_modified_triggered_by_element_tree_change
# ---------------------------------------------------------------------------

def test_structure_modified_triggered_by_element_tree_change(tmp_path: Path) -> None:
    """Добавление элемента на форму (BSL не тронут) → structure_modified."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    _write_bsl(form_dir, "CatalogForm", content="// stable bsl")

    elements_v1 = [
        {"name": "Field1", "type": "InputField", "parent": None,
         "parent_path": None, "path": "Field1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements_v1)
    index = scan_forms(tmp_path)
    save_path = tmp_path / "idx.json"
    index.save(save_path)

    # Меняем структуру формы: добавляем элемент, BSL без изменений
    elements_v2 = elements_v1 + [
        {"name": "Button1", "type": "Button", "parent": None,
         "parent_path": None, "path": "Button1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements_v2)

    report = check_drift(tmp_path, save_path)
    form_key = "Catalog/Goods/CatalogForm/FormElement"
    assert form_key in report.structure_modified, (
        "Изменение дерева элементов должно попасть в structure_modified"
    )
    assert form_key not in report.modified, (
        "BSL не изменён — форма не должна попасть в modified"
    )
    assert report.has_drift is True


# ---------------------------------------------------------------------------
# test_structure_modified_not_triggered_by_bsl_only_change
# ---------------------------------------------------------------------------

def test_structure_modified_not_triggered_by_bsl_only_change(tmp_path: Path) -> None:
    """Изменение только BSL (разметка не тронута) → modified, но не structure_modified."""
    form_dir = _make_config_tree(tmp_path, "Catalog", "Goods", "CatalogForm", "FormElement")
    bsl = _write_bsl(form_dir, "CatalogForm", content="// v1")

    elements = [
        {"name": "Field1", "type": "InputField", "parent": None,
         "parent_path": None, "path": "Field1", "page": None, "source": "data"},
    ]
    _write_elem_json(form_dir, elements)
    index = scan_forms(tmp_path)
    save_path = tmp_path / "idx.json"
    index.save(save_path)

    # Меняем только BSL
    bsl.write_text("// v2 changed", encoding="utf-8")

    report = check_drift(tmp_path, save_path)
    form_key = "Catalog/Goods/CatalogForm/FormElement"
    assert form_key in report.modified, "Изменение BSL должно попасть в modified"
    assert form_key not in report.structure_modified, (
        "Разметка не тронута — не должно быть structure_modified"
    )
    assert report.has_drift is True
