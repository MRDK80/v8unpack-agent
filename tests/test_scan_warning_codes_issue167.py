"""Машинные коды scan_warnings — issue #167.

Покрывает:
- контракт helpers (_format_scan_warning / scan_warning_code);
- все восемь фактических ветвей формирования scan_warnings;
- совместимость с legacy-индексами и round-trip save/load;
- guard соответствия документации и константы SCAN_WARNING_CODES.

Важно: модуль импортируется как `import v8unpack_agent.scan_forms as sf`.
`from v8unpack_agent import scan_forms` вернёт одноимённую ФУНКЦИЮ из корневого
пакета, а не модуль.

Фикстуры синтетические: реальные UUID, имена объектов и пути не используются.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

import v8unpack_agent.scan_forms as sf
from v8unpack_agent.form_router import FormRouter
from v8unpack_agent.scan_forms import (
    SCAN_WARNING_CODE_MARKER,
    SCAN_WARNING_CODES,
    SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE,
    SCAN_WARNING_FORM_MODULE_MISSING,
    SCAN_WARNING_FORM_SCAN_ERROR,
    SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE,
    SCAN_WARNING_REFERENCE_UUID_CONFLICT,
    SCAN_WARNING_SCAN_ROOT_INVALID,
    FormScanIndex,
    scan_forms,
    scan_warning_code,
)

DOCS_PATH = Path(__file__).resolve().parents[1] / "docs" / "scan_forms.md"
DOC_START = "<!-- scan-warning-codes:start -->"
DOC_END = "<!-- scan-warning-codes:end -->"

OBJECT_TYPE = "Catalog"
CONTAINER = "CatalogForm"

SHARED_UUID = "11111111-1111-4111-8111-111111111111"
OTHER_UUID = "22222222-2222-4222-8222-222222222222"

EXTERNAL_ROOT = "External"
EXTERNAL_OBJECT_MODULE = "ExternalDataProcessor.obj.bsl"
EXTERNAL_CONTAINER = "Form"
EXTERNAL_FORM_BSL = "Form.obj.bsl"


# --------------------------------------------------------------------------- #
# фикстуры
# --------------------------------------------------------------------------- #
def _make_config_form(root: Path, object_name: str, form_name: str, *, with_bsl: bool) -> Path:
    """<root>/Catalog/<object>/CatalogForm/<form>/CatalogForm.obj.bsl"""
    form_dir = root / OBJECT_TYPE / object_name / CONTAINER / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_bsl:
        (form_dir / f"{CONTAINER}.obj.bsl").write_text(
            "Procedure OnOpen()\nEndProcedure\n", encoding="utf-8"
        )
        (form_dir / f"{CONTAINER}.json").write_text("{}", encoding="utf-8")
    return form_dir


def _write_identity_block(
    root: Path, object_type: str, object_name: str, identity: list[str]
) -> Path:
    """<root>/<Type>/<Name>/<Type>.json с блоком идентификации (issue #88)."""
    object_dir = root / object_type / object_name
    object_dir.mkdir(parents=True, exist_ok=True)
    path = object_dir / f"{object_type}.json"
    path.write_text(json.dumps({"header": [["metadata", identity]]}), encoding="utf-8")
    return path


def _make_external_processor(
    root: Path, processor_name: str, form_name: str, *, with_form_bsl: bool
) -> Path:
    """External/<обработка>/ExternalDataProcessor.obj.bsl + Form/<форма>/."""
    processor_dir = root / EXTERNAL_ROOT / processor_name
    processor_dir.mkdir(parents=True, exist_ok=True)
    (processor_dir / EXTERNAL_OBJECT_MODULE).write_text(
        "Procedure ObjectModule()\nEndProcedure\n", encoding="utf-8"
    )
    form_dir = processor_dir / EXTERNAL_CONTAINER / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_form_bsl:
        (form_dir / EXTERNAL_FORM_BSL).write_text(
            "Procedure OnOpen()\nEndProcedure\n", encoding="utf-8"
        )
    return form_dir


def _codes(index: FormScanIndex) -> list[str | None]:
    return [scan_warning_code(warning) for warning in index.scan_warnings]


def _with_code(index: FormScanIndex, code: str) -> list[str]:
    return [w for w in index.scan_warnings if scan_warning_code(w) == code]


# --------------------------------------------------------------------------- #
# A. helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", sorted(SCAN_WARNING_CODES))
def test_format_and_parse_round_trip(code: str) -> None:
    warning = sf._format_scan_warning(code, "human readable message")
    assert warning == f"human readable message{SCAN_WARNING_CODE_MARKER}{code}]"
    assert warning.startswith("human readable message")
    assert scan_warning_code(warning) == code


def test_legacy_warning_has_no_code() -> None:
    assert scan_warning_code("skipped some/path") is None
    assert scan_warning_code("legacy warning without code") is None


@pytest.mark.parametrize(
    "warning",
    [
        "msg [code=]",
        "msg [code=lower_case]",
        "msg [code=WITH SPACE]",
        "msg [code=TRAILING",
        "msg [code=DOT.CODE]",
        "",
    ],
)
def test_broken_suffix_returns_none(warning: str) -> None:
    assert scan_warning_code(warning) is None


def test_unknown_but_valid_code_returns_none() -> None:
    assert scan_warning_code("msg [code=NOT_DOCUMENTED]") is None


def test_format_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        sf._format_scan_warning("not_a_code", "msg")


def test_parser_takes_last_suffix() -> None:
    warning = sf._format_scan_warning(SCAN_WARNING_FORM_MODULE_MISSING, "text [code=FAKE]")
    assert scan_warning_code(warning) == SCAN_WARNING_FORM_MODULE_MISSING


# --------------------------------------------------------------------------- #
# B1. ветви config-режима
# --------------------------------------------------------------------------- #
def test_config_layout_precondition(tmp_path: Path) -> None:
    _make_config_form(tmp_path, "Obj1", "FormOk", with_bsl=True)
    index = scan_forms(tmp_path)
    assert index.total == 1, "layout config-фикстуры не распознан scan_forms"


def test_form_module_missing_config(tmp_path: Path) -> None:
    _make_config_form(tmp_path, "Obj1", "FormOk", with_bsl=True)
    _make_config_form(tmp_path, "Obj1", "FormNoBsl", with_bsl=False)

    index = scan_forms(tmp_path)
    skipped = [w for w in index.scan_warnings if w.startswith("skipped")]

    assert skipped, f"ожидалось skip-предупреждение, получено: {index.scan_warnings}"
    assert all(scan_warning_code(w) == SCAN_WARNING_FORM_MODULE_MISSING for w in skipped)


def test_form_scan_error_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_config_form(tmp_path, "Obj1", "FormOk", with_bsl=True)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic failure")

    monkeypatch.setattr(sf, "_scan_form_dir", boom)
    index = scan_forms(tmp_path)

    errors = _with_code(index, SCAN_WARNING_FORM_SCAN_ERROR)
    assert errors, f"ожидалось error-предупреждение, получено: {index.scan_warnings}"
    assert all(w.startswith("error scanning") for w in errors)
    assert index.total == 0


# --------------------------------------------------------------------------- #
# B2. ветви reference-типов
# --------------------------------------------------------------------------- #
def test_reference_uuid_conflict_between_objects(tmp_path: Path) -> None:
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "FirstSynthetic", ["identity", SHARED_UUID, "object", "tail"]
    )
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "SecondSynthetic", ["identity", SHARED_UUID, "object", "tail"]
    )

    index = scan_forms(tmp_path)
    conflicts = _with_code(index, SCAN_WARNING_REFERENCE_UUID_CONFLICT)

    assert conflicts, f"ожидался конфликт UUID, получено: {index.scan_warnings}"
    assert all(w.startswith("reference type duplicate UUID") for w in conflicts)
    assert index.resolve_reference_type(SHARED_UUID) is not None


