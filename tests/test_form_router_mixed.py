"""FormRouter.route() на смешанном индексе: конфигурация + External (#30).

Все фикстуры — синтетические. Нет реальных путей, баз, хостов, имён сотрудников.
"""
import json
from pathlib import Path

import pytest

from v8unpack_agent.form_router import FormRouter
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _config_entry(
    object_type: str,
    object_name: str,
    form_name: str,
    container_name: str | None = None,
) -> FormEntry:
    """Синтетическая запись формы конфигурации."""
    cname = container_name or (object_type + "Form")
    base = Path("cf_export") / object_type / object_name / cname / form_name
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=cname,
        form_name=form_name,
        form_path=base,
        bsl_path=base / (cname + ".obj.bsl"),
        json_path=base / (cname + ".json"),
    )


def _external_entry(
    object_name: str,
    form_name: str,
    object_type: str = "ExternalDataProcessor",
) -> FormEntry:
    """Синтетическая запись формы внешней обработки."""
    base = Path("External") / object_name / "Form" / form_name
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name="Form",
        form_name=form_name,
        form_path=base,
        bsl_path=base / "Form.obj.bsl",
        json_path=base / "Form.json",
    )


# ---------------------------------------------------------------------------
# Смешанный индекс: конфигурация + External, с намеренной коллизией имён
# ---------------------------------------------------------------------------
#
# Коллизия: form_name «ФормаНастроек» есть у конфигурации (Report) и External.
# По правилу #23 ключ = (object_type, object_name, container_name, form_name),
# поэтому две записи с одним form_name, но разными object_type/object_name
# должны ОСТАВАТЬСЯ ОБЕИМИ в matched при route("ФормаНастроек").

MIXED_FORMS = [
    # --- конфигурация ---
    _config_entry("Catalog",  "Counterparties", "ListForm"),
    _config_entry("Catalog",  "Counterparties", "ObjectForm"),
    _config_entry("Document", "GoodsReceipt",   "ListForm"),
    _config_entry("Document", "GoodsReceipt",   "ObjectForm"),
    _config_entry("Report",   "SalesReport",    "ФормаНастроек", "ReportForm"),
    # --- External ---
    _external_entry("SettingsProcessor",  "ФормаНастроек"),   # коллизия по form_name
    _external_entry("DocumentProcessor",  "MainForm"),
    _external_entry("AnotherProcessor",   "ListForm"),        # коллизия по form_name с конфигурацией
]


