"""Тесты FormRouter на синтетических фикстурах (без реальных данных)."""
import json
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex
from v8unpack_agent.form_router import FormRouter, RouteResult


def _entry(object_type: str, object_name: str, form_name: str) -> FormEntry:
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=object_type + "Form",
        form_name=form_name,
        form_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}"),
        bsl_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}/{object_type}Form.obj.bsl"),
        json_path=Path(f"cf_export/{object_type}/{object_name}/{object_type}Form/{form_name}/{object_type}Form.json"),
    )


FIXTURES = [
    _entry("Catalog",  "Catalog1",  "ListForm"),
    _entry("Catalog",  "Catalog1",  "ObjectForm"),
    _entry("Catalog",  "Catalog2",  "ListForm"),
    _entry("Document", "Document1", "ListForm"),
    _entry("Document", "Document1", "ObjectForm"),
]


@pytest.fixture()
def router(tmp_path: Path) -> FormRouter:
    index_path = tmp_path / "forms_scan_index.json"
    idx = FormScanIndex(forms=FIXTURES, total=len(FIXTURES), scanned_at="2026-01-01T00:00:00+00:00")
    idx.save(index_path)
    return FormRouter(index_path=index_path)


def test_exact_form_name(router: FormRouter) -> None:
    # form_name "ListForm" встречается у нескольких объектов
    result = router.route("ListForm")
    assert result.confidence == 1.0
    assert all(e.form_name == "ListForm" for e in result.matched)
    assert len(result.matched) == 3


def test_partial_object_name(router: FormRouter) -> None:
    result = router.route("Catalog1")
    assert result.confidence >= 0.5
    assert all(e.object_name == "Catalog1" for e in result.matched)
    assert len(result.matched) == 2


def test_object_type_fallback(router: FormRouter) -> None:
    result = router.route("Document")
    assert result.confidence == pytest.approx(0.4)
    assert all(e.object_type == "Document" for e in result.matched)


def test_no_match(router: FormRouter) -> None:
    result = router.route("НесуществующаяФорма")
    assert result.matched == []
    assert result.confidence == 0.0
    assert result.warnings


def test_reindex_updates_entry(router: FormRouter, tmp_path: Path) -> None:
    updated = _entry("Catalog", "Catalog1", "ListForm")
    updated.warnings = ["updated"]
    router.reindex([updated])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    result = router2.route("ListForm")
    updated_entries = [e for e in result.matched if e.object_name == "Catalog1"]
    assert updated_entries[0].warnings == ["updated"]


def test_reindex_preserves_others(router: FormRouter, tmp_path: Path) -> None:
    new_entry = _entry("Report", "Report1", "ReportForm")
    router.reindex([new_entry])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    assert len(router2._entries) == 6  # 5 исходных + 1 новая
