"""Компактный LLM-контекст формы: FormContext (issue #77).

Все фикстуры синтетические: реальная выгрузка, контейнер 1С и приватные
метаданные здесь не используются и не требуются.

Проверяемые свойства #77:

1. ``FormEntry`` остаётся карточкой указателей, ``FormContext`` материализует
   содержимое: BSL-текст, ``FormSummary``, компактные ``metadata``;
2. отсутствие BSL и отсутствие ``*.elem.json`` — штатные ситуации, а не ошибки;
3. ``to_llm_prompt_fragment`` физически не может превысить ``max_chars``;
4. ни фрагмент, ни ``metadata`` не публикуют локальные абсолютные пути.

Семантика путей ``FormEntry`` (issue #57): ``form_path`` / ``bsl_path`` /
``json_path`` абсолютные, ``elem_json_path`` — relative-to-root и Optional.
Именно поэтому ``build_form_context`` принимает ``unpacked_root``.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from v8unpack_agent.form_context import (
    FormContext,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import FormEntry

# ---------------------------------------------------------------------------
# Синтетические данные
# ---------------------------------------------------------------------------

OBJECT_TYPE = "Catalog"
OBJECT_NAME = "Справочник1"
CONTAINER = "CatalogForm"
FORM_NAME = "ФормаЭлемента"

BSL_TEXT = (
    "&НаКлиенте\n"
    "Процедура ПриИзменении(Элемент)\n"
    "    Сообщить(\"Реквизит изменён\");\n"
    "КонецПроцедуры\n"
)

ELEM_PAYLOAD = {
    "tree": [
        {
            "name": "Таблица",
            "type": "Table",
            "ПутьКДанным": "Объект.Товары",
        }
    ],
    "data": {
        "-pages-": ["Страница1"],
        "Страница1/Таблица": {"id": 1},
    },
    "props": [
        {
            "name": "Реквизит",
            "type": "String",
        }
    ],
}


def _make_form_dir(
    root: Path,
    *,
    with_bsl: bool = True,
    with_elem: bool = True,
    bsl_text: str = BSL_TEXT,
) -> Path:
    """Создать каталог формы по конвенции config-layout."""
    form_dir = root / OBJECT_TYPE / OBJECT_NAME / CONTAINER / FORM_NAME
    form_dir.mkdir(parents=True, exist_ok=True)
    if with_bsl:
        (form_dir / f"{CONTAINER}.obj.bsl").write_text(bsl_text, encoding="utf-8")
    if with_elem:
        (form_dir / f"{CONTAINER}.elem.json").write_text(
            json.dumps(ELEM_PAYLOAD, ensure_ascii=False),
            encoding="utf-8",
        )
    return form_dir


def _make_entry(
    root: Path,
    form_dir: Path,
    *,
    elem_json_path: Path | None,
    bsl_sha256: str | None = "a" * 64,
    elem_sha256: str | None = "b" * 64,
    warnings: list[str] | None = None,
) -> FormEntry:
    """Карточка указателей ровно с той семантикой путей, что даёт scan_forms."""
    return FormEntry(
        object_type=OBJECT_TYPE,
        object_name=OBJECT_NAME,
        container_name=CONTAINER,
        form_name=FORM_NAME,
        form_path=form_dir.resolve(),
        bsl_path=(form_dir / f"{CONTAINER}.obj.bsl").resolve(),
        json_path=(form_dir / f"{CONTAINER}.json").resolve(),
        warnings=list(warnings or []),
        bsl_mtime=0.0,
        bsl_sha256=bsl_sha256,
        elem_sha256=elem_sha256,
        elem_json_path=elem_json_path,
    )


@pytest.fixture()
def bsl_and_elem(tmp_path: Path) -> tuple[Path, FormEntry]:
    form_dir = _make_form_dir(tmp_path)
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
    )
    return tmp_path, entry


# ---------------------------------------------------------------------------
# Форма с BSL и elem
# ---------------------------------------------------------------------------

def test_context_materializes_bsl_and_summary(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    context = build_form_context(entry, root)

    assert isinstance(context, FormContext)
    assert context.form_name == FORM_NAME
    assert context.container_name == CONTAINER
    assert context.object_type == OBJECT_TYPE
    assert context.object_name == OBJECT_NAME
    assert context.bsl_text == BSL_TEXT
    assert isinstance(context.summary, FormSummary)


def test_bsl_is_read_as_utf8_with_cyrillic(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Кодировка задаётся явно: кириллица не должна портиться."""
    root, entry = bsl_and_elem
    context = build_form_context(entry, root)

    assert context.bsl_text is not None
    assert "Реквизит изменён" in context.bsl_text
    assert "КонецПроцедуры" in context.bsl_text