def test_no_conflict_for_distinct_uuids(tmp_path: Path) -> None:
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "FirstSynthetic", ["identity", SHARED_UUID, "object", "tail"]
    )
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "SecondSynthetic", ["identity", OTHER_UUID, "object", "tail"]
    )

    index = scan_forms(tmp_path)

    assert _with_code(index, SCAN_WARNING_REFERENCE_UUID_CONFLICT) == []


def test_reference_metadata_incomplete(tmp_path: Path) -> None:
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "NoUuidSynthetic", ["identity", "not-a-uuid", "object", "tail"]
    )

    index = scan_forms(tmp_path)
    incomplete = _with_code(index, SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE)

    assert incomplete, f"ожидались неполные метаданные, получено: {index.scan_warnings}"
    assert all(w.startswith("reference type metadata is incomplete") for w in incomplete)


# --------------------------------------------------------------------------- #
# B3. ветви external-режима
# --------------------------------------------------------------------------- #
def test_external_layout_precondition(tmp_path: Path) -> None:
    _make_external_processor(tmp_path, "SyntheticProc", "ФормаОк", with_form_bsl=True)
    index = scan_forms(tmp_path, mode="external")
    assert index.total == 1, "layout external-фикстуры не распознан scan_forms"


def test_form_module_missing_external(tmp_path: Path) -> None:
    _make_external_processor(tmp_path, "SyntheticProc", "ФормаОк", with_form_bsl=True)
    _make_external_processor(tmp_path, "SyntheticProc", "ФормаБезМодуля", with_form_bsl=False)

    index = scan_forms(tmp_path, mode="external")
    skipped = [w for w in index.scan_warnings if w.startswith("skipped")]

    assert skipped, f"ожидался пропуск external-формы, получено: {index.scan_warnings}"
    assert all(scan_warning_code(w) == SCAN_WARNING_FORM_MODULE_MISSING for w in skipped)


