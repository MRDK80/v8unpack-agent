"""Интеграция FormContext.resolved_relations с raw-header layout (issue #148).

Дополняет tests/test_form_context.py, не меняя его фикстур (#77): здесь
строится форма, чей ``data_path`` заведомо совпадает с реквизитом из
production-подобного raw-header файла объекта. Имя реквизита берётся из
``decode_object_attributes``, расположение файла объекта определяет сам
``object_json_path`` — конвенции не дублируются.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.catalog_resolver import clear_object_cache, object_json_path
from v8unpack_agent.form_context import (
    NO_OBJECT_PLACEHOLDER,
    OBJECT_ATTRIBUTES_MARKER,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.object_decoder import DecodeError, decode_object_attributes
from v8unpack_agent.scan_forms import FormEntry

try:  # зависит от rootdir/conftest
    from tests.test_object_decoder import MINIMAL_CATALOG_WITH_TS
except ImportError:  # pragma: no cover
    from test_object_decoder import MINIMAL_CATALOG_WITH_TS

OBJECT_TYPE = "Catalog"
OBJECT_NAME = "Справочник1"
CONTAINER = "CatalogForm"
FORM_NAME = "ФормаЭлемента"

BSL_TEXT = "&НаКлиенте\nПроцедура ПриИзменении(Элемент)\nКонецПроцедуры\n"


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_object_cache()
    yield
    clear_object_cache()


def _make_form(root: Path, data_path: str) -> FormEntry:
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


def _write_raw_object_json(root: Path) -> None:
    object_dir = root / OBJECT_TYPE / OBJECT_NAME
    object_dir.mkdir(parents=True, exist_ok=True)
    (object_dir / f"{OBJECT_TYPE}.json").write_text(
        json.dumps(MINIMAL_CATALOG_WITH_TS, ensure_ascii=False), encoding="utf-8"
    )


def _top_property(root: Path) -> dict:
    result = decode_object_attributes(
        root / OBJECT_TYPE / OBJECT_NAME / f"{OBJECT_TYPE}.json"
    )
    assert result.ok, "raw-header фикстура должна декодироваться"
    props = result.data.get("Properties") or []
    assert props, "фикстура должна содержать верхнеуровневые реквизиты"
    return props[0]


@pytest.fixture()
def raw_header_form(tmp_path: Path) -> tuple[Path, FormEntry, dict]:
    _write_raw_object_json(tmp_path)
    prop = _top_property(tmp_path)
    entry = _make_form(tmp_path, f"Объект.{prop['Name']}")
    assert object_json_path(entry) is not None, "файл объекта должен быть найден"
    return tmp_path, entry, prop


def test_known_data_relation_is_resolved(
    raw_header_form: tuple[Path, FormEntry, dict]
) -> None:
    """Регрессия #148: на raw-header layout связь должна резолвиться."""
    root, entry, prop = raw_header_form
    relations = build_form_context(entry, root).resolved_relations

    assert relations, "форма должна давать хотя бы одну resolved-запись"
    resolved = [rel for rel in relations if rel.get("resolved")]
    assert resolved, "ни одна data-связь не резолвится на raw-header layout"
    assert resolved[0].get("value_type") == prop.get("Type")
    assert resolved[0].get("synonym") == prop.get("Synonym")


def test_unknown_data_relation_stays_unresolved(tmp_path: Path) -> None:
    """Данные не выдумываются: несуществующий реквизит остаётся resolved=False."""
    _write_raw_object_json(tmp_path)
    entry = _make_form(tmp_path, "Объект.НетТакогоРеквизита")

    for rel in build_form_context(entry, tmp_path).resolved_relations:
        assert rel.get("resolved") is False
        assert rel.get("synonym") is None
        assert rel.get("value_type") is None


def test_missing_object_json_is_fail_safe(tmp_path: Path) -> None:
    """Файла объекта нет: контекст строится, исключений нет."""
    entry = _make_form(tmp_path, "Объект.Наименование")
    context = build_form_context(entry, tmp_path)

    assert context.summary.relations, "структура формы должна читаться"
    for rel in context.resolved_relations:
        assert rel.get("resolved") is False


