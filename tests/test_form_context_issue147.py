"""FormContext прокидывает type_resolver в object_decoder (issue #147).

Фикстуры синтетические: production-подобный raw-header собирается теми же
хелперами, что и tests/test_reference_type_resolution.py (#88), поэтому в
проекте не появляется второго описания layout. Расположение файла объекта
определяет сам ``object_json_path`` — конвенции не дублируются.

Проверяется именно ``FormContext.object_attributes``. ``resolved_relations``
строит ``catalog_resolver.resolve_data_path()`` своим вызовом декодера без
``type_resolver``, поэтому эти связи здесь не являются метрикой (#148).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.catalog_resolver import clear_object_cache, object_json_path
from v8unpack_agent.form_context import build_form_context
from v8unpack_agent.object_decoder import decode_object_attributes
from v8unpack_agent.scan_forms import FormEntry, scan_forms

try:  # зависит от rootdir/conftest
    from tests.test_reference_type_resolution import (
        CATALOG_UUID,
        DOCUMENT_UUID,
        UNKNOWN_UUID,
        _attribute_wrapper,
        _name_entry,
        _write_identity_block,
    )
except ImportError:  # pragma: no cover
    from test_reference_type_resolution import (
        CATALOG_UUID,
        DOCUMENT_UUID,
        UNKNOWN_UUID,
        _attribute_wrapper,
        _name_entry,
        _write_identity_block,
    )

OBJECT_TYPE = "Catalog"
OBJECT_NAME = "Справочник1"
CONTAINER = "CatalogForm"
FORM_NAME = "ФормаЭлемента"

REF_PREFIX = "Ref#"
CATALOG_TYPE_NAME = "CatalogRef.SyntheticCatalog"
DOCUMENT_TYPE_NAME = "DocumentRef.SyntheticDocument"

TS_UUID = "77777777-7777-4777-8777-777777777777"
TS_ATTR_UUID = "88888888-8888-4888-8888-888888888888"

BSL_TEXT = "&НаКлиенте\nПроцедура ПриИзменении(Элемент)\nКонецПроцедуры\n"


class _Resolver:
    """Резолвер с журналом вызовов: проверяет и результат, и контракт входа."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def __call__(self, uuid: str) -> str | None:
        self.calls.append(uuid)
        return self.mapping.get(uuid)