def test_form_scan_error_external(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_external_processor(tmp_path, "SyntheticProc", "ФормаОк", with_form_bsl=True)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic failure")

    monkeypatch.setattr(sf, "_scan_external_form_dir", boom)
    index = scan_forms(tmp_path, mode="external")

    errors = _with_code(index, SCAN_WARNING_FORM_SCAN_ERROR)
    assert errors, f"ожидалась ошибка обхода external-формы, получено: {index.scan_warnings}"
    assert all(w.startswith("error scanning") for w in errors)


# --------------------------------------------------------------------------- #
# B4. ветви уровня scan_forms
# --------------------------------------------------------------------------- #
def test_elem_discovery_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "v8unpack_agent.managed_forms", None)
    index = scan_forms(tmp_path)

    unavailable = _with_code(index, SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE)
    assert unavailable
    assert unavailable[0].startswith("cannot import discover_elem_forms")


def test_scan_root_invalid(tmp_path: Path) -> None:
    index = scan_forms(tmp_path / "does-not-exist")

    assert index.total == 0
    assert index.forms == []
    assert len(index.scan_warnings) == 1
    warning = index.scan_warnings[0]
    assert warning.startswith("cf_export_root not found or not a directory")
    assert scan_warning_code(warning) == SCAN_WARNING_SCAN_ROOT_INVALID


def test_every_documented_code_has_a_branch_test() -> None:
    """Каждый код перечня закрыт хотя бы одним тестом ветви в этом модуле."""
    covered = {
        SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE,
        SCAN_WARNING_FORM_MODULE_MISSING,
        SCAN_WARNING_FORM_SCAN_ERROR,
        SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE,
        SCAN_WARNING_REFERENCE_UUID_CONFLICT,
        SCAN_WARNING_SCAN_ROOT_INVALID,
    }
    assert covered == set(SCAN_WARNING_CODES)


# --------------------------------------------------------------------------- #
# C. совместимость
# --------------------------------------------------------------------------- #
LEGACY_WARNINGS = ["skipped some/path", "error another/path"]


