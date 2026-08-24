"""Issue #172: object_json_path() не поднимается к корню выгрузки.

Контракт: если у ``FormEntry`` отсутствует уровень ``ObjectName``
(``object_name == ""``), объекта-владельца в layout нет — ``object_json_path()``
возвращает ``None`` немедленно, без поиска и без подъёма к корню выгрузки.

Фикстуры не дублируются: 4-level выгрузка и валидный raw-header берутся из
``tests/test_form_context_object_attributes.py`` (``_make_export``,
``_FakeFormEntry``). Схема owner JSON описана в ``object_decoder`` и второго
описания в проекте не получает. Layout без владельца строится копией реальной
директории формы в ``CommonForm/<Форма>``, а «чужой» валидный JSON в корне
выгрузки — копией настоящего owner JSON: так тест 2 проверяет именно опасный
сценарий (валидный header не у той формы), а не отказ декодера.

Покрытие (нумерация из хендовера #172):
1. layout без ObjectName → None, хотя в корне выгрузки есть подходящий *.json;
2. валидный чужой raw-header не попадает в object_attributes;
3. 4-level layout не затронут: путь внутри каталога объекта, decode.ok is True;
4. настоящий owner JSON отсутствует → None и прежнее предупреждение;
5. предупреждение различает «нет владельца по layout» и «файл не найден»;
6. для layout без владельца decode_object_attributes() не вызывается;
7. запись без атрибута object_name не ломает старый сценарий;
8. resolved_relations остаются unresolved, формат списка не меняется.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_form_context_object_attributes import _FakeFormEntry, _make_export
from v8unpack_agent import form_context as form_context_module
from v8unpack_agent.catalog_resolver import clear_object_cache, object_json_path
from v8unpack_agent.form_context import build_form_context
from v8unpack_agent.object_decoder import decode_object_attributes

COMMON = "CommonForm"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Кэш объектных JSON (#148) не переносит состояние между тестами."""
    clear_object_cache()
    yield
    clear_object_cache()


def _owner_export(tmp_path: Path):
    """4-level layout: (root, form_dir, object_dir, entry)."""
    form_dir = Path(_make_export(tmp_path))
    object_dir = form_dir.parents[1]
    entry = _FakeFormEntry(form_path=str(form_dir))
    return tmp_path, form_dir, object_dir, entry


def _owner_json(object_dir: Path) -> Path:
    candidates = sorted(object_dir.glob("*.json"))
    assert candidates, "_make_export должен создавать JSON объекта-владельца"
    return candidates[0]


def _common_form_export(tmp_path: Path):
    """layout без владельца: (root, entry, planted) — чужие JSON в корне.

    ``planted`` — копии валидного owner JSON под именами, которые выбрал бы
    fallback после подъёма на 2 уровня: по имени каталога корня и по имени
    типа метаданных.
    """
    form_dir = Path(_make_export(tmp_path))
    object_dir = form_dir.parents[1]
    source_json = _owner_json(object_dir)

    common_dir = tmp_path / COMMON / form_dir.name
    common_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(form_dir, common_dir)

    planted = [tmp_path / f"{tmp_path.name}.json", tmp_path / f"{COMMON}.json"]
    for dst in planted:
        shutil.copyfile(source_json, dst)

    entry = _FakeFormEntry(
        form_path=str(common_dir),
        object_type=COMMON,
        container_name=COMMON,
        object_name="",
    )
    return tmp_path, entry, planted


# --- Тест 1 ---------------------------------------------------------------
def test_no_object_name_level_returns_none(tmp_path: Path) -> None:
    """Подходящий JSON в корне есть, но владельца по layout нет."""
    _root, entry, planted = _common_form_export(tmp_path)
    assert all(p.exists() for p in planted), "предусловие: кандидаты в корне есть"

    assert object_json_path(entry) is None


# --- Тест 2 ---------------------------------------------------------------
def test_foreign_valid_header_never_reaches_object_attributes(tmp_path: Path) -> None:
    """Валидный чужой raw-header не должен попасть в object_attributes."""
    root, entry, planted = _common_form_export(tmp_path)

    # Файл в корне сам по себе декодируется — до #172 это давало ok=True
    # на постороннем файле вместо HEADER_MISSING.
    assert decode_object_attributes(planted[0]).ok is True

    context = build_form_context(entry, root)

    assert context.object_attributes is None


# --- Тест 3 ---------------------------------------------------------------
def test_four_level_layout_unchanged(tmp_path: Path) -> None:
    """Регрессия: обычный layout резолвится внутри каталога объекта."""
    root, _form_dir, object_dir, entry = _owner_export(tmp_path)
    assert entry.object_name

    resolved = object_json_path(entry)

    assert resolved is not None
    assert resolved.parent == object_dir
    assert resolved.parent != root
    assert resolved == _owner_json(object_dir)
    assert decode_object_attributes(resolved).ok is True


# --- Тест 4 ---------------------------------------------------------------
def test_missing_real_owner_file_keeps_old_warning(tmp_path: Path) -> None:
    """object_name непустой, файла владельца нет → прежняя диагностика."""
    root, _form_dir, object_dir, entry = _owner_export(tmp_path)
    for stale in object_dir.glob("*.json"):
        stale.unlink()
    clear_object_cache()

    assert object_json_path(entry) is None

    warnings = build_form_context(entry, root).metadata["warnings"]
    assert any("файл объекта метаданных не найден" in w for w in warnings)
    assert not any("объект-владелец отсутствует по layout" in w for w in warnings)


# --- Тест 5 ---------------------------------------------------------------
def test_specialized_warning_for_layout_without_owner(tmp_path: Path) -> None:
    """Предупреждение говорит о layout, а не об ошибке декодера."""
    root, entry, _planted = _common_form_export(tmp_path)

    warnings = build_form_context(entry, root).metadata["warnings"]

    assert any("объект-владелец отсутствует по layout" in w for w in warnings)
    assert not any("файл объекта метаданных не найден" in w for w in warnings)
    assert not any("header" in w.lower() for w in warnings)


# --- Тест 6 ---------------------------------------------------------------
def test_decoder_not_called_without_owner(tmp_path: Path, monkeypatch) -> None:
    """Декодер больше не читает посторонний корневой файл."""
    calls: list[object] = []

    def _spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("object_json"))
        raise AssertionError("decode_object_attributes не должен вызываться")

    monkeypatch.setattr(form_context_module, "decode_object_attributes", _spy)

    root, entry, _planted = _common_form_export(tmp_path)
    context = build_form_context(entry, root)

    assert calls == []
    assert context.object_attributes is None


# --- Тест 7 ---------------------------------------------------------------
def test_entry_without_object_name_attribute(tmp_path: Path) -> None:
    """Запись без атрибута object_name идёт по прежнему пути (не == "")."""
    root, entry, _planted = _common_form_export(tmp_path)
    legacy = SimpleNamespace(
        form_path=entry.form_path,
        object_type=COMMON,
        container_name=COMMON,
        form_name=Path(entry.form_path).name,
    )
    assert not hasattr(legacy, "object_name")

    result = object_json_path(legacy)

    assert result is None or isinstance(result, Path)
    assert root.exists()


# --- Тест 8 ---------------------------------------------------------------
def test_resolved_relations_stay_unresolved(tmp_path: Path) -> None:
    """Формат связей не меняется, все остаются resolved=False."""
    root, entry, _planted = _common_form_export(tmp_path)

    relations = build_form_context(entry, root).resolved_relations

    assert isinstance(relations, list)
    assert all(getattr(r, "resolved", False) is False for r in relations)
