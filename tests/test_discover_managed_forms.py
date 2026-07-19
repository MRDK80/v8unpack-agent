"""Тесты discovery форм по *.elem.json (issue #55).

Все тесты синтетические: файлы генерируются в tmp_path через фикстуры из
tests/_managed_fixtures.py. Нет ссылок на реальные базы, серверы, хосты
или абсолютные пути.

Реальная схема v8unpack 1.2.11 (без --descent):
    <stem>.elem.json  — структура (обязательный)
    <stem>.json       — метаданные  → meta_json_path
    <stem>.id.json    — UUID         → id_json_path
    <stem>.obj.bsl    — BSL-модуль   → bsl_path
Числовой descent (<stem>.obj.<num>.bsl и т.п.) — опциональный слой для
распаковок с --descent; складывается в descent_artifacts.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _managed_fixtures import (
    make_managed_form_elem_json,
    write_managed_form_elem,
    write_aux_artifacts,
)

from v8unpack_agent.managed_forms import (
    DescentArtifacts,
    ElemFormEntry,
    discover_elem_forms,
)

# Обратная совместимость: старые имена остаются рабочими (deprecated aliases)
from v8unpack_agent.managed_forms import ManagedFormEntry, discover_managed_forms  # noqa: F401

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _rel_posix(entry: ElemFormEntry) -> str:
    """Нормализованный относительный путь *.elem.json через косую черту."""
    return PurePosixPath(entry.elem_json_path).as_posix()


def _make_form_dir(tmp_path: Path, container: str, form_name: str) -> Path:
    """Создать каталог формы <container>/<form_name>/ с *.elem.json."""
    form_dir = tmp_path / container / form_name
    form_dir.mkdir(parents=True)
    (form_dir / f"{form_name}.elem.json").write_text(
        '{"items": []}', encoding="utf-8"
    )
    return form_dir


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestDiscoverSingleForm:
    """Тест 1 — discovery одной формы (реальная схема артефактов)."""

    def test_single_form_found(self, tmp_path: Path) -> None:
        payload = make_managed_form_elem_json(pages=["Страница1"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Catalog",
            object_name="Банки",
            form_name="ФормаЭлементаУправляемая",
            payload=payload,
        )
        results = discover_elem_forms(tmp_path)

        assert len(results) == 1
        entry = results[0]
        assert entry.elem_json_path.name == "ФормаЭлементаУправляемая.elem.json"
        assert tmp_path not in entry.elem_json_path.parents
        # реальная схема: meta / id / bsl заполнены, descent нет
        assert entry.meta_json_path is not None
        assert entry.id_json_path is not None
        assert entry.bsl_path is not None
        assert entry.descent_artifacts == []

    def test_deprecated_aux_json_points_to_meta(self, tmp_path: Path) -> None:
        """DEPRECATED aux_json_path → meta_json_path."""
        payload = make_managed_form_elem_json(pages=["Страница1"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Catalog",
            object_name="Банки",
            form_name="ФормаЭлементаУправляемая",
            payload=payload,
        )
        entry = discover_elem_forms(tmp_path)[0]
        assert entry.aux_json_path == entry.meta_json_path


class TestDiscoverMultipleLayouts:
    """Тест 2 — discovery нескольких форм в разных layout-вариантах."""

    def test_multiple_layouts(self, tmp_path: Path) -> None:
        cases = [
            ("Catalog", "Банки", "ФормаЭлементаУправляемая"),
            ("external_managed", "ВнешнийОтчетУправляемый", "ФормаОтчетаУправляемая"),
            ("external_managed", "ВнешняяОбработкаУпр", "ФормаВнешняяУправляемая"),
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

        results = discover_elem_forms(tmp_path)

        assert len(results) == 3
        found_names = {r.elem_json_path.name for r in results}
        assert found_names == expected_names


class TestDiscoverNoForms:
    """Тест 3 — discovery в дереве без форм с *.elem.json."""

    def test_empty_tree_returns_empty_list(self, tmp_path: Path) -> None:
        form_dir = tmp_path / "Catalog" / "Контрагенты" / "CatalogForm" / "ФормаСписка"
        form_dir.mkdir(parents=True)
        (form_dir / "CatalogForm.obj.bsl").write_text("// обычная форма", encoding="utf-8")

        results = discover_elem_forms(tmp_path)
        assert results == []

    def test_nonexistent_root_returns_empty_list(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert discover_elem_forms(missing) == []


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
        results = discover_elem_forms(tmp_path)
        assert len(results) == 1
        assert not results[0].elem_json_path.is_absolute()
        assert (tmp_path / results[0].elem_json_path).resolve() == elem_path.resolve()

    def test_path_stability_two_calls(self, tmp_path: Path) -> None:
        payload = make_managed_form_elem_json(pages=["Страница1", "Страница2"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Document",
            object_name="ЗаказКлиента",
            form_name="ФормаДокументаУправляемая",
            payload=payload,
        )
        first = [_rel_posix(e) for e in discover_elem_forms(tmp_path)]
        second = [_rel_posix(e) for e in discover_elem_forms(tmp_path)]
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
        results = discover_elem_forms(tmp_path)
        assert len(results) == 1
        posix_path = _rel_posix(results[0])
        assert "ОбщаяФорма" in posix_path
        assert "ВводПароля" in posix_path
        assert "ФормаВводаПароляУправляемая" in posix_path


class TestCommonFormLayout:
    """Тест 6 — 3-уровневый layout CommonForm."""

    def test_commonform_3level_layout(self, tmp_path: Path) -> None:
        form_dir = tmp_path / "CommonForm" / "ФормаВыбора"
        form_dir.mkdir(parents=True)
        (form_dir / "CommonForm.elem.json").write_text('{"items": []}', encoding="utf-8")

        results = discover_elem_forms(tmp_path)
        assert len(results) == 1
        assert results[0].elem_json_path == Path("CommonForm") / "ФормаВыбора" / "CommonForm.elem.json"
        assert not results[0].elem_json_path.is_absolute()

    def test_commonform_mixed_with_4level(self, tmp_path: Path) -> None:
        cf_dir = tmp_path / "CommonForm" / "ВводПароля"
        cf_dir.mkdir(parents=True)
        (cf_dir / "CommonForm.elem.json").write_text('{"items": []}', encoding="utf-8")

        payload = make_managed_form_elem_json(pages=["Страница1"])
        write_managed_form_elem(
            root=tmp_path,
            object_type="Catalog",
            object_name="Банки",
            form_name="ФормаЭлементаУправляемая",
            payload=payload,
        )
        results = discover_elem_forms(tmp_path)
        assert len(results) == 2
        paths = {_rel_posix(e) for e in results}
        assert "CommonForm/ВводПароля/CommonForm.elem.json" in paths
        assert any("Банки" in p for p in paths)


class TestExternalObjectLayout:
    """Тест 7 — 3-уровневый layout внешнего объекта (Form / ReportForm)."""

    def test_external_form_layout(self, tmp_path: Path) -> None:
        form_dir = tmp_path / "ВнешняяОбработкаУпр" / "Form" / "ФормаВнешняяУправляемая"
        form_dir.mkdir(parents=True)
        (form_dir / "Form.elem.json").write_text('{"items": []}', encoding="utf-8")

        results = discover_elem_forms(tmp_path)
        assert len(results) == 1
        assert _rel_posix(results[0]) == "ВнешняяОбработкаУпр/Form/ФормаВнешняяУправляемая/Form.elem.json"

    def test_external_report_layout(self, tmp_path: Path) -> None:
        form_dir = tmp_path / "ВнешнийОтчетУправляемый" / "ReportForm" / "ФормаОтчетаУправляемая"
        form_dir.mkdir(parents=True)
        (form_dir / "ReportForm.elem.json").write_text('{"items": []}', encoding="utf-8")

        results = discover_elem_forms(tmp_path)
        assert len(results) == 1
        assert "ReportForm" in _rel_posix(results[0])

    def test_external_two_objects(self, tmp_path: Path) -> None:
        for obj, container, form in [
            ("ВнешняяОбработкаУпр", "Form", "ФормаВнешняяУправляемая"),
            ("ВнешнийОтчетУправляемый", "ReportForm", "ФормаОтчетаУправляемая"),
        ]:
            d = tmp_path / obj / container / form
            d.mkdir(parents=True)
            (d / f"{container}.elem.json").write_text('{"items": []}', encoding="utf-8")

        results = discover_elem_forms(tmp_path)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Тест 8 — реальная схема артефактов (v8unpack 1.2.11, без --descent)
# ---------------------------------------------------------------------------


class TestRealArtifactScheme:
    """Бессуффиксные <stem>.json / <stem>.id.json / <stem>.obj.bsl."""

    def _write_real(self, form_dir: Path, stem: str, *, with_bsl: bool = True) -> None:
        (form_dir / f"{stem}.json").write_text('{"meta": 1}', encoding="utf-8")
        (form_dir / f"{stem}.id.json").write_text(
            '{"uuid": "00000000-0000-0000-0000-000000000000"}', encoding="utf-8"
        )
        if with_bsl:
            (form_dir / f"{stem}.obj.bsl").write_text("// bsl\n", encoding="utf-8")

    def test_meta_id_bsl_populated(self, tmp_path: Path) -> None:
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        self._write_real(form_dir, "CatalogForm")

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.meta_json_path is not None
        assert entry.id_json_path is not None
        assert entry.bsl_path is not None
        assert entry.bsl_path.name == "CatalogForm.obj.bsl"
        assert entry.descent_artifacts == []
        assert entry.extra_warnings == []

    def test_form_without_bsl(self, tmp_path: Path) -> None:
        """Форма без *.obj.bsl — bsl_path None, остальное на месте."""
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        self._write_real(form_dir, "CatalogForm", with_bsl=False)

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.bsl_path is None
        assert entry.meta_json_path is not None
        assert entry.id_json_path is not None

    def test_manager_module_ignored(self, tmp_path: Path) -> None:
        """<stem>.mgr.bsl (модуль менеджера) не считается модулем формы."""
        form_dir = _make_form_dir(tmp_path, "DocumentForm", "DocumentForm")
        (form_dir / "DocumentForm.mgr.bsl").write_text("// manager\n", encoding="utf-8")

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.bsl_path is None
        assert entry.descent_artifacts == []

    def test_id_json_not_treated_as_descent(self, tmp_path: Path) -> None:
        """<stem>.id.json → id_json_path, а не descent-набор."""
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        (form_dir / "CatalogForm.id.json").write_text('{"uuid": "x"}', encoding="utf-8")

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.id_json_path is not None
        assert entry.descent_artifacts == []

    def test_elem_only_no_artifacts(self, tmp_path: Path) -> None:
        """Только *.elem.json — все опциональные поля None/пустые."""
        _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.meta_json_path is None
        assert entry.id_json_path is None
        assert entry.bsl_path is None
        assert entry.descent_artifacts == []
        assert entry.extra_warnings == []


# ---------------------------------------------------------------------------
# Тест 9 — числовой descent (чужой сценарий с --descent)
# ---------------------------------------------------------------------------


class TestDescentArtifacts:
    """Опциональный слой для распаковок с числовым --descent."""

    def test_descent_simple_10(self, tmp_path: Path) -> None:
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        write_aux_artifacts(form_dir, "CatalogForm", "10")

        entry = discover_elem_forms(tmp_path)[0]
        assert len(entry.descent_artifacts) == 1
        da = entry.descent_artifacts[0]
        assert da.descent == "10"
        assert da.aux_json_path is not None
        assert da.bsl_path is not None
        # bsl_path подхватывает descent-BSL при отсутствии бессуффиксного
        assert entry.bsl_path is not None

    def test_descent_four_component(self, tmp_path: Path) -> None:
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        write_aux_artifacts(form_dir, "CatalogForm", "3.0.75.100")

        da = discover_elem_forms(tmp_path)[0].descent_artifacts
        assert len(da) == 1
        assert da[0].descent == "3.0.75.100"
        assert da[0].aux_json_path is not None
        assert da[0].bsl_path is not None

    def test_json_bsl_matched_same_descent(self, tmp_path: Path) -> None:
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        write_aux_artifacts(form_dir, "CatalogForm", "10")

        da = discover_elem_forms(tmp_path)[0].descent_artifacts
        assert len(da) == 1
        assert da[0].aux_json_path is not None
        assert da[0].bsl_path is not None

    def test_different_descent_not_merged(self, tmp_path: Path) -> None:
        """Два разных числовых descent (10 и 4) — два набора."""
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        write_aux_artifacts(form_dir, "CatalogForm", "10", with_bsl=False)
        write_aux_artifacts(form_dir, "CatalogForm", "4", with_json=False)

        da = discover_elem_forms(tmp_path)[0].descent_artifacts
        assert {d.descent for d in da} == {"10", "4"}

    def test_multiple_descents_warns(self, tmp_path: Path) -> None:
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        write_aux_artifacts(form_dir, "CatalogForm", "10")
        write_aux_artifacts(form_dir, "CatalogForm", "3.0.75.100")

        entry = discover_elem_forms(tmp_path)[0]
        assert {d.descent for d in entry.descent_artifacts} == {"10", "3.0.75.100"}
        assert any("descent" in w for w in entry.extra_warnings)

    def test_plain_bsl_wins_over_descent(self, tmp_path: Path) -> None:
        """Бессуффиксный obj.bsl приоритетнее descent-BSL в bsl_path."""
        form_dir = _make_form_dir(tmp_path, "CatalogForm", "CatalogForm")
        (form_dir / "CatalogForm.obj.bsl").write_text("// plain\n", encoding="utf-8")
        write_aux_artifacts(form_dir, "CatalogForm", "10", with_json=False)

        entry = discover_elem_forms(tmp_path)[0]
        assert entry.bsl_path.name == "CatalogForm.obj.bsl"
        assert entry.descent_artifacts[0].descent == "10"