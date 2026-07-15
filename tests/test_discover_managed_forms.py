"""Тесты discovery управляемых форм (issue #55).

Все тесты синтетические: файлы генерируются в tmp_path через фикстуры из
tests/_managed_fixtures.py. Нет ссылок на реальные базы, серверы, хосты
или абсолютные пути.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _managed_fixtures import make_managed_form_elem_json, write_managed_form_elem

from v8unpack_agent.managed_forms import ManagedFormEntry, discover_managed_forms


# ---------------------------------------------------------------------------
# Вспомогательная утилита
# ---------------------------------------------------------------------------


def _rel_posix(entry: ManagedFormEntry) -> str:
    """Нормализованный относительный путь *.elem.json через косую черту."""
    return PurePosixPath(entry.elem_json_path).as_posix()


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestDiscoverSingleForm:
    """Тест 1 — discovery одной управляемой формы (CatalogForm layout)."""

    def test_single_form_found(self, tmp_path: Path) -> None:
        payload = make_managed_form_elem_json(pages=["Страница1"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Catalog",
            object_name="Банки",
            form_name="ФормаЭлементаУправляемая",
            payload=payload,
        )

        results = discover_managed_forms(tmp_path)

        assert len(results) == 1
        entry = results[0]
        # *.elem.json найден
        assert entry.elem_json_path.name == "ФормаЭлементаУправляемая.elem.json"
        # путь относительный (нет tmp_path)
        assert tmp_path not in entry.elem_json_path.parents
        # сопутствующие артефакты присутствуют (write_aux=True по умолчанию)
        assert entry.aux_json_path is not None
        assert entry.bsl_path is not None


class TestDiscoverMultipleLayouts:
    """Тест 2 — discovery нескольких форм в разных layout-вариантах."""

    def test_multiple_layouts(self, tmp_path: Path) -> None:
        cases = [
            # (object_type, object_name, form_name) — суффикс определяется _detect_form_suffix
            ("Catalog", "Банки", "ФормаЭлементаУправляемая"),      # → CatalogForm
            ("external_managed", "ВнешнийОтчетУправляемый", "ФормаОтчетаУправляемая"),  # → ReportForm
            ("external_managed", "ВнешняяОбработкаУпр", "ФормаВнешняяУправляемая"),    # → Form
        ]
        expected_names = set()
        for obj_type, obj_name, form_name in cases:
            payload = make_managed_form_elem_json(pages=["Страница1"])
            write_managed_form_elem(
                root=tmp_path,
                object_type=obj_type,
                object_name=obj_name,
                form_name=form_name,
                payload=payload,
            )
            expected_names.add(f"{form_name}.elem.json")

        results = discover_managed_forms(tmp_path)

        assert len(results) == 3
        found_names = {r.elem_json_path.name for r in results}
        assert found_names == expected_names


class TestDiscoverNoForms:
    """Тест 3 — discovery в дереве без управляемых форм."""

    def test_empty_tree_returns_empty_list(self, tmp_path: Path) -> None:
        # Создаём дерево с *.bsl, но без *.elem.json — не управляемые формы
        form_dir = tmp_path / "Catalog" / "Контрагенты" / "CatalogForm" / "ФормаСписка"
        form_dir.mkdir(parents=True)
        (form_dir / "CatalogForm.obj.bsl").write_text("// обычная форма", encoding="utf-8")

        results = discover_managed_forms(tmp_path)

        assert results == []

    def test_nonexistent_root_returns_empty_list(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        results = discover_managed_forms(missing)
        assert results == []


class TestRelativePaths:
    """Тест 4 — стабильность и относительность путей."""

    def test_paths_are_relative_to_root(self, tmp_path: Path) -> None:
        payload = make_managed_form_elem_json()
        elem_path = write_managed_form_elem(
            root=tmp_path,
            object_type="Catalog",
            object_name="ТестОбъект",
            form_name="ФормаЭлементаУправляемая",
            payload=payload,
        )

        results = discover_managed_forms(tmp_path)
        assert len(results) == 1

        # elem_json_path должен быть relative (не absolute)
        assert not results[0].elem_json_path.is_absolute()
        # восстановленный абсолютный путь совпадает с тем, что вернул write_managed_form_elem
        assert (tmp_path / results[0].elem_json_path).resolve() == elem_path.resolve()

    def test_path_stability_two_calls(self, tmp_path: Path) -> None:
        """Один и тот же корень → одинаковый список путей при двух вызовах."""
        payload = make_managed_form_elem_json(pages=["Страница1", "Страница2"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Document",
            object_name="ЗаказКлиента",
            form_name="ФормаДокументаУправляемая",
            payload=payload,
        )

        first = [_rel_posix(e) for e in discover_managed_forms(tmp_path)]
        second = [_rel_posix(e) for e in discover_managed_forms(tmp_path)]
        assert first == second


class TestUnicodePaths:
    """Тест 5 — UTF-8 / кириллические имена каталогов."""

    def test_cyrillic_names(self, tmp_path: Path) -> None:
        payload = make_managed_form_elem_json(pages=["ОсновнаяСтраница"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="ОбщаяФорма",
            object_name="ВводПароля",
            form_name="ФормаВводаПароляУправляемая",
            payload=payload,
        )

        results = discover_managed_forms(tmp_path)

        assert len(results) == 1
        posix_path = _rel_posix(results[0])
        # Кириллица в пути не ломается
        assert "ОбщаяФорма" in posix_path
        assert "ВводПароля" in posix_path
        assert "ФормаВводаПароляУправляемая" in posix_path