def test_resolved_relations_format_and_anonymity(
    raw_header_form: tuple[Path, FormEntry, dict]
) -> None:
    """Формат resolved_relations не менялся, абсолютных путей в нём нет."""
    root, entry, _ = raw_header_form

    for rel in build_form_context(entry, root).resolved_relations:
        assert "resolved" in rel
        assert "synonym" in rel
        assert str(root) not in json.dumps(rel, ensure_ascii=False)


def test_resolved_relations_are_deterministic(
    raw_header_form: tuple[Path, FormEntry, dict]
) -> None:
    """Кэш декодирования не меняет результат между вызовами."""
    root, entry, _ = raw_header_form

    first = build_form_context(entry, root).resolved_relations
    second = build_form_context(entry, root).resolved_relations

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


#: Нормализованный owner JSON — это *выход* декодера, а не его вход (#160).
#: Значения синтетические и специально узнаваемые: если они попадут в промпт,
#: assert ниже это поймает.
NORMALIZED_OWNER_PAYLOAD = {
    "Properties": [
        {
            "Name": "SyntheticAttribute",
            "Type": "String",
            "Synonym": "Synthetic attribute",
        }
    ],
    "TabularSections": [],
}


def test_unsupported_owner_layout_is_not_an_empty_attribute_set(
    raw_header_form: tuple[Path, FormEntry, dict]
) -> None:
    """#184: owner JSON найден, но его layout отклонён декодером.

    Отдельный сценарий от уже покрытых: #148 — owner JSON найден и успешно
    декодирован; #172 — уровня ObjectName нет и ``object_json_path()``
    возвращает ``None``. Здесь файл лежит именно там, где его находит
    ``object_json_path()``, синтаксически корректен, но содержит
    нормализованные ``Properties`` / ``TabularSections`` без ``header``.

    Контракт #160 (normalized input rejected explicitly): такой payload даёт
    ``ok=False`` с ``DecodeError.HEADER_MISSING`` и не принимается passthrough.
    Тест закрепляет это на уровне ``build_form_context()``: найденный, но
    неподдерживаемый owner JSON не превращается в выдуманный пустой набор
    реквизитов — ни в ``FormContext``, ни в LLM-фрагменте.

    Refs #160, #148, #172.
    """
    root, entry, _ = raw_header_form

    # Путь не собирается руками: файл ищет настоящий публичный resolver.
    owner_json = object_json_path(entry)
    assert owner_json is not None
    assert owner_json.exists()

    # Перезаписывается только содержимое уже найденного файла.
    owner_json.write_text(
        json.dumps(NORMALIZED_OWNER_PAYLOAD, ensure_ascii=False), encoding="utf-8"
    )
    # Кэш (path, mtime_ns, size) из #148 не должен отдать прежний raw-header.
    clear_object_cache()

    # Precondition: отказ именно по layout, а не по отсутствию файла.
    # Без него тест мог бы пройти по неверной причине.
    decode_result = decode_object_attributes(owner_json)
    assert decode_result.ok is False
    assert decode_result.error is DecodeError.HEADER_MISSING
    assert decode_result.data["Properties"] == []
    assert decode_result.data["TabularSections"] == []
    assert decode_result.warnings

    context = build_form_context(entry, root)

    # Пустая структура не подставляется вместо неуспешного декодирования.
    assert context.object_attributes is None

    # Остальной контекст формы продолжает строиться.
    assert context.form_name
    assert context.summary is not None

    # Причина отказа не теряется. Текст предупреждения в тесте не дублируется.
    context_warnings = context.metadata["warnings"]
    assert context_warnings
    for warning in decode_result.warnings:
        assert warning in context_warnings
    # Причина — отклонённый layout, а не ненайденный файл (#148 / #172).
    assert not any(
        "файл объекта метаданных не найден" in warning for warning in context_warnings
    )

    # Fixture даёт хотя бы одну data-связь, иначе цикл был бы вакуумным.
    assert context.resolved_relations
    for relation in context.resolved_relations:
        assert relation["resolved"] is False
        assert relation["value_type"] is None
        assert relation["synonym"] is None

    fragment = to_llm_prompt_fragment(context)

    # LLM-слой показывает отсутствие данных через публичные маркеры,
    # а не выдаёт его за пустой набор реквизитов.
    assert OBJECT_ATTRIBUTES_MARKER in fragment
    assert NO_OBJECT_PLACEHOLDER in fragment
    assert "SyntheticAttribute" not in fragment
    assert "Synthetic attribute" not in fragment