def test_summary_receives_real_elem_data(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Данные попадают в FormSummary через существующий parse_elem_json."""
    root, entry = bsl_and_elem
    summary = build_form_context(entry, root).summary

    assert {
        "element": "Таблица",
        "target": "Объект.Товары",
        "kind": "data",
    } in summary.relations
    assert {"name": "Реквизит", "type": "String"} in summary.attributes


def test_relative_elem_json_path_is_resolved_against_root(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """elem_json_path относительный (issue #57) — его резолвит unpacked_root."""
    root, entry = bsl_and_elem

    assert entry.elem_json_path is not None
    assert not entry.elem_json_path.is_absolute()
    assert build_form_context(entry, root).summary.relations


def test_context_is_frozen(bsl_and_elem: tuple[Path, FormEntry]) -> None:
    root, entry = bsl_and_elem
    context = build_form_context(entry, root)

    with pytest.raises(FrozenInstanceError):
        context.form_name = "Другая"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Отсутствие BSL: elem-only форма
# ---------------------------------------------------------------------------

def test_missing_bsl_gives_none(tmp_path: Path) -> None:
    """elem-only форма: bsl_path — заглушка на несуществующий файл."""
    form_dir = _make_form_dir(tmp_path, with_bsl=False)
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
        bsl_sha256=None,
        warnings=["elem-only: no .obj.bsl found"],
    )

    context = build_form_context(entry, tmp_path)

    assert context.bsl_text is None
    assert context.summary.relations, "структура elem-only формы должна читаться"


def test_empty_bsl_is_empty_string_not_none(tmp_path: Path) -> None:
    """Пустой файл модуля — это пустая строка, а не отсутствие файла."""
    form_dir = _make_form_dir(tmp_path, bsl_text="")
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
    )

    assert build_form_context(entry, tmp_path).bsl_text == ""


# ---------------------------------------------------------------------------
# Отсутствие elem.json
# ---------------------------------------------------------------------------

def test_missing_elem_json_keeps_parser_warnings(tmp_path: Path) -> None:
    """Нет elem.json — штатный результат: пустые бакеты и warnings из парсера."""
    form_dir = _make_form_dir(tmp_path, with_elem=False)
    entry = _make_entry(tmp_path, form_dir, elem_json_path=None, elem_sha256=None)

    context = build_form_context(entry, tmp_path)

    assert isinstance(context.summary, FormSummary)
    assert context.summary.elements == []
    assert context.summary.relations == []
    assert context.summary.warnings, "предупреждения парсера не должны исчезать"
    assert context.bsl_text == BSL_TEXT


def test_elem_json_path_none_falls_back_to_form_path(tmp_path: Path) -> None:
    """Старый индекс без elem_json_path: каталог формы берётся из form_path."""
    form_dir = _make_form_dir(tmp_path)
    entry = _make_entry(tmp_path, form_dir, elem_json_path=None)

    assert build_form_context(entry, tmp_path).summary.relations


def test_missing_form_dir_does_not_raise(tmp_path: Path) -> None:
    """Каталога формы нет вовсе: контекст строится, данные не выдумываются."""
    form_dir = tmp_path / OBJECT_TYPE / OBJECT_NAME / CONTAINER / FORM_NAME
    entry = _make_entry(tmp_path, form_dir, elem_json_path=None, bsl_sha256=None)

    context = build_form_context(entry, tmp_path)

    assert context.bsl_text is None
    assert context.summary.elements == []


# ---------------------------------------------------------------------------
# metadata: отбор, а не дублирование FormEntry
# ---------------------------------------------------------------------------

EXPECTED_METADATA_KEYS = {
    "form_path",
    "elem_json_path",
    "bsl_sha256",
    "elem_sha256",
    "has_bsl",
    "warnings",
}


