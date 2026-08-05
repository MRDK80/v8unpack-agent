"""Резолюция ссылочных типов реквизитов: Ref#uuid → имя объекта метаданных (issue #88).

Пример полностью синтетический: реальный контейнер 1С и production-выгрузка
не требуются. Демонстрируются три состояния одного и того же объекта:

1. Без резолвера — ссылочный тип остаётся достоверным `Ref#<uuid>`
   (прежнее поведение, обратная совместимость).
2. С резолвером из `scan_forms` — тип становится читаемым именем
   (`CatalogRef.<Имя>`, `EnumRef.<Имя>` и др.).
3. Неизвестный UUID — остаётся `Ref#<uuid>`: тип не угадывается.

Дополнительно показано, что индекс собирается тем же обходом выгрузки
(второго discovery нет), что примитивные типы резолверу не передаются
и что сбой резолвера не роняет декодирование.

Запуск:

    python examples/reference_types.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent.object_decoder import decode_object_attributes
from v8unpack_agent.scan_forms import scan_forms

# UUID синтетические: ни один не взят из реальной конфигурации.
NULL_UUID = "00000000-0000-0000-0000-000000000000"

# Идентификаторы объектов-целей ссылки. У объекта метаданных их несколько,
# и ссылка реквизита адресует один из «типовых» слотов блока header[0][1].
CITY_TYPE_UUID = "11111111-1111-4111-8111-111111111111"
CITY_OBJECT_UUID = "1111aaaa-1111-4111-8111-111111111111"
ORDER_TYPE_UUID = "22222222-2222-4222-8222-222222222222"
STATUS_TYPE_UUID = "33333333-3333-4333-8333-333333333333"

# UUID, которого нет ни у одного объекта выгрузки: безопасный fallback.
UNKNOWN_TYPE_UUID = "99999999-9999-4999-8999-999999999999"


# ---------------------------------------------------------------------------
# Синтетический raw-header объекта: реквизиты и их типы
# ---------------------------------------------------------------------------

def _name_entry(uuid: str, name: str) -> list:
    """name-entry production-layout: UUID, имя, синоним."""
    return [
        "2",
        ["1", "100", uuid],
        json.dumps(name, ensure_ascii=False),
        ["1", '"ru"', json.dumps(name, ensure_ascii=False)],
        '""', "0", "0", NULL_UUID,
    ]


def _attribute(attribute_uuid: str, name: str, type_node: list) -> list:
    """Реквизит объекта: name-entry + описание типа в соседнем descriptor."""
    descriptor = ["2", _name_entry(attribute_uuid, name), ['"Pattern"', type_node]]
    return [
        [
            "8",
            [
                "27", descriptor, "0", ["0"], ["0"], "0", '""', "0",
                ['"U"'], ['"U"'], "0", NULL_UUID, "2", "0",
                ["5004", "0"], ["3", "0", "0"], ["0", "0"],
                "0", ["0"], ['"U"'], "0", "0", "0",
            ],
            "0", "1", "1",
        ],
        "0",
    ]


def _reference(type_uuid: str) -> list:
    """Ссылочный тип: код '#' и UUID цели."""
    return ['"#"', type_uuid]


def _demo_object_json() -> dict:
    """Объект с примитивным реквизитом и тремя ссылочными."""
    attributes = [
        _attribute(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000001", "Наименование", ['"S"']
        ),
        _attribute(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000002", "Город", _reference(CITY_TYPE_UUID)
        ),
        _attribute(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000003", "Заказ", _reference(ORDER_TYPE_UUID)
        ),
        _attribute(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000004", "Статус", _reference(STATUS_TYPE_UUID)
        ),
        _attribute(
            "aaaaaaaa-aaaa-4aaa-8aaa-000000000005",
            "НеизвестнаяСсылка",
            _reference(UNKNOWN_TYPE_UUID),
        ),
    ]
    root = [
        "1", [], "0", ["sections", "0"], "0",
        ["properties", str(len(attributes)), *attributes],
    ]
    return {"header": [root]}


# ---------------------------------------------------------------------------
# Синтетическая выгрузка: объекты-цели ссылок
# ---------------------------------------------------------------------------

def _identity_json(identity: list) -> dict:
    """Метаданные объекта: блок идентификации header[0][1]."""
    return {"header": [["metadata", identity]]}


def build_demo_export(root: Path) -> Path:
    """Собрать синтетическую выгрузку и вернуть путь к объекту с реквизитами."""
    # Слот 1 блока идентификации несёт UUID, на который ссылается реквизит.
    (root / "Catalog" / "Города").mkdir(parents=True)
    (root / "Catalog" / "Города" / "Catalog.json").write_text(
        json.dumps(_identity_json(["identity", CITY_TYPE_UUID, CITY_OBJECT_UUID, "tail"])),
        encoding="utf-8",
    )

    # Слот 3 — тоже валидный вариант: у объекта несколько идентификаторов.
    (root / "Document" / "Заказ").mkdir(parents=True)
    (root / "Document" / "Заказ" / "Document.json").write_text(
        json.dumps(_identity_json(["identity", "x", "object", ORDER_TYPE_UUID])),
        encoding="utf-8",
    )

    # Перечисление: собственный префикс имени типа.
    (root / "Enum" / "СтатусыЗаказа").mkdir(parents=True)
    (root / "Enum" / "СтатусыЗаказа" / "Enum.json").write_text(
        json.dumps(_identity_json(["identity", STATUS_TYPE_UUID, "object", "tail"])),
        encoding="utf-8",
    )

    # Регистр: ссылочного типа нет, в индекс не попадает.
    (root / "InformationRegister" / "ЦеныНоменклатуры").mkdir(parents=True)
    (root / "InformationRegister" / "ЦеныНоменклатуры" / "InformationRegister.json").write_text(
        json.dumps(_identity_json(["identity", "44444444-4444-4444-8444-444444444444", "object"])),
        encoding="utf-8",
    )

    # Объект, реквизиты которого декодируются в примере.
    object_json = root / "Catalog" / "Контрагенты" / "Catalog.json"
    object_json.parent.mkdir(parents=True)
    object_json.write_text(
        json.dumps(_demo_object_json(), ensure_ascii=False), encoding="utf-8"
    )
    return object_json


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

def _print_types(title: str, result) -> None:
    print(f"\n{title}")
    print("-" * 64)
    for prop in result.data["Properties"]:
        print(f"  {prop['Name']:<20} {prop['Type']}")
    if result.warnings:
        print("  warnings:")
        for warning in result.warnings:
            print(f"    - {warning}")


def demo_index(export_root: Path) -> None:
    """Индекс собирается тем же обходом, что и опись форм."""
    index = scan_forms(export_root)

    print("Индекс ссылочных типов (issue #88)")
    print("-" * 64)
    print(f"  форм в индексе      : {index.total}")
    print(f"  ссылочных типов     : {len(index.reference_types)}")
    for uuid, type_name in sorted(index.reference_types.items(), key=lambda item: item[1]):
        print(f"    {uuid}  →  {type_name}")

    print(f"  неизвестный UUID    : {index.resolve_reference_type(UNKNOWN_TYPE_UUID)}")
    if index.scan_warnings:
        print("  предупреждения обхода:")
        for warning in index.scan_warnings:
            print(f"    - {warning}")


def demo_resolution(export_root: Path, object_json: Path) -> None:
    """Три состояния одного объекта: без резолвера, с резолвером, при сбое."""
    index = scan_forms(export_root)

    # 1. Прежнее поведение: параметр не передан.
    _print_types("Без резолвера (обратная совместимость)", decode_object_attributes(object_json))

    # 2. Резолвер из индекса выгрузки.
    _print_types(
        "С резолвером из scan_forms",
        decode_object_attributes(object_json, type_resolver=index.resolve_reference_type),
    )

    # 3. Резолвер, который всегда отказывается отвечать.
    _print_types(
        "Резолвер вернул None для всех UUID",
        decode_object_attributes(object_json, type_resolver=lambda uuid: None),
    )

    # 4. Сбой внутри резолвера не роняет декодирование.
    def failing_resolver(uuid: str) -> str | None:
        raise RuntimeError("внешний источник недоступен")

    _print_types(
        "Резолвер выбросил исключение (тип сохранён, сбой в диагностике)",
        decode_object_attributes(object_json, type_resolver=failing_resolver),
    )


def demo_primitive_is_not_resolved(object_json: Path) -> None:
    """Примитивные типы резолверу не передаются."""
    seen: list[str] = []

    def tracking_resolver(uuid: str) -> str | None:
        seen.append(uuid)
        return None

    decode_object_attributes(object_json, type_resolver=tracking_resolver)

    print("\nЧто получил резолвер")
    print("-" * 64)
    print(f"  вызовов резолвера   : {len(seen)}")
    print("  UUID приходят без префикса Ref#:")
    for uuid in seen:
        print(f"    {uuid}")
    print("  примитивный реквизит 'Наименование' резолверу не передавался")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        export_root = Path(tmp) / "cf_export"
        object_json = build_demo_export(export_root)

        demo_index(export_root)
        demo_resolution(export_root, object_json)
        demo_primitive_is_not_resolved(object_json)

    print(
        "\nИтог: имя ссылочного типа появляется только при переданном резолвере;"
        "\nнеизвестный UUID остаётся Ref#<uuid> — тип не угадывается."
    )


if __name__ == "__main__":
    main()