@pytest.fixture()
def mixed_router(tmp_path: Path) -> FormRouter:
    """FormRouter на смешанном индексе (конфигурация + External)."""
    index_path = tmp_path / "forms_scan_index.json"
    idx = FormScanIndex(
        forms=MIXED_FORMS,
        total=len(MIXED_FORMS),
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    idx.save(index_path)
    return FormRouter(index_path=index_path)


# ---------------------------------------------------------------------------
# Тесты маршрутизации по object_name внешней обработки
# ---------------------------------------------------------------------------

def test_route_by_external_object_name_returns_only_external(
    mixed_router: FormRouter,
) -> None:
    """route() по object_name External-обработки возвращает только её формы."""
    result = mixed_router.route("DocumentProcessor")
    assert result.matched, "Ожидали хотя бы одну запись"
    assert all(
        e.object_type == "ExternalDataProcessor" for e in result.matched
    ), "Не ожидали записей конфигурации"
    assert all(e.object_name == "DocumentProcessor" for e in result.matched)


def test_route_by_external_object_name_confidence(
    mixed_router: FormRouter,
) -> None:
    """Confidence для route() по object_name — не ниже 0.5 (приоритет 2)."""
    result = mixed_router.route("SettingsProcessor")
    assert result.confidence >= 0.5


# ---------------------------------------------------------------------------
# Тесты маршрутизации по object_type
# ---------------------------------------------------------------------------

def test_route_by_external_object_type_returns_only_external(
    mixed_router: FormRouter,
) -> None:
    """route() по object_type=ExternalDataProcessor — только External, ни одной конфигурации."""
    result = mixed_router.route("ExternalDataProcessor")
    assert result.matched, "Ожидали хотя бы одну запись"
    assert all(
        e.object_type == "ExternalDataProcessor" for e in result.matched
    )
    config_types = {"Catalog", "Document", "Report"}
    assert not any(e.object_type in config_types for e in result.matched)


def test_route_by_catalog_type_returns_no_external(
    mixed_router: FormRouter,
) -> None:
    """route() по object_type конфигурации (Catalog) — ни одной External-формы."""
    result = mixed_router.route("Catalog")
    assert result.matched
    assert not any(
        e.object_type == "ExternalDataProcessor" for e in result.matched
    )


def test_route_by_document_type_returns_no_external(
    mixed_router: FormRouter,
) -> None:
    """route() по object_type Document — ни одной External-формы."""
    result = mixed_router.route("Document")
    assert result.matched
    assert not any(
        e.object_type == "ExternalDataProcessor" for e in result.matched
    )


# ---------------------------------------------------------------------------
# Коллизия form_name: обе записи сохраняются, не схлопываются
# ---------------------------------------------------------------------------

def test_collision_form_name_returns_both_config_and_external(
    mixed_router: FormRouter,
) -> None:
    """При коллизии form_name конфигурация и External НЕ схлопываются.

    route("ФормаНастроек") обязан вернуть обе записи: одну из Report/SalesReport
    и одну из ExternalDataProcessor/SettingsProcessor.
    Ключ идентичности: (object_type, object_name, container_name, form_name).
    """
    result = mixed_router.route("ФормаНастроек")
    assert result.confidence == 1.0, "Точное совпадение по form_name → confidence=1.0"
    assert len(result.matched) == 2, (
        f"Ожидали 2 записи (конфигурация + External), получили {len(result.matched)}"
    )
    types = {e.object_type for e in result.matched}
    assert "Report" in types, "Ожидали запись из конфигурации (Report)"
    assert "ExternalDataProcessor" in types, "Ожидали External-запись"


def test_collision_form_name_keys_are_distinct(
    mixed_router: FormRouter,
) -> None:
    """Составные ключи двух записей с одинаковым form_name различны (#23)."""
    result = mixed_router.route("ФормаНастроек")
    keys = [
        (e.object_type, e.object_name, e.container_name, e.form_name)
        for e in result.matched
    ]
    assert len(keys) == len(set(keys)), "Ключи должны быть уникальными"


# ---------------------------------------------------------------------------
# Приоритет: form_name (1.0) > object_name (0.9)
# ---------------------------------------------------------------------------

def test_priority_form_name_wins_over_object_name(
    tmp_path: Path,
) -> None:
    """Если запрос совпадает и по form_name (1.0) и по object_name (0.9),
    возвращается результат с confidence=1.0 (form_name приоритетнее).

    Сценарий: object_name одной записи равен form_name другой.
    """
    # Запись A: object_name="QueryMatch", form_name="SomeForm"
    entry_a = _config_entry("Catalog", "QueryMatch", "SomeForm")
    # Запись B: object_name="OtherCatalog", form_name="QueryMatch"
    entry_b = _config_entry("Catalog", "OtherCatalog", "QueryMatch")

    index_path = tmp_path / "forms_scan_index.json"
    FormScanIndex(
        forms=[entry_a, entry_b],
        total=2,
        scanned_at="2026-01-01T00:00:00+00:00",
    ).save(index_path)

    router = FormRouter(index_path=index_path)
    result = router.route("QueryMatch")

    # form_name точное совпадение → confidence=1.0, не 0.9
    assert result.confidence == 1.0
    assert all(e.form_name == "QueryMatch" for e in result.matched)
    assert len(result.matched) == 1


# ---------------------------------------------------------------------------
# reindex(): External и конфигурация не смешиваются
# ---------------------------------------------------------------------------

def test_reindex_updates_external_without_touching_config(
    mixed_router: FormRouter,
    tmp_path: Path,
) -> None:
    """reindex() обновляет External-запись, не изменяя записи конфигурации."""
    updated_ext = _external_entry("DocumentProcessor", "MainForm")
    updated_ext.warnings = ["reindexed"]

    mixed_router.reindex([updated_ext])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    # External-запись обновилась
    ext_result = router2.route("DocumentProcessor")
    updated = [e for e in ext_result.matched if e.form_name == "MainForm"]
    assert updated, "Ожидали найти обновлённую External-запись"
    assert updated[0].warnings == ["reindexed"]

    # Записи конфигурации (Catalog, Document, Report) не тронуты
    config_result = router2.route("Counterparties")
    assert config_result.matched
    assert all(e.object_type == "Catalog" for e in config_result.matched)


def test_reindex_updates_config_without_touching_external(
    mixed_router: FormRouter,
    tmp_path: Path,
) -> None:
    """reindex() обновляет запись конфигурации, не изменяя External-записи."""
    updated_cfg = _config_entry("Document", "GoodsReceipt", "ObjectForm")
    updated_cfg.warnings = ["cfg-updated"]

    mixed_router.reindex([updated_cfg])

    router2 = FormRouter(index_path=tmp_path / "forms_scan_index.json")
    # Конфиг-запись обновилась
    cfg_result = router2.route("GoodsReceipt")
    updated = [e for e in cfg_result.matched if e.form_name == "ObjectForm"]
    assert updated
    assert updated[0].warnings == ["cfg-updated"]

    # External-записи не тронуты
    ext_result = router2.route("ExternalDataProcessor")
    assert ext_result.matched
    assert all(e.object_type == "ExternalDataProcessor" for e in ext_result.matched)
    assert not any(e.warnings for e in ext_result.matched)


def test_reindex_preserves_total_count_in_mixed_index(
    mixed_router: FormRouter,
    tmp_path: Path,
) -> None:
    """После reindex() общее количество записей не уменьшается (update, не delete)."""
    original_count = len(MIXED_FORMS)

    updated = _external_entry("AnotherProcessor", "ListForm")
    updated.warnings = ["updated"]
    mixed_router.reindex([updated])

    raw = json.loads((tmp_path / "forms_scan_index.json").read_text(encoding="utf-8"))
    assert raw["total"] == original_count