def test_metadata_keys_are_exactly_expected(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    assert set(build_form_context(entry, root).metadata) == EXPECTED_METADATA_KEYS


def test_metadata_does_not_duplicate_form_entry(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Out of scope #77: копировать всю структуру FormEntry в metadata."""
    root, entry = bsl_and_elem
    metadata = build_form_context(entry, root).metadata

    for forbidden in ("json_path", "bsl_path", "bsl_mtime", "form_elem_path"):
        assert forbidden not in metadata


def test_metadata_carries_hashes_and_bsl_flag(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    metadata = build_form_context(entry, root).metadata

    assert metadata["bsl_sha256"] == entry.bsl_sha256
    assert metadata["elem_sha256"] == entry.elem_sha256
    assert metadata["has_bsl"] is True


def test_metadata_paths_are_relative_and_posix(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Обезличенность: в metadata только относительные posix-пути."""
    root, entry = bsl_and_elem
    metadata = build_form_context(entry, root).metadata

    expected = f"{OBJECT_TYPE}/{OBJECT_NAME}/{CONTAINER}/{FORM_NAME}"
    assert metadata["form_path"] == expected
    assert metadata["elem_json_path"] == f"{expected}/{CONTAINER}.elem.json"
    assert "\\\\" not in metadata["form_path"]
    assert str(root) not in json.dumps(metadata, ensure_ascii=False)


def test_metadata_has_bsl_false_without_module(tmp_path: Path) -> None:
    form_dir = _make_form_dir(tmp_path, with_bsl=False)
    entry = _make_entry(tmp_path, form_dir, elem_json_path=None, bsl_sha256=None)

    metadata = build_form_context(entry, tmp_path).metadata

    assert metadata["has_bsl"] is False
    assert metadata["bsl_sha256"] is None


# ---------------------------------------------------------------------------
# to_llm_prompt_fragment: порядок, детерминизм, лимит
# ---------------------------------------------------------------------------

def test_fragment_is_deterministic(bsl_and_elem: tuple[Path, FormEntry]) -> None:
    root, entry = bsl_and_elem
    context = build_form_context(entry, root)

    assert to_llm_prompt_fragment(context) == to_llm_prompt_fragment(context)


def test_fragment_puts_summary_before_bsl(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(build_form_context(entry, root))

    assert "## SUMMARY" in fragment
    assert "## BSL" in fragment
    assert fragment.index("## SUMMARY") < fragment.index("## BSL")


def test_fragment_contains_form_identity(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(build_form_context(entry, root))

    assert FORM_NAME in fragment
    assert CONTAINER in fragment


@pytest.mark.parametrize(
    "max_chars", [1, 2, 5, 10, 30, 80, 200, 1000, 8000, 100000]
)
def test_fragment_never_exceeds_limit(
    max_chars: int, bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(
        build_form_context(entry, root), max_chars=max_chars
    )

    assert len(fragment) <= max_chars


def test_fragment_limit_smaller_than_headers(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Лимит меньше заголовков: обрезка, а не исключение."""
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(build_form_context(entry, root), max_chars=3)

    assert len(fragment) <= 3


@pytest.mark.parametrize("max_chars", [0, -2, -8000])
def test_non_positive_limit_gives_empty_string(
    max_chars: int, bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Нулевой и отрицательный лимит: пустая строка, определённое поведение."""
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(
        build_form_context(entry, root), max_chars=max_chars
    )

    assert fragment == ""


def test_default_does_not_truncate_context(tmp_path: Path) -> None:
    form_dir = _make_form_dir(tmp_path, bsl_text="// длинный модуль\n" * 2000)
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
    )
    context = build_form_context(entry, tmp_path)

    default_fragment = to_llm_prompt_fragment(context)
    explicit_unlimited = to_llm_prompt_fragment(context, max_chars=-1)

    assert default_fragment == explicit_unlimited
    assert len(default_fragment) > 8000
    assert default_fragment.endswith("// длинный модуль\n")


def test_fragment_without_bsl_still_has_summary(tmp_path: Path) -> None:
    form_dir = _make_form_dir(tmp_path, with_bsl=False)
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
        bsl_sha256=None,
    )

    fragment = to_llm_prompt_fragment(build_form_context(entry, tmp_path))

    assert "## SUMMARY" in fragment
    assert "Таблица" in fragment


def test_fragment_with_empty_bsl_is_not_truncated_incorrectly(
    tmp_path: Path,
) -> None:
    form_dir = _make_form_dir(tmp_path, bsl_text="")
    entry = _make_entry(tmp_path, form_dir, elem_json_path=None)

    fragment = to_llm_prompt_fragment(build_form_context(entry, tmp_path))

    assert "## SUMMARY" in fragment
    assert len(fragment) <= 8000


def test_fragment_has_no_absolute_local_paths(
    bsl_and_elem: tuple[Path, FormEntry]
) -> None:
    """Обезличенность фрагмента: локальных абсолютных путей в нём нет."""
    root, entry = bsl_and_elem
    fragment = to_llm_prompt_fragment(
        build_form_context(entry, root), max_chars=100000
    )

    assert str(root) not in fragment
    assert str(entry.bsl_path) not in fragment


def test_fragment_prefers_summary_when_bsl_is_huge(tmp_path: Path) -> None:
    """Сначала summary, затем доступная часть BSL — а не наоборот."""
    form_dir = _make_form_dir(tmp_path, bsl_text="// хвост\n" * 5000)
    entry = _make_entry(
        tmp_path,
        form_dir,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
    )

    fragment = to_llm_prompt_fragment(
        build_form_context(entry, tmp_path), max_chars=1200
    )

    assert len(fragment) <= 1200
    assert "## SUMMARY" in fragment
    assert "Таблица" in fragment
