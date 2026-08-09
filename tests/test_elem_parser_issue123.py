"""Обезличенность предупреждений парсера elem.json (issue #123).

Дефект обнаружен в #77 (PR #121): тест
``test_fragment_has_no_absolute_local_paths`` упал, потому что абсолютный
путь каталога формы попадал в фрагмент для промпта. Источник — не
``form_context``, а сам парсер: часть предупреждений строится с полным
путём и уезжает всем потребителям ``FormSummary.warnings``.

Проверяемые свойства:

1. ни одно предупреждение ``parse_elem_json`` не содержит корня выгрузки;
2. предупреждение остаётся информативным — по нему видно контейнер и форму;
3. ``FormSummary.warnings`` после ``build_form_summary`` чист даже без
   ``form_context``;
4. строки с разделителем Windows ``\\`` обезличиваются независимо от ОС,
   на которой запущены тесты.

Все фикстуры синтетические: реальная выгрузка не нужна, Windows-пути
явно придуманы. Защита ``form_context._strip_root`` здесь не участвует —
она проверяется своими тестами и остаётся вторым эшелоном.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.form_summary import build_form_summary

# Хвосты каталогов форм: config-layout и external-layout.
_CONFIG_TAIL = ("Catalog", "Справочник1", "CatalogForm", "ФормаЭлемента")
_EXTERNAL_TAIL = ("ext_forms", "ОтчётПродаж", "ReportForm", "Форма")

_ELEM_WITH_DATA = {
    "tree": [{"name": "Таблица", "type": "Table"}],
    "data": {"-pages-": ["Страница1"], "Страница1/Таблица": {"id": "4", "raw": []}},
    "props": [{"name": "Реквизит", "type": "String"}],
}

_ELEM_EMPTY: dict = {"tree": [], "data": {}, "props": []}


def _form_dir(root: Path, tail: tuple[str, ...]) -> Path:
    form_dir = root.joinpath(*tail)
    form_dir.mkdir(parents=True, exist_ok=True)
    return form_dir


def _write_elem(form_dir: Path, payload: object) -> None:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False)
    )
    (form_dir / "Form.elem.json").write_text(text, encoding="utf-8")


def _assert_anonymous(warnings: list[str], root: Path, form_dir: Path) -> None:
    """Ни корня выгрузки, ни его сегментов; форма при этом узнаваема."""
    assert warnings, "предупреждения должны присутствовать"
    text = "\n".join(warnings)

    assert str(root) not in text
    assert str(form_dir) not in text
    for part in root.parts[1:]:
        assert part not in text, f"сегмент корня {part!r} попал в предупреждение"

    assert form_dir.name in text, "по предупреждению должна опознаваться форма"
    assert form_dir.parent.name in text, "контейнер форм должен сохраняться"


# ---------------------------------------------------------------------------
# parse_elem_json: источники предупреждений с путями
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tail", [_CONFIG_TAIL, _EXTERNAL_TAIL], ids=["config", "external"])
def test_missing_elem_json_warning_is_anonymous(
    tmp_path: Path, tail: tuple[str, ...]
) -> None:
    root = tmp_path / "dump"
    form_dir = _form_dir(root, tail)

    result = parse_elem_json(form_dir)

    assert result.elem_index_ok is False
    _assert_anonymous(result.warnings, root, form_dir)


def test_broken_elem_json_warning_is_anonymous(tmp_path: Path) -> None:
    root = tmp_path / "dump"
    form_dir = _form_dir(root, _CONFIG_TAIL)
    _write_elem(form_dir, "{не json")

    result = parse_elem_json(form_dir)

    assert result.elem_index_ok is False
    _assert_anonymous(result.warnings, root, form_dir)


def test_empty_elem_json_warning_is_anonymous(tmp_path: Path) -> None:
    root = tmp_path / "dump"
    form_dir = _form_dir(root, _CONFIG_TAIL)
    _write_elem(form_dir, _ELEM_EMPTY)

    result = parse_elem_json(form_dir)

    assert result.elem_index_ok is False
    _assert_anonymous(result.warnings, root, form_dir)


def test_owner_metadata_warning_is_anonymous(tmp_path: Path) -> None:
    """Метаданных владельца нет: текст не должен печатать каталог формы."""
    root = tmp_path / "dump"
    form_dir = _form_dir(root, _CONFIG_TAIL)
    _write_elem(form_dir, _ELEM_WITH_DATA)

    result = parse_elem_json(form_dir)

    owner_warnings = [w for w in result.warnings if "адельца" in w]
    assert owner_warnings, "предупреждение о метаданных владельца ожидается"
    _assert_anonymous(owner_warnings, root, form_dir)


# ---------------------------------------------------------------------------
# FormSummary.warnings без form_context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tail", [_CONFIG_TAIL, _EXTERNAL_TAIL], ids=["config", "external"])
def test_form_summary_warnings_have_no_dump_root(
    tmp_path: Path, tail: tuple[str, ...]
) -> None:
    """Второй эшелон form_context здесь не участвует принципиально."""
    root = tmp_path / "dump"
    form_dir = _form_dir(root, tail)

    summary = build_form_summary(form_dir)

    _assert_anonymous(summary.warnings, root, form_dir)


# ---------------------------------------------------------------------------
# safe_path_ref: кроссплатформенное обезличивание
# ---------------------------------------------------------------------------


def test_posix_absolute_path_keeps_only_layout_tail() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    ref = safe_path_ref("/home/пользователь/dump/Catalog/Справочник1/CatalogForm/ФормаЭлемента")

    assert ref == ".../Catalog/Справочник1/CatalogForm/ФормаЭлемента"
    assert "пользователь" not in ref
    assert not ref.startswith("/")


def test_windows_drive_path_is_anonymized_on_any_os() -> None:
    """Синтетическая Windows-строка: Path на POSIX её не разберёт."""
    from v8unpack_agent._safe_paths import safe_path_ref

    ref = safe_path_ref(
        "C:\\Users\\пользователь\\dump\\Catalog\\Справочник1\\CatalogForm\\ФормаЭлемента"
    )

    assert ref == ".../Catalog/Справочник1/CatalogForm/ФормаЭлемента"
    assert "C:" not in ref
    assert "Users" not in ref
    assert "\\" not in ref


def test_windows_unc_path_drops_host_and_share() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    ref = safe_path_ref(
        "\\\\сервер\\обмен\\dump\\Catalog\\Справочник1\\CatalogForm\\ФормаЭлемента"
    )

    assert ref == ".../Catalog/Справочник1/CatalogForm/ФормаЭлемента"
    assert "сервер" not in ref
    assert "обмен" not in ref


def test_shallow_absolute_path_keeps_only_last_segment() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    assert safe_path_ref("/home/пользователь/ФормаЭлемента") == ".../ФормаЭлемента"
    assert safe_path_ref("D:\\пользователь\\ФормаЭлемента") == ".../ФормаЭлемента"


def test_relative_path_is_not_distorted() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    assert (
        safe_path_ref("Catalog/Справочник1/CatalogForm/ФормаЭлемента")
        == "Catalog/Справочник1/CatalogForm/ФормаЭлемента"
    )
    assert safe_path_ref("CatalogForm/ФормаЭлемента") == "CatalogForm/ФормаЭлемента"


def test_long_relative_path_is_truncated_to_tail() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    ref = safe_path_ref("dump/Catalog/Справочник1/CatalogForm/ФормаЭлемента")

    assert ref == ".../Catalog/Справочник1/CatalogForm/ФормаЭлемента"


def test_file_path_keeps_file_name() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    ref = safe_path_ref(
        "/dump/Catalog/Справочник1/CatalogForm/ФормаЭлемента/Form.elem.json"
    )

    assert ref.endswith("Form.elem.json")
    assert "ФормаЭлемента" in ref
    assert not ref.startswith("/")


def test_empty_and_none_are_explicit() -> None:
    from v8unpack_agent._safe_paths import UNKNOWN_REF, safe_path_ref

    assert safe_path_ref(None) == UNKNOWN_REF
    assert safe_path_ref("") == UNKNOWN_REF
    assert safe_path_ref("   ") == UNKNOWN_REF


def test_result_is_deterministic_and_posix() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    value = "C:\\dump\\Catalog\\Справочник1\\CatalogForm\\ФормаЭлемента"

    assert safe_path_ref(value) == safe_path_ref(value)
    assert "\\" not in safe_path_ref(value)


def test_tail_zero_keeps_only_name() -> None:
    from v8unpack_agent._safe_paths import safe_path_ref

    assert (
        safe_path_ref("/dump/Catalog/Справочник1/CatalogForm/ФормаЭлемента", tail=0)
        == "ФормаЭлемента"
    )
