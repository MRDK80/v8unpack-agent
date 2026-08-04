"""Пример: сегментные цепочки ``data_path`` управляемых форм (issue #89).

Пример полностью синтетический и самодостаточный: форма собирается во
временном каталоге по структуре реальной выгрузки. Реальные данные
и контейнеры 1С не используются.

Часть управляемых форм адресует не реквизит объекта, а вложенное поле
реквизита формы. Блок привязки лежит в ``raw[11]`` и имеет вид::

    [<счётчик>, <сегмент>, ["0", <uuid типа>], <сегмент>, ...]

Сегмент ``[<id>]`` разрешается по дереву реквизитов формы (``props``),
сегмент ``[<id>, <uuid таблицы>]`` — по таблицам определений из большого
JSON формы (``$.form[0][0][3]``).

Привязка принимается только при двух условиях одновременно: UUID таблицы
объявлен в определениях формы и склейка имён сегментов посимвольно равна
имени элемента.

Запуск:

    python examples/chain_form_bindings.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent.chain_data_path import (
    build_form_attribute_ids,
    build_form_segment_tables,
    decode_chain_data_path,
)
from v8unpack_agent.elem_parser import parse_elem_json

TYPE_UUID = "11111111-1111-4111-8111-111111111111"
TABLE_UUID = "22222222-2222-4222-8222-222222222222"
ALIEN_UUID = "33333333-3333-4333-8333-333333333333"


# ---------------------------------------------------------------------------
# Таблицы определений: большой JSON формы
# ---------------------------------------------------------------------------
def _field(field_id: str, name: str) -> list:
    """Поле таблицы: id в позиции 1, имя в позиции 3."""
    return [
        "5",
        field_id,
        "0",
        f'"{name}"',
        ["1", "1", ['"ru"', f'"{name}"']],
        ['"Pattern"', ['"S"', "10", "1"]],
        ["0", "0"],
        "0",
    ]


def _definition(address: list[list], fields: list[list]) -> list:
    """Узел таблицы: ["0", <адрес>, <кол-во полей>, <поле>, ...]."""
    return ["0", [str(len(address))] + address, str(len(fields)), *fields]


def _form_json() -> dict:
    section = [
        "4",
        "Дочерние элементы отдельно",
        "21",
        _definition(
            [["1"], ["2"]],
            [_field("1", "ГруппаА"), _field("2", "ГруппаБ")],
        ),
        _definition(
            [["1"], ["2"], ["2", TABLE_UUID]],
            [_field("1", "ПолеОдин"), _field("5", "ПолеПять")],
        ),
    ]
    return {"form": [[["0", "0", "0", section]]]}


# ---------------------------------------------------------------------------
# Реквизиты формы и записи элементов
# ---------------------------------------------------------------------------
def _props() -> list:
    return [
        {
            "name": "СтруктураДанных",
            "id": "1",
            "raw": [],
            "child": [
                {"name": "Узел", "id": "2", "raw": []},
                {"name": "_Прочее", "id": "3", "raw": []},
            ],
        },
    ]


def _chain_full() -> list:
    """props → props → таблица → таблица."""
    return [
        "7",
        ["1"],
        ["0", TYPE_UUID],
        ["2"],
        ["0", TYPE_UUID],
        ["2", TABLE_UUID],
        ["0", TYPE_UUID],
        ["5", TABLE_UUID],
    ]


def _chain_short() -> list:
    """Целиком по дереву реквизитов формы."""
    return ["3", ["1"], ["0", TYPE_UUID], ["3"]]


def _chain_alien_table() -> list:
    """UUID таблицы не объявлен в определениях формы."""
    return [
        "5",
        ["1"],
        ["0", TYPE_UUID],
        ["2"],
        ["0", TYPE_UUID],
        ["5", ALIEN_UUID],
    ]


def _widget_raw(name: str, block: object | None) -> list:
    """Запись элемента: блок привязки в позиции 11."""
    return [
        "34",
        ["100", TYPE_UUID],
        "0",
        "0",
        "0",
        "2",
        f'"{name}"',
        "1",
        "0",
        ["1", "0"],
        ["1", "0"],
        block if block is not None else ["1", "0"],
        ["0"],
    ]


def make_form(root: Path) -> Path:
    """Форма с четырьмя элементами: два с привязкой, два без неё."""
    form_root = root / "Catalog" / "Объект1" / "CatalogForm" / "Форма1"
    form_root.mkdir(parents=True)

    payload = {
        "params": [],
        "props": _props(),
        "commands": [],
        "tree": [
            {"name": "СтруктураДанныхУзелГруппаБПолеПять", "type": "Field"},
            {"name": "СтруктураДанных_Прочее", "type": "Field"},
            {"name": "ПолеСЧужойТаблицей", "type": "Field"},
            {"name": "НадписьБезПривязки", "type": "Label"},
        ],
        "data": {
            # полная цепочка: склейка совпадает с именем → привязка есть
            "Группа1/СтруктураДанныхУзелГруппаБПолеПять": {
                "ver": "1",
                "raw": _widget_raw(
                    "СтруктураДанныхУзелГруппаБПолеПять", _chain_full()
                ),
            },
            # короткая цепочка по реквизитам формы
            "Группа1/СтруктураДанных_Прочее": {
                "ver": "1",
                "raw": _widget_raw("СтруктураДанных_Прочее", _chain_short()),
            },
            # таблица сегмента не объявлена → привязки нет
            "Группа1/ПолеСЧужойТаблицей": {
                "ver": "1",
                "raw": _widget_raw("ПолеСЧужойТаблицей", _chain_alien_table()),
            },
            # блока привязки нет вовсе — штатный случай
            "Группа1/НадписьБезПривязки": {
                "ver": "1",
                "raw": _widget_raw("НадписьБезПривязки", None),
            },
        },
    }

    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (form_root / "CatalogForm.json").write_text(
        json.dumps(_form_json(), ensure_ascii=False), encoding="utf-8"
    )
    return form_root


def report(form_root: Path) -> None:
    result = parse_elem_json(form_root)
    bound = [item for item in result.elements if item.get("data_path")]

    print("=== Разбор формы с цепочками ===")
    print(f"elem_index_ok = {result.elem_index_ok}, "
          f"элементов = {len(result.elements)}, с привязкой = {len(bound)}")
    for element in result.elements:
        path = element.get("data_path") or "— (привязки нет)"
        print(f"  {element['name']:<40} {element['type']:<8} {path}")


def refusals() -> None:
    """Почему декодер отказывается создавать путь."""
    tables = build_form_segment_tables(_form_json())
    attribute_ids = build_form_attribute_ids(_props())

    print("\n=== Проверки против ложных привязок ===")

    path, _ = decode_chain_data_path(
        _chain_full(), tables, attribute_ids, "СтруктураДанныхУзелГруппаБПолеПять"
    )
    print(f"  склейка совпала с именем   → {path}")

    path, warnings = decode_chain_data_path(
        _chain_full(), tables, attribute_ids, "СовершенноДругоеИмя"
    )
    print(f"  склейка не совпала          → {path}")
    for warning in warnings:
        print(f"      {warning}")

    path, warnings = decode_chain_data_path(
        _chain_alien_table(), tables, attribute_ids, "ПолеСЧужойТаблицей"
    )
    print(f"  таблица не объявлена        → {path}")
    for warning in warnings:
        print(f"      {warning}")

    path, _ = decode_chain_data_path(
        ["2", ["1"], ["0", TYPE_UUID]], tables, attribute_ids, "ЛюбоеИмя"
    )
    print(f"  цепочка короче двух звеньев → {path}  (старый формат)")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report(make_form(Path(tmp)))
    refusals()
    print(
        "\nОтсутствие data_path у надписей и у элементов с неподтверждённой\n"
        "цепочкой — штатный результат: ложная привязка хуже отсутствующей."
    )


if __name__ == "__main__":
    main()
