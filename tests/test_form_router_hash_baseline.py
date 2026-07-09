"""Regression-тесты для issue #47.

FormRouter._load_index должен сохранять bsl_sha256 / elem_sha256
для нетронутых форм после частичного reindex().
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.form_router import FormRouter
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_entry(
    form_name: str,
    bsl_sha256: str | None = None,
    elem_sha256: str | None = None,
    bsl_mtime: float = 1000.0,
) -> FormEntry:
    """Синтетическая FormEntry (пути не должны существовать на диске)."""
    return FormEntry(
        object_type="Catalog",
        object_name="TestObject",
        container_name="CatalogForm",
        form_name=form_name,
        form_path=Path(f"/fake/{form_name}"),
        bsl_path=Path(f"/fake/{form_name}/CatalogForm.obj.bsl"),
        json_path=Path(f"/fake/{form_name}/CatalogForm.json"),
        bsl_mtime=bsl_mtime,
        bsl_sha256=bsl_sha256,
        elem_sha256=elem_sha256,
    )


def _write_index(path: Path, entries: list[FormEntry]) -> None:
    """Сохранить индекс с заданными FormEntry через FormScanIndex.save()."""
    idx = FormScanIndex(
        forms=entries,
        total=len(entries),
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    idx.save(path)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestLoadIndexPreservesHashBaseline:
    """FormRouter._load_index восстанавливает bsl_sha256 / elem_sha256."""

    def test_load_index_restores_bsl_sha256(self, tmp_path: Path) -> None:
        """После сохранения и перечитки bsl_sha256 должен совпадать."""
        entry = _make_entry("FormA", bsl_sha256="aaa111", elem_sha256="eee111")
        idx_file = tmp_path / "forms_scan_index.json"
        _write_index(idx_file, [entry])

        loaded_index = FormRouter._load_index(idx_file)
        assert len(loaded_index.forms) == 1
        loaded = loaded_index.forms[0]
        assert loaded.bsl_sha256 == "aaa111", (
            f"bsl_sha256 утерян при _load_index: ожидалось 'aaa111', получено {loaded.bsl_sha256!r}"
        )

    def test_load_index_restores_elem_sha256(self, tmp_path: Path) -> None:
        """После сохранения и перечитки elem_sha256 должен совпадать."""
        entry = _make_entry("FormA", bsl_sha256="aaa111", elem_sha256="eee111")
        idx_file = tmp_path / "forms_scan_index.json"
        _write_index(idx_file, [entry])

        loaded_index = FormRouter._load_index(idx_file)
        loaded = loaded_index.forms[0]
        assert loaded.elem_sha256 == "eee111", (
            f"elem_sha256 утерян при _load_index: ожидалось 'eee111', получено {loaded.elem_sha256!r}"
        )

    def test_load_index_none_hashes_stay_none(self, tmp_path: Path) -> None:
        """Старый индекс без hash-полей → None (обратная совместимость)."""
        entry = _make_entry("FormA", bsl_sha256=None, elem_sha256=None)
        idx_file = tmp_path / "forms_scan_index.json"
        _write_index(idx_file, [entry])

        # убедимся, что JSON действительно содержит null-значения
        raw = json.loads(idx_file.read_text(encoding="utf-8"))
        assert raw["forms"][0]["bsl_sha256"] is None
        assert raw["forms"][0]["elem_sha256"] is None

        loaded_index = FormRouter._load_index(idx_file)
        loaded = loaded_index.forms[0]
        assert loaded.bsl_sha256 is None
        assert loaded.elem_sha256 is None


class TestReindexPreservesHashBaselineForUntouchedForms:
    """FormRouter.reindex() не сбрасывает hash-baseline нетронутых форм."""

    def test_untouched_form_keeps_hashes_after_reindex(self, tmp_path: Path) -> None:
        """Нетронутая форма сохраняет bsl_sha256 / elem_sha256 после reindex.

        Воспроизводит эффект из issue #47:
            BEFORE: [('CatalogForm', 'AAA', 'EEE'), ('DocumentForm', 'BBB', 'FFF')]
            AFTER : [('CatalogForm', None, None), ('DocumentForm', 'BBB2', 'FFF2')]
        """
        form_untouched = _make_entry("FormA", bsl_sha256="AAA", elem_sha256="EEE")
        form_changed = _make_entry("FormB", bsl_sha256="BBB", elem_sha256="FFF")
        form_changed.object_name = "AnotherObject"  # разные ключи

        idx_file = tmp_path / "forms_scan_index.json"
        _write_index(idx_file, [form_untouched, form_changed])

        router = FormRouter(idx_file)

        # Только FormB изменена
        updated_changed = _make_entry("FormB", bsl_sha256="BBB2", elem_sha256="FFF2")
        updated_changed.object_name = "AnotherObject"
        router.reindex([updated_changed])

        # Перечитать сохранённый индекс
        reloaded = json.loads(idx_file.read_text(encoding="utf-8"))
        forms_by_name = {r["form_name"]: r for r in reloaded["forms"]}

        # Нетронутая форма должна сохранить hash
        assert forms_by_name["FormA"]["bsl_sha256"] == "AAA", (
            "reindex() сбросил bsl_sha256 нетронутой формы FormA"
        )
        assert forms_by_name["FormA"]["elem_sha256"] == "EEE", (
            "reindex() сбросил elem_sha256 нетронутой формы FormA"
        )

        # Изменённая форма должна получить новые хэши
        assert forms_by_name["FormB"]["bsl_sha256"] == "BBB2"
        assert forms_by_name["FormB"]["elem_sha256"] == "FFF2"

    def test_untouched_form_no_false_modified_after_reindex(
        self, tmp_path: Path
    ) -> None:
        """После reindex() check_drift() не даёт ложный modified для нетронутой формы."""
        from v8unpack_agent.drift_checker import check_drift

        # BSL-файл создаём на диске
        bsl_dir = tmp_path / "Catalog" / "TestObj" / "CatalogForm" / "FormA"
        bsl_dir.mkdir(parents=True)
        bsl_file = bsl_dir / "CatalogForm.obj.bsl"
        bsl_file.write_text("Процедура Тест() КонецПроцедуры", encoding="utf-8")
        json_file = bsl_dir / "CatalogForm.json"
        json_file.write_text("{}", encoding="utf-8")

        import hashlib
        bsl_content = bsl_file.read_bytes()
        real_hash = hashlib.sha256(bsl_content).hexdigest()

        # FormB — только в памяти (путей нет на диске, но это форма «изменена»)
        form_untouched = FormEntry(
            object_type="Catalog",
            object_name="TestObj",
            container_name="CatalogForm",
            form_name="FormA",
            form_path=bsl_dir,
            bsl_path=bsl_file,
            json_path=json_file,
            bsl_mtime=bsl_file.stat().st_mtime,
            bsl_sha256=real_hash,
            elem_sha256=None,
        )
        form_other = _make_entry("FormB", bsl_sha256="BBB", elem_sha256=None)
        form_other.object_name = "Other"

        idx_file = tmp_path / "forms_scan_index.json"
        _write_index(idx_file, [form_untouched, form_other])

        router = FormRouter(idx_file)

        # Reindex только FormB
        updated_other = _make_entry("FormB", bsl_sha256="BBB2", elem_sha256=None)
        updated_other.object_name = "Other"
        router.reindex([updated_other])

        # Перечитать индекс и проверить drift для FormA
        reloaded_index = FormScanIndex.load(idx_file)
        form_a_reloaded = next(
            e for e in reloaded_index.forms if e.form_name == "FormA"
        )
        result = check_drift(form_a_reloaded)
        assert result.status == "ok", (
            f"Ложный drift после reindex: ожидалось 'ok', получено {result.status!r} "
            f"(bsl_sha256 в индексе: {form_a_reloaded.bsl_sha256!r})"
        )
