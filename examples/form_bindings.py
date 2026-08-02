"""Пример: привязка элементов формы к данным (data_path, issue #85).

Пример полностью синтетический и самодостаточный: обе формы собираются
во временном каталоге по структуре реальной выгрузки. Реальные данные,
контейнеры 1С и внутренняя инфраструктура не используются.

Показано, что `parse_elem_json` заполняет `data_path` двумя механизмами:

* обычная форма  — через поле ``prop`` записи ``data``;
* управляемая    — через UUID реквизита из ``Catalog.json``.

Запуск:

    python examples/form_bindings.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent.elem_parser import parse_elem_json

UUID_FORM = "02023637-7868-4a5f-8576-835a76e0c9ba"
UUID_GOROD = "3d446926-2fb8-11d7-85a2-0050bae0a772"
UUID_ADRES = "3d446928-2fb8-11d7-85a2-0050bae0a772"


# ---------------------------------------------------------------------------
# Метаданные объекта: карта UUID -> имя реквизита (нужна управляемым формам)
# ---------------------------------------------------------------------------
def _catalog_attribute(uuid: str, name: str, synonym: str) -> list:
    """Узел реквизита Catalog.json: UUID в node[1][2], имя в node[2]."""
    node = ["0", ["0", "0", uuid], f'"{name}"', ["ru", f'"{synonym}"'], '"(Общ)"']
    return [[["1", [["1", [node]]]]]]


def _catalog_json() -> dict:
    return {
        "header": [
            [
                None, None, None, None, None, None,
                [
                    "cf4abea7-37b2-11d4-940f-008048da11f9",
                    "11",
                    _catalog_attribute(UUID_GOROD, "Город", "Город"),
                    _catalog_attribute(UUID_ADRES, "Адрес", "Адрес"),
                ],
            ]
        ]
    }


# ---------------------------------------------------------------------------
# Обычная форма: привязка через prop
# ---------------------------------------------------------------------------
def _legacy_widget_raw(name: str) -> list:
    """Сокращённый raw элемента обычной формы.

    Тег "14" в raw[4] есть у ВСЕХ элементов — и у полей, и у надписей,
    поэтому сам по себе привязки не означает. Признак привязки — prop.
    """
    return [
        "381ed624-9217-4e63-85db-c4c3cb87daae",
        "4",
        ["9", ['"Pattern"', ['"S"', "100", "1"]], [[["16", "1", ["1", "0"]]]]],
        ["8", "94", "61", "354", "80", "1"],
        ["14", f'"{name}"', "4294967295", "0", "0", "0"],
        ["0"],
    ]


def _legacy_record(rec_id: int, name: str, prop: str | None = None) -> dict:
    record = {
        "id": rec_id,
        "ver": "1",
        "page": "Страница1",
        "raw": _legacy_widget_raw(name),
    }
    if prop is not None:
        record["prop"] = prop
    return record


def make_legacy_form(catalog_root: Path) -> Path:
    """Обычная форма элемента: поля с prop, надписи и панель — без него."""
    form_root = catalog_root / "CatalogForm" / "ФормаЭлемента"
    form_root.mkdir(parents=True, exist_ok=True)

    payload = {
        "params": [],
        "props": [{"name": "СправочникОбъект", "id": "0", "raw": []}],
        "commands": [],
        "tree": [
            {"name": "НадписьГород", "type": "Label"},
            {"name": "Город", "type": "Field"},
            {"name": "Код", "type": "Field"},
            {"name": "ДействияФормы", "type": "CommandPanel"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1": {"ver": "1", "page_format_version": "1", "raw": [], "info": {}},
            # надпись: prop нет -> привязки нет
            "Страница1/НадписьГород": _legacy_record(1, "НадписьГород"),
            # реквизит объекта: prop != имя -> префикс добавляется
            "Страница1/Город": _legacy_record(2, "Город", "СправочникОбъект"),
            # стандартный реквизит: в Catalog.json его нет, но привязка по имени
            "Страница1/Код": _legacy_record(3, "Код", "СправочникОбъект"),
            "Страница1/ДействияФормы": _legacy_record(4, "ДействияФормы"),
        },
    }
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return form_root


# ---------------------------------------------------------------------------
# Управляемая форма: привязка через UUID реквизита
# ---------------------------------------------------------------------------
def _managed_field_raw(name: str, attribute_uuid: str, local_id: str) -> list:
    """Сокращённый raw поля управляемой формы: UUID привязки в raw[11][2][1]."""
    return [
        "37",
        [local_id, UUID_FORM],
        "0", "0", "0", "2",
        f'"{name}"',
        "1", "0",
        ["1", "0"],
        ["1", "0"],
        ["2", ["1"], ["0", attribute_uuid]],
        ["0"],
    ]


def make_managed_form(catalog_root: Path) -> Path:
    """Управляемая форма: поля с UUID реквизита, кнопка и группа — без него."""
    form_root = catalog_root / "CatalogForm" / "ФормаЭлементаУправляемая"
    form_root.mkdir(parents=True, exist_ok=True)

    payload = {
        "params": [],
        "props": [],
        "commands": [],
        "tree": [
            {"name": "Город", "type": "Field"},
            {"name": "Адрес", "type": "Field"},
            {"name": "Команда1", "type": "Button"},
        ],
        "data": {
            "Город": {"raw": _managed_field_raw("Город", UUID_GOROD, "13"), "ver": "1"},
            "Адрес": {"raw": _managed_field_raw("Адрес", UUID_ADRES, "16"), "ver": "1"},
            # кнопка: UUID реквизита нет -> привязки нет
            "Команда1": {
                "raw": ["22", ["25", UUID_FORM], "0", '"Команда1"', ["1", "0"]],
                "ver": "1",
            },
        },
    }
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return form_root


def report(title: str, form_root: Path) -> None:
    result = parse_elem_json(form_root)

    bound = [e for e in result.elements if e.get("data_path")]
    print(f"\n=== {title} ===")
    print(f"elem_index_ok = {result.elem_index_ok}, "
          f"элементов = {len(result.elements)}, с привязкой = {len(bound)}")

    for element in result.elements:
        path = element.get("data_path")
        mark = path if path else "— (привязки нет)"
        print(f"  {element['name']:<28} {element['type']:<14} {mark}")

    if result.warnings:
        print("  warnings:", result.warnings)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog_root = Path(tmp) / "Catalog" / "Банки"
        catalog_root.mkdir(parents=True)
        (catalog_root / "Catalog.json").write_text(
            json.dumps(_catalog_json(), ensure_ascii=False), encoding="utf-8"
        )

        report("Обычная форма (привязка через prop)", make_legacy_form(catalog_root))
        report("Управляемая форма (привязка через UUID)", make_managed_form(catalog_root))

        print(
            "\nОтсутствие data_path у надписей, панелей команд, групп и кнопок —\n"
            "штатный результат, а не пробел декодирования."
        )


if __name__ == "__main__":
    main()