def _write_legacy_index(path: Path) -> None:
    payload = {
        "total": 0,
        "scanned_at": "2026-01-01T00:00:00+00:00",
        "scan_warnings": list(LEGACY_WARNINGS),
        "forms": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_legacy_index_loads_unchanged(tmp_path: Path) -> None:
    index_path = tmp_path / "forms_scan_index.json"
    _write_legacy_index(index_path)

    loaded = FormScanIndex.load(index_path)

    assert loaded.scan_warnings == LEGACY_WARNINGS
    assert all(scan_warning_code(w) is None for w in loaded.scan_warnings)


def test_legacy_warnings_not_reclassified_on_save(tmp_path: Path) -> None:
    index_path = tmp_path / "forms_scan_index.json"
    _write_legacy_index(index_path)

    loaded = FormScanIndex.load(index_path)
    resaved = tmp_path / "resaved.json"
    loaded.save(resaved)

    data = json.loads(resaved.read_text(encoding="utf-8"))
    assert data["scan_warnings"] == LEGACY_WARNINGS


def test_new_index_round_trip_preserves_codes(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_config_form(root, "Obj1", "FormOk", with_bsl=True)
    _make_config_form(root, "Obj1", "FormNoBsl", with_bsl=False)
    _write_identity_block(
        root, OBJECT_TYPE, "Obj1", ["identity", SHARED_UUID, "object", "tail"]
    )

    index = scan_forms(root)
    out = tmp_path / "forms_scan_index.json"
    index.save(out)
    loaded = FormScanIndex.load(out)

    assert loaded.scan_warnings == index.scan_warnings
    assert _codes(loaded) == _codes(index)
    assert loaded.scanned_at == index.scanned_at
    assert loaded.reference_types == index.reference_types
    assert len(loaded.forms) == len(index.forms)


def test_all_synthetic_warnings_have_codes(tmp_path: Path) -> None:
    _make_config_form(tmp_path, "Obj1", "FormOk", with_bsl=True)
    _make_config_form(tmp_path, "Obj1", "FormNoBsl", with_bsl=False)
    _write_identity_block(
        tmp_path, OBJECT_TYPE, "NoUuidSynthetic", ["identity", "not-a-uuid", "object", "tail"]
    )

    index = scan_forms(tmp_path)

    assert index.scan_warnings, "фикстура не породила ни одного предупреждения"
    assert [c for c in _codes(index) if c is None] == []


def test_scan_warnings_order_is_deterministic(tmp_path: Path) -> None:
    _make_config_form(tmp_path, "Obj1", "FormA", with_bsl=False)
    _make_config_form(tmp_path, "Obj1", "FormB", with_bsl=False)
    _make_config_form(tmp_path, "Obj2", "FormC", with_bsl=False)

    first = scan_forms(tmp_path).scan_warnings
    second = scan_forms(tmp_path).scan_warnings

    assert first == second


def test_form_router_reindex_keeps_warnings(tmp_path: Path) -> None:
    root = tmp_path / "cf_export"
    _make_config_form(root, "Obj1", "FormOk", with_bsl=True)
    _make_config_form(root, "Obj1", "FormNoBsl", with_bsl=False)

    index_path = tmp_path / "forms_scan_index.json"
    index = scan_forms(root)
    index.save(index_path)

    router = FormRouter(index_path)
    router.reindex(index.forms)

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["scan_warnings"] == index.scan_warnings


def test_startswith_skipped_contract_preserved(tmp_path: Path) -> None:
    """Суффикс кода не должен ломать внешние проверки startswith."""
    _make_config_form(tmp_path, "Obj1", "FormNoBsl", with_bsl=False)

    index = scan_forms(tmp_path)

    assert index.scan_warnings
    assert all(w.startswith("skipped") for w in index.scan_warnings)


# --------------------------------------------------------------------------- #
# D. guard документации
# --------------------------------------------------------------------------- #
def _documented_codes() -> list[str]:
    text = DOCS_PATH.read_text(encoding="utf-8")
    assert DOC_START in text and DOC_END in text, "маркеры scan-warning-codes отсутствуют"
    block = text.split(DOC_START, 1)[1].split(DOC_END, 1)[0]
    return re.findall(r"^\|\s*`([A-Z0-9_]+)`\s*\|", block, flags=re.MULTILINE)


def test_documented_codes_match_constants() -> None:
    assert set(_documented_codes()) == set(SCAN_WARNING_CODES)


def test_documented_codes_sorted_and_unique() -> None:
    documented = _documented_codes()
    assert documented == sorted(documented)
    assert len(documented) == len(set(documented))
