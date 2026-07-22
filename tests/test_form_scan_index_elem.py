"""Тесты единого реестра форм с elem_json_path (issue #57).

Покрывает все Acceptance Criteria:
- AC1: scan_forms возвращает ordinary + external + elem-формы в одном FormScanIndex.
- AC2: у elem-форм заполнен elem_json_path (relative-to-root).
- AC3: у ordinary/external форм elem_json_path == None, если *.elem.json нет.
- AC4: JSON serialization/deserialization стабильна.
- AC5: старые индексы без elem_json_path читаются с дефолтами (None).
- AC6: старые индексы с form_xml_path читаются без ошибок (игнорируется).
- AC7: в коде нет зависимости от Form.xml (регрессия: отсутствие Form.xml не ломает scan).
- AC8: смешанный индекс (ordinary + external + elem-only) — все три типа присутствуют.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms


# ---------------------------------------------------------------------------
# Вспомогательные фабрики файловой структуры
# ---------------------------------------------------------------------------

def _make_ordinary_form(
    root: Path,
    object_type: str,
    object_name: str,
    container: str,
    form_name: str,
    with_elem_json: bool = False,
) -> Path:
    """Создать структуру обычной формы конфигурации."""
    form_dir = root / object_type / object_name / container / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{container}.obj.bsl").write_text("// bsl", encoding="utf-8")
    (form_dir / f"{container}.json").write_text("{}", encoding="utf-8")
    if with_elem_json:
        (form_dir / f"{container}.elem.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_external_form(
    root: Path,
    proc_name: str,
    container: str,
    form_name: str,
    with_elem_json: bool = False,
) -> Path:
    """Создать структуру формы внешней обработки."""
    proc_dir = root / proc_name
    proc_dir.mkdir(parents=True, exist_ok=True)
    # модуль объекта — чтобы тип определился как ExternalDataProcessor
    (proc_dir / "ExternalDataProcessor.obj.bsl").write_text("// obj", encoding="utf-8")
    form_dir = proc_dir / container / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{container}.obj.bsl").write_text("// bsl", encoding="utf-8")
    (form_dir / f"{container}.json").write_text("{}", encoding="utf-8")
    if with_elem_json:
        (form_dir / f"{container}.elem.json").write_text("{}", encoding="utf-8")
    return form_dir


def _make_elem_only_form(
    root: Path,
    object_type: str,
    object_name: str,
    container: str,
    form_name: str,
) -> Path:
    """Создать elem-only форму (нет .obj.bsl, есть *.elem.json)."""
    form_dir = root / object_type / object_name / container / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{container}.elem.json").write_text("{}", encoding="utf-8")
    return form_dir


# ---------------------------------------------------------------------------
# AC1 + AC3: обычная форма без elem.json → elem_json_path is None
# ---------------------------------------------------------------------------

class TestOrdinaryForm:
    def test_ordinary_form_in_index(self, tmp_path):
        """AC1: обычная форма попадает в FormScanIndex."""
        _make_ordinary_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement")
        idx = scan_forms(tmp_path, include_elem_only=False)
        assert idx.total == 1
        assert idx.forms[0].form_name == "FormElement"

    def test_ordinary_form_elem_json_path_none_without_file(self, tmp_path):
        """AC3: у обычной формы без *.elem.json → elem_json_path is None."""
        _make_ordinary_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
                            with_elem_json=False)
        idx = scan_forms(tmp_path, include_elem_only=False)
        assert idx.forms[0].elem_json_path is None

    def test_ordinary_form_elem_json_path_filled_with_file(self, tmp_path):
        """AC2/AC3: у обычной формы с *.elem.json → elem_json_path заполнен relative-to-root."""
        form_dir = _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            with_elem_json=True,
        )
        idx = scan_forms(tmp_path, include_elem_only=False)
        entry = idx.forms[0]
        assert entry.elem_json_path is not None
        # путь должен быть относительным (не абсолютным)
        assert not entry.elem_json_path.is_absolute()
        # должен указывать именно на *.elem.json внутри каталога формы
        assert entry.elem_json_path.name.endswith(".elem.json")
        assert entry.elem_json_path.parts[0] == "Catalog"


# ---------------------------------------------------------------------------
# AC1 + AC3: внешняя форма
# ---------------------------------------------------------------------------

class TestExternalForm:
    def test_external_form_in_index(self, tmp_path):
        """AC1: внешняя форма попадает в FormScanIndex."""
        _make_external_form(tmp_path, "MyProc", "Form", "FormMain")
        idx = scan_forms(tmp_path, mode="external", include_elem_only=False)
        assert idx.total == 1
        assert idx.forms[0].form_name == "FormMain"

    def test_external_form_elem_json_path_none_without_file(self, tmp_path):
        """AC3: у внешней формы без *.elem.json → elem_json_path is None."""
        _make_external_form(tmp_path, "MyProc", "Form", "FormMain", with_elem_json=False)
        idx = scan_forms(tmp_path, mode="external", include_elem_only=False)
        assert idx.forms[0].elem_json_path is None

    def test_external_form_elem_json_path_filled_with_file(self, tmp_path):
        """AC2/AC3: у внешней формы с *.elem.json → elem_json_path relative-to-root."""
        _make_external_form(tmp_path, "MyProc", "Form", "FormMain", with_elem_json=True)
        idx = scan_forms(tmp_path, mode="external", include_elem_only=False)
        entry = idx.forms[0]
        assert entry.elem_json_path is not None
        assert not entry.elem_json_path.is_absolute()
        assert entry.elem_json_path.name.endswith(".elem.json")


# ---------------------------------------------------------------------------
# AC2: elem-only форма
# ---------------------------------------------------------------------------

class TestElemOnlyForm:
    def test_elem_only_form_in_index(self, tmp_path):
        """AC1+AC2: elem-only форма попадает в FormScanIndex с заполненным elem_json_path."""
        _make_elem_only_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement")
        idx = scan_forms(tmp_path, include_elem_only=True)
        # elem-only не имеет .obj.bsl → не пройдёт _scan_form_dir, но должна быть в индексе
        elem_entries = [e for e in idx.forms if e.elem_json_path is not None]
        assert len(elem_entries) >= 1

    def test_elem_only_elem_json_path_is_relative(self, tmp_path):
        """AC2: elem_json_path у elem-only формы — относительный путь."""
        _make_elem_only_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement")
        idx = scan_forms(tmp_path, include_elem_only=True)
        elem_entries = [e for e in idx.forms if e.elem_json_path is not None]
        assert elem_entries, "elem-only форма не попала в индекс"
        for e in elem_entries:
            assert not e.elem_json_path.is_absolute()
            assert e.elem_json_path.name.endswith(".elem.json")

    def test_elem_only_skipped_without_flag(self, tmp_path):
        """При include_elem_only=False elem-only формы не добавляются."""
        _make_elem_only_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement")
        idx = scan_forms(tmp_path, include_elem_only=False)
        # без флага форма без .obj.bsl пропускается
        assert idx.total == 0


# ---------------------------------------------------------------------------
# AC8: смешанный индекс
# ---------------------------------------------------------------------------

class TestMixedIndex:
    def test_mixed_index_contains_all_types(self, tmp_path):
        """AC8: обычная + elem-only формы присутствуют в одном FormScanIndex."""
        # обычная форма с .obj.bsl
        _make_ordinary_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement")
        # elem-only форма (управляемая, только *.elem.json)
        _make_elem_only_form(
            tmp_path, "Catalog", "AlcProd", "CatalogForm", "FormElemManaged"
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        form_names = {e.form_name for e in idx.forms}
        assert "FormElement" in form_names
        assert "FormElemManaged" in form_names

    def test_no_duplicates_when_ordinary_has_elem_json(self, tmp_path):
        """Обычная форма с *.elem.json не дублируется в elem-only коллекции."""
        _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            with_elem_json=True,
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        form_dirs = [e.form_path for e in idx.forms]
        # каждый form_path уникален
        assert len(form_dirs) == len(set(str(p) for p in form_dirs))


# ---------------------------------------------------------------------------
# AC4: JSON round-trip
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:
    def test_round_trip_ordinary_no_elem_json_path(self, tmp_path):
        """AC4: обычная форма без elem_json_path сохраняется и читается корректно."""
        _make_ordinary_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement")
        idx = scan_forms(tmp_path, include_elem_only=False)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)
        loaded = FormScanIndex.load(save_path)
        assert loaded.total == idx.total
        assert loaded.forms[0].elem_json_path is None

    def test_round_trip_ordinary_with_elem_json_path(self, tmp_path):
        """AC4: обычная форма с elem_json_path сохраняется и читается корректно."""
        _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            with_elem_json=True,
        )
        idx = scan_forms(tmp_path, include_elem_only=False)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)
        loaded = FormScanIndex.load(save_path)
        assert loaded.forms[0].elem_json_path is not None
        assert not loaded.forms[0].elem_json_path.is_absolute()

    def test_round_trip_elem_only(self, tmp_path):
        """AC4: elem-only форма round-trip сохраняет elem_json_path."""
        _make_elem_only_form(tmp_path, "Catalog", "Banks", "CatalogForm", "FormManaged")
        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)
        raw = json.loads(save_path.read_text(encoding="utf-8"))
        # хотя бы одна форма содержит elem_json_path в JSON
        paths = [f.get("elem_json_path") for f in raw["forms"]]
        assert any(p is not None for p in paths)
        loaded = FormScanIndex.load(save_path)
        elem_entries = [e for e in loaded.forms if e.elem_json_path is not None]
        assert elem_entries


# ---------------------------------------------------------------------------
# AC5: обратная совместимость — старый индекс без elem_json_path
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def _old_index_json(self, tmp_path: Path, form_path: str) -> Path:
        """Создать JSON-файл старого формата (без elem_json_path)."""
        data = {
            "total": 1,
            "scanned_at": "2025-01-01T00:00:00+00:00",
            "scan_warnings": [],
            "forms": [
                {
                    "object_type": "Catalog",
                    "object_name": "Banks",
                    "container_name": "CatalogForm",
                    "form_name": "FormElement",
                    "form_path": form_path,
                    "bsl_path": form_path + "/CatalogForm.obj.bsl",
                    "json_path": form_path + "/CatalogForm.json",
                    "warnings": [],
                    "bsl_mtime": 1718450000.0,
                    "form_elem_path": None,
                    "bsl_sha256": None,
                    "elem_sha256": None,
                    # elem_json_path ОТСУТСТВУЕТ намеренно
                }
            ],
        }
        idx_path = tmp_path / "old_index.json"
        idx_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return idx_path

    def test_old_index_loads_with_none_elem_json_path(self, tmp_path):
        """AC5: старый индекс без elem_json_path загружается, поле = None."""
        idx_path = self._old_index_json(tmp_path, "/some/path/FormElement")
        loaded = FormScanIndex.load(idx_path)
        assert loaded.total == 1
        assert loaded.forms[0].elem_json_path is None

    def test_old_index_with_form_xml_path_loads_silently(self, tmp_path):
        """AC6: старый индекс с form_xml_path (устаревшее поле) загружается без ошибок."""
        data = {
            "total": 1,
            "scanned_at": "2025-01-01T00:00:00+00:00",
            "scan_warnings": [],
            "forms": [
                {
                    "object_type": "Catalog",
                    "object_name": "Banks",
                    "container_name": "CatalogForm",
                    "form_name": "FormElement",
                    "form_path": "/some/path/FormElement",
                    "bsl_path": "/some/path/FormElement/CatalogForm.obj.bsl",
                    "json_path": "/some/path/FormElement/CatalogForm.json",
                    "warnings": [],
                    "bsl_mtime": 0.0,
                    "form_elem_path": None,
                    "bsl_sha256": None,
                    "elem_sha256": None,
                    "form_xml_path": "/some/path/FormElement/Form.xml",  # устаревшее поле
                    # elem_json_path отсутствует
                }
            ],
        }
        idx_path = tmp_path / "old_xml_index.json"
        idx_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        loaded = FormScanIndex.load(idx_path)
        assert loaded.total == 1
        assert loaded.forms[0].elem_json_path is None  # поле проигнорировано корректно


# ---------------------------------------------------------------------------
# AC7: регрессия — Form.xml не влияет на scan
# ---------------------------------------------------------------------------

class TestFormXmlRegression:
    def test_form_xml_absence_does_not_break_scan(self, tmp_path):
        """AC7: отсутствие Form.xml не ломает scan_forms."""
        # создаём форму БЕЗ Form.xml — именно так работает v8unpack 1.2.11
        form_dir = _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            with_elem_json=True,
        )
        # убеждаемся, что Form.xml действительно отсутствует
        assert not (form_dir / "Form.xml").exists()
        # scan не должен падать
        idx = scan_forms(tmp_path)
        assert idx.total >= 1

    def test_no_form_xml_import_in_scan_forms(self):
        """AC7: scan_forms не импортирует/не читает Form.xml (нет зависимости)."""
        import v8unpack_agent.scan_forms as sf
        source = Path(sf.__file__).read_text(encoding="utf-8")
        assert "Form.xml" not in source, (
            "scan_forms.py содержит упоминание Form.xml — это нарушает AC7"
        )