class _FailingResolver:
    """Резолвер, который падает: контракт best-effort должен сохраниться."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, uuid: str) -> str | None:
        self.calls.append(uuid)
        raise ValueError("resolver is broken")


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_object_cache()
    yield
    clear_object_cache()


def _ref_node(uuid: str) -> list:
    """type_node ссылочного типа production-layout."""
    return ['"#"', uuid]


def _tabular_section(uuid: str, name: str, columns: list) -> list:
    """Узел табличной части: [wrapper-head, флаг, counted-контейнер колонок]."""
    descriptor = ["2", _name_entry(uuid, name), ['"Pattern"', ['"U"']]]
    head = ["8", ["0", "0", "0", "0", "0", descriptor]]
    return [head, "0", ["columns", str(len(columns)), *columns]]


def _write_object_json(root: Path, wrappers: list, sections: list | None = None) -> Path:
    """Записать файл объекта там, где его ищет object_json_path."""
    sections = sections or []
    object_dir = root / OBJECT_TYPE / OBJECT_NAME
    object_dir.mkdir(parents=True, exist_ok=True)
    header_root = [
        "1",
        [],
        "0",
        ["sections", str(len(sections)), *sections],
        "0",
        ["properties", str(len(wrappers)), *wrappers],
    ]
    path = object_dir / f"{OBJECT_TYPE}.json"
    path.write_text(json.dumps({"header": [header_root]}), encoding="utf-8")
    return path


def _make_form(root: Path, data_path: str = "Объект.Наименование") -> FormEntry:
    form_dir = root / OBJECT_TYPE / OBJECT_NAME / CONTAINER / FORM_NAME
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{CONTAINER}.obj.bsl").write_text(BSL_TEXT, encoding="utf-8")
    (form_dir / f"{CONTAINER}.elem.json").write_text(
        json.dumps(
            {
                "tree": [
                    {"name": "ПолеРеквизита", "type": "Field", "ПутьКДанным": data_path}
                ],
                "data": {
                    "-pages-": ["Страница1"],
                    "Страница1/ПолеРеквизита": {"id": 1},
                },
                "props": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return FormEntry(
        object_type=OBJECT_TYPE,
        object_name=OBJECT_NAME,
        container_name=CONTAINER,
        form_name=FORM_NAME,
        form_path=form_dir.resolve(),
        bsl_path=(form_dir / f"{CONTAINER}.obj.bsl").resolve(),
        json_path=(form_dir / f"{CONTAINER}.json").resolve(),
        warnings=[],
        bsl_mtime=0.0,
        bsl_sha256="a" * 64,
        elem_sha256="b" * 64,
        elem_json_path=Path(OBJECT_TYPE)
        / OBJECT_NAME
        / CONTAINER
        / FORM_NAME
        / f"{CONTAINER}.elem.json",
    )


def _property_types(object_attributes: dict[str, Any]) -> list[str | None]:
    return [prop.get("Type") for prop in object_attributes.get("Properties", [])]


def _tabular_types(object_attributes: dict[str, Any]) -> list[str | None]:
    types: list[str | None] = []
    for section in object_attributes.get("TabularSections", []):
        types.extend(prop.get("Type") for prop in section.get("Properties", []))
    return types


def _guard_fixture(object_json: Path, expected_types: set[str]) -> None:
    """Фикстура обязана декодироваться и содержать ожидаемые ссылочные типы.

    Без этой проверки провал теста нельзя отличить от развалившейся
    синтетической структуры raw-header.
    """
    result = decode_object_attributes(object_json)
    assert result.ok, f"raw-header фикстура не декодируется: {result.warnings}"
    actual = set(_property_types(result.data)) | set(_tabular_types(result.data))
    missing = expected_types - actual
    assert not missing, f"фикстура не содержит ожидаемых типов: {missing}, есть {actual}"


@pytest.fixture()
def single_reference_form(tmp_path: Path) -> tuple[Path, FormEntry]:
    """Один верхнеуровневый ссылочный реквизит с известным UUID."""
    object_json = _write_object_json(
        tmp_path,
        [_attribute_wrapper(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000001",
            "ОсновнойПоставщик",
            _ref_node(CATALOG_UUID),
        )],
    )
    _guard_fixture(object_json, {REF_PREFIX + CATALOG_UUID})
    entry = _make_form(tmp_path)
    assert object_json_path(entry) is not None, "файл объекта должен быть найден"
    return tmp_path, entry


# 1. Без резолвера — обратная совместимость.
def test_without_resolver_reference_type_is_unchanged(
    single_reference_form: tuple[Path, FormEntry]
) -> None:
    root, entry = single_reference_form
    context = build_form_context(entry, root)

    assert context.object_attributes is not None
    assert _property_types(context.object_attributes) == [REF_PREFIX + CATALOG_UUID]


# 2. С резолвером — успешная замена, резолвер получает UUID без Ref#.
def test_resolver_receives_bare_uuid_and_replaces_type(
    single_reference_form: tuple[Path, FormEntry]
) -> None:
    root, entry = single_reference_form
    resolver = _Resolver({CATALOG_UUID: CATALOG_TYPE_NAME})

    context = build_form_context(entry, root, type_resolver=resolver)

    assert resolver.calls == [CATALOG_UUID]
    assert all(not call.startswith(REF_PREFIX) for call in resolver.calls)
    assert context.object_attributes is not None
    assert _property_types(context.object_attributes) == [CATALOG_TYPE_NAME]


# 3. Неизвестный UUID — безопасный fallback, тип не угадывается.
def test_unknown_uuid_keeps_ref_fallback(tmp_path: Path) -> None:
    object_json = _write_object_json(
        tmp_path,
        [_attribute_wrapper(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000002",
            "Производитель",
            _ref_node(UNKNOWN_UUID),
        )],
    )
    _guard_fixture(object_json, {REF_PREFIX + UNKNOWN_UUID})
    entry = _make_form(tmp_path)
    resolver = _Resolver()

    context = build_form_context(entry, tmp_path, type_resolver=resolver)

    assert resolver.calls == [UNKNOWN_UUID]
    assert context.object_attributes is not None
    assert _property_types(context.object_attributes) == [REF_PREFIX + UNKNOWN_UUID]


# 4. Реквизит табличной части.
def test_tabular_section_attribute_is_resolved(tmp_path: Path) -> None:
    column = _attribute_wrapper(TS_ATTR_UUID, "Контрагент", _ref_node(CATALOG_UUID))
    object_json = _write_object_json(
        tmp_path,
        [_attribute_wrapper(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000003",
            "Наименование",
            ['"S"'],
        )],
        sections=[_tabular_section(TS_UUID, "Товары", [column])],
    )
    _guard_fixture(object_json, {REF_PREFIX + CATALOG_UUID})
    entry = _make_form(tmp_path)
    resolver = _Resolver({CATALOG_UUID: CATALOG_TYPE_NAME})

    context = build_form_context(entry, tmp_path, type_resolver=resolver)

    assert context.object_attributes is not None
    assert _tabular_types(context.object_attributes) == [CATALOG_TYPE_NAME]
    assert resolver.calls == [CATALOG_UUID]


# 5. Нессылочный тип резолверу не передаётся.
def test_primitive_types_are_not_passed_to_resolver(tmp_path: Path) -> None:
    object_json = _write_object_json(
        tmp_path,
        [
            _attribute_wrapper("aaaaaaaa-aaaa-4aaa-8aaa-000000000004", "Код", ['"S"']),
            _attribute_wrapper("aaaaaaaa-aaaa-4aaa-8aaa-000000000005", "Вес", ['"N"']),
            _attribute_wrapper("aaaaaaaa-aaaa-4aaa-8aaa-000000000006", "Флаг", ['"B"']),
        ],
    )
    _guard_fixture(object_json, {"String", "Number", "Boolean"})
    entry = _make_form(tmp_path)
    resolver = _Resolver({CATALOG_UUID: CATALOG_TYPE_NAME})

    context = build_form_context(entry, tmp_path, type_resolver=resolver)

    assert resolver.calls == []
    assert context.object_attributes is not None
    assert set(_property_types(context.object_attributes)) == {
        "String",
        "Number",
        "Boolean",
    }


# 6. Несколько ссылочных реквизитов, детерминированный результат.
def test_multiple_references_are_resolved_deterministically(tmp_path: Path) -> None:
    object_json = _write_object_json(
        tmp_path,
        [
            _attribute_wrapper(
                "aaaaaaaa-aaaa-4aaa-8aaa-000000000007",
                "Контрагент",
                _ref_node(CATALOG_UUID),
            ),
            _attribute_wrapper(
                "aaaaaaaa-aaaa-4aaa-8aaa-000000000008",
                "ДокументОснование",
                _ref_node(DOCUMENT_UUID),
            ),
        ],
    )
    _guard_fixture(
        object_json, {REF_PREFIX + CATALOG_UUID, REF_PREFIX + DOCUMENT_UUID}
    )
    entry = _make_form(tmp_path)
    mapping = {CATALOG_UUID: CATALOG_TYPE_NAME, DOCUMENT_UUID: DOCUMENT_TYPE_NAME}

    first = build_form_context(entry, tmp_path, type_resolver=_Resolver(mapping))
    second = build_form_context(entry, tmp_path, type_resolver=_Resolver(mapping))

    assert first.object_attributes is not None
    assert second.object_attributes is not None
    assert sorted(_property_types(first.object_attributes)) == [
        CATALOG_TYPE_NAME,
        DOCUMENT_TYPE_NAME,
    ]
    assert json.dumps(
        first.object_attributes, ensure_ascii=False, sort_keys=True
    ) == json.dumps(second.object_attributes, ensure_ascii=False, sort_keys=True)


# 7. Отсутствующий файл объекта: резолвер не вызывается.
def test_missing_object_json_does_not_call_resolver(tmp_path: Path) -> None:
    entry = _make_form(tmp_path)
    resolver = _Resolver({CATALOG_UUID: CATALOG_TYPE_NAME})

    context = build_form_context(entry, tmp_path, type_resolver=resolver)

    assert context.object_attributes is None
    assert resolver.calls == []
    assert context.metadata["warnings"], "отсутствие файла объекта фиксируется warning"


# 8. Ошибка декодирования raw-header: best-effort контракт сохраняется.
def test_broken_raw_header_keeps_best_effort_contract(tmp_path: Path) -> None:
    object_dir = tmp_path / OBJECT_TYPE / OBJECT_NAME
    object_dir.mkdir(parents=True, exist_ok=True)
    (object_dir / f"{OBJECT_TYPE}.json").write_text(
        json.dumps({"header": {"unsupported": True}}), encoding="utf-8"
    )
    entry = _make_form(tmp_path)
    resolver = _Resolver({CATALOG_UUID: CATALOG_TYPE_NAME})

    context = build_form_context(entry, tmp_path, type_resolver=resolver)

    assert context.object_attributes is None
    assert resolver.calls == []
    assert context.summary is not None, "FormContext строится по действующим правилам"
    assert context.metadata["warnings"]


# 9. Совместимость старых вызовов и keyword-only контракт.
def test_legacy_positional_call_stays_supported(
    single_reference_form: tuple[Path, FormEntry]
) -> None:
    root, entry = single_reference_form

    legacy = build_form_context(entry, root)

    assert legacy.object_attributes is not None
    with pytest.raises(TypeError):
        build_form_context(entry, root, lambda uuid: CATALOG_TYPE_NAME)  # type: ignore[misc]


# 10. Интеграция с реальным FormScanIndex.
def test_real_scan_index_resolver_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write_identity_block(
        root, "Catalog", "SyntheticCatalog", ["identity", "object", CATALOG_UUID]
    )
    object_json = _write_object_json(
        root,
        [_attribute_wrapper(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000009",
            "Импортер",
            _ref_node(CATALOG_UUID),
        )],
    )
    _guard_fixture(object_json, {REF_PREFIX + CATALOG_UUID})
    entry = _make_form(root)

    index = scan_forms(root)
    assert index.resolve_reference_type(CATALOG_UUID) == CATALOG_TYPE_NAME

    context = build_form_context(
        entry, root, type_resolver=index.resolve_reference_type
    )

    assert context.object_attributes is not None
    assert _property_types(context.object_attributes) == [CATALOG_TYPE_NAME]
    assert index.reference_types == {CATALOG_UUID: CATALOG_TYPE_NAME}, "индекс не мутирован"


# 11. Падение резолвера не ломает сборку контекста (контракт #88).
def test_failing_resolver_is_reported_and_context_is_built(
    single_reference_form: tuple[Path, FormEntry]
) -> None:
    root, entry = single_reference_form
    resolver = _FailingResolver()

    context = build_form_context(entry, root, type_resolver=resolver)

    assert resolver.calls == [CATALOG_UUID]
    assert context.object_attributes is not None
    assert _property_types(context.object_attributes) == [REF_PREFIX + CATALOG_UUID]
    assert any(
        "REF_RESOLVER_FAILED" in warning for warning in context.metadata["warnings"]
    )
