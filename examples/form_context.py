"""Компактный LLM-контекст формы: FormContext (issue #77, #NEW).

Пример полностью синтетический: реальная выгрузка, контейнер 1С и установленная
платформа не требуются. Файлы создаются во временном каталоге и
удаляются после завершения. Все имена придуманы.

Показано:

1. ``FormEntry`` — карточка указателей, ``FormContext`` — содержимое;
2. форма с BSL и elem, elem-only форма без кода, форма без ``*.elem.json``;
3. ``metadata`` содержит только относительные пути;
4. ``to_llm_prompt_fragment`` по умолчанию не режет контекст, а положительный
``max_chars`` задаёт жёсткий лимит;
5. ``object_attributes``/``resolved_relations`` (issue #NEW) — реквизиты объекта
метаданных за формой и их отсутствие как равноправные исходы.

Запуск:

python examples/form_context.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent import (
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.scan_forms import FormEntry

# ---------------------------------------------------------------------------
# Синтетическая выгрузка
# ---------------------------------------------------------------------------

OBJECT_TYPE = "Catalog"
CONTAINER = "CatalogForm"

BSL_TEXT = (
    "&НаКлиенте\n"
    "Процедура ПриИзменении(Элемент)\n"
    "    Сообщить(\"Значение изменено\");\n"
    "КонецПроцедуры\n"
)

ELEM_PAYLOAD = {
    "tree": [
        {
            "name": "Таблица",
            "type": "Table",
            "ПутьКДанным": "Объект.Строки",
        }
    ],
    "data": {
        "-pages-": ["Страница1"],
        "Страница1/Таблица": {"id": 1},
    },
    "props": [{"name": "Реквизит", "type": "String"}],
}

#: compact-layout raw header, распознаваемый object_decoder._walk_node fallback-путём.
OBJECT_HEADER_PAYLOAD = {
    "header": [
        0,
        [
            0,
            [
                0,
                [0, 0, "11111111-1111-1111-1111-111111111111"],
                '"ОсновнойПоставщик"',
                '"Ref#22222222-2222-2222-2222-222222222222"',
                '"Основной поставщик"',
            ],
        ],
    ]
}

#: UUID ссылочного типа из OBJECT_HEADER_PAYLOAD и его читаемое имя (issue #147).
REFERENCE_TYPE_UUID = "22222222-2222-2222-2222-222222222222"
REFERENCE_TYPES = {REFERENCE_TYPE_UUID: "CatalogRef.Контрагенты"}


def make_form(
    root: Path,
    object_name: str,
    form_name: str,
    *,
    with_bsl: bool,
    with_elem: bool,
) -> FormEntry:
    """Создать каталог формы и вернуть карточку указателей.

    Пути повторяют семантику ``scan_forms`` (issue #57): ``form_path`` и
    ``bsl_path`` абсолютные, ``elem_json_path`` — relative-to-root. ``form_path``
    указывает именно на директорию формы (issue #NEW: тот же путь использует
    ``object_json_path()`` для поиска каталога объекта).
    """
    relative = Path(OBJECT_TYPE) / object_name / CONTAINER / form_name
    form_dir = root / relative
    form_dir.mkdir(parents=True, exist_ok=True)

    if with_bsl:
        (form_dir / f"{CONTAINER}.obj.bsl").write_text(BSL_TEXT, encoding="utf-8")
    if with_elem:
        (form_dir / f"{CONTAINER}.elem.json").write_text(
            json.dumps(ELEM_PAYLOAD, ensure_ascii=False),
            encoding="utf-8",
        )

    return FormEntry(
        object_type=OBJECT_TYPE,
        object_name=object_name,
        container_name=CONTAINER,
        form_name=form_name,
        form_path=form_dir.resolve(),
        bsl_path=(form_dir / f"{CONTAINER}.obj.bsl").resolve(),
        json_path=(form_dir / f"{CONTAINER}.json").resolve(),
        warnings=[] if with_bsl else ["elem-only: no .obj.bsl found"],
        bsl_mtime=0.0,
        bsl_sha256="a" * 64 if with_bsl else None,
        elem_sha256="b" * 64 if with_elem else None,
        elem_json_path=(
            relative / f"{CONTAINER}.elem.json" if with_elem else None
        ),
    )


def write_object_json(root: Path, object_name: str) -> None:
    """Положить ``Catalog.json`` в каталог объекта (issue #NEW).

    ``object_json_path()`` из ``catalog_resolver`` ищет файл либо по имени
    директории объекта, либо по имени типа метаданных — здесь используется
    второй вариант: ``Catalog.json`` в каталоге ``<root>/Catalog/<object_name>/``.
    """
    object_dir = root / OBJECT_TYPE / object_name
    object_dir.mkdir(parents=True, exist_ok=True)
    (object_dir / f"{OBJECT_TYPE}.json").write_text(
        json.dumps(OBJECT_HEADER_PAYLOAD, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------


def demo_materialization(root: Path) -> None:
    """Карточка указателей превращается в содержимое."""
    entry = make_form(root, "Полная", "ФормаЭлемента", with_bsl=True, with_elem=True)
    context = build_form_context(entry, root)

    print("Форма с BSL и elem")
    print("-" * 72)
    print(f"  было в карточке    : пути и хэши, без содержимого")
    print(f"  строк кода          : {len(context.bsl_text.splitlines())}")
    print(f"  реквизитов        : {len(context.summary.attributes)}")
    print(f"  элементов         : {len(context.summary.elements)}")
    print(f"  привязок          : {len(context.summary.relations)}")


def demo_missing_artifacts(root: Path) -> None:
    """Отсутствующие артефакты — штатные ситуации."""
    elem_only = make_form(
        root, "БезКода", "ФормаСписка", with_bsl=False, with_elem=True
    )
    no_elem = make_form(
        root, "БезСтруктуры", "ФормаВыбора", with_bsl=True, with_elem=False
    )

    print("\nОтсутствующие артефакты")
    print("-" * 72)
    for title, entry in (("elem-only, кода нет", elem_only), ("нет elem.json", no_elem)):
        context = build_form_context(entry, root)
        print(
            f"  {title:<22} → bsl_text={context.bsl_text!r:<8.8} "
            f"has_bsl={context.metadata['has_bsl']!s:<5} "
            f"привязок={len(context.summary.relations)} "
            f"warnings={len(context.summary.warnings)}"
        )


def demo_metadata(root: Path) -> None:
    """metadata — отбор полей и только относительные пути."""
    entry = make_form(root, "Мета", "ФормаЭлемента", with_bsl=True, with_elem=True)
    metadata = build_form_context(entry, root).metadata

    print("\nmetadata")
    print("-" * 72)
    for key in sorted(metadata):
        print(f"  {key:<16}: {metadata[key]}")
    print(f"  абсолютных путей    : {str(root) in json.dumps(metadata, ensure_ascii=False)}")


def demo_object_attributes(root: Path) -> None:
    """object_attributes/resolved_relations (issue #NEW): найденный и не найденный объект."""
    with_object = make_form(
        root, "СОбъектом", "ФормаЭлемента", with_bsl=True, with_elem=True
    )
    write_object_json(root, "СОбъектом")

    without_object = make_form(
        root, "БезОбъектаЗагрузки", "ФормаЭлемента", with_bsl=True, with_elem=True
    )

    print("\nobject_attributes / resolved_relations (issue #NEW)")
    print("-" * 72)
    for title, entry in (("Catalog.json есть", with_object), ("Catalog.json нет", without_object)):
        context = build_form_context(entry, root)
        properties = (
            len(context.object_attributes.get("Properties", []))
            if context.object_attributes is not None
            else None
        )
        print(
            f"  {title:<16} → object_attributes={'None' if properties is None else properties} "
            f"resolved_relations={len(context.resolved_relations)} "
            f"warnings={context.metadata['warnings']}"
        )


def demo_truncation(root: Path) -> None:
    """Лимит соблюдается всегда; SUMMARY и OBJECT_ATTRIBUTES раньше BSL."""
    entry = make_form(root, "Большая", "ФормаЭлемента", with_bsl=True, with_elem=True)
    form_dir = root / OBJECT_TYPE / "Большая" / CONTAINER / "ФормаЭлемента"
    (form_dir / f"{CONTAINER}.obj.bsl").write_text(
        "// длинный модуль\n" * 2000, encoding="utf-8"
    )

    context = build_form_context(entry, root)
    full = to_llm_prompt_fragment(context)

    print("\nОбрезка фрагмента")
    print("-" * 72)
    print(f"  полная длина           : {len(full)}")
    print(f"  SUMMARY раньше OBJECT_ATTRIBUTES: {full.index('## SUMMARY') < full.index('## OBJECT_ATTRIBUTES')}")
    print(f"  OBJECT_ATTRIBUTES раньше BSL: {full.index('## OBJECT_ATTRIBUTES') < full.index('## BSL')}")
    unlimited = to_llm_prompt_fragment(context, max_chars=-1)
    print(f"  max_chars=-1 → без обрезки: {unlimited == full}")
    for limit in (0, 3, 200, 8000):
        fragment = to_llm_prompt_fragment(context, max_chars=limit)
        print(f"  max_chars={limit:<7} → длина {len(fragment):<5} в лимите: {len(fragment) <= limit}")

    first = to_llm_prompt_fragment(context, max_chars=4000)
    second = to_llm_prompt_fragment(build_form_context(entry, root), max_chars=4000)
    print(f"  детерминизм              : {first == second}")

def demo_type_resolver(root: Path) -> None:
    """type_resolver (issue #147): Ref#uuid превращается в имя типа.

    Индекс подменён словарём, чтобы пример оставался запускаемым без реальной
    выгрузки: контракт тот же, что у FormScanIndex.resolve_reference_type —
    ``uuid -> имя типа`` либо ``None``.
    """
    entry = make_form(root, "СРезолвером", "ФормаЭлемента", with_bsl=True, with_elem=True)
    write_object_json(root, "СРезолвером")

    without = build_form_context(entry, root)
    with_resolver = build_form_context(entry, root, type_resolver=REFERENCE_TYPES.get)
    unknown = build_form_context(entry, root, type_resolver=lambda uuid: None)

    print("\nСсылочные типы (issue #147)")
    print("-" * 72)
    for title, context in (
        ("без резолвера", without),
        ("с резолвером", with_resolver),
        ("резолвер вернул None", unknown),
    ):
        types = [prop.get("Type") for prop in (context.object_attributes or {}).get("Properties", [])]
        print(f"  {title:<22} → {types}")
    print("  индекс не мутирован    :", REFERENCE_TYPES[REFERENCE_TYPE_UUID])

def main() -> None:
    with tempfile.TemporaryDirectory(prefix="form_context_example_") as tmp:
        root = Path(tmp)
        demo_materialization(root)
        demo_missing_artifacts(root)
        demo_metadata(root)
        demo_object_attributes(root)
        demo_type_resolver(root)
        demo_truncation(root)

        print(
            "\nИтог: контекст открывает то, на что реестр только указывал,"
            "\nи никогда не заменяет отсутствующий файл догадкой (включая реквизиты объекта)."
        )


if __name__ == "__main__":
    main()
