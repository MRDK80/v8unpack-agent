"""Причины нулевой привязки data_path: ZeroBindingReason (issue #116).

Пример полностью синтетический: реальная выгрузка, контейнер 1С и файловая
система не требуются. Все UUID и имена придуманы.

Показано:

1. подтверждённая цепочка не получает причины (``None``);
2. каждая наблюдаемая структурная ситуация даёт СВОЮ причину;
3. агрегат уровня формы, включая ``MIXED``;
4. ни одна причина не порождает выдуманный ``data_path``.

Запуск:

    python examples/zero_binding_reasons.py
"""
from __future__ import annotations

from v8unpack_agent.chain_data_path import (
    BIND_SLOT,
    ZeroBindingReason,
    aggregate_form_zero_binding,
    build_form_attribute_ids,
    build_form_segment_tables,
    classify_element_zero_binding,
    classify_raw_zero_binding,
    decode_chain_data_path,
)

# ---------------------------------------------------------------------------
# Синтетическая форма: один реквизит с вложенным полем
# ---------------------------------------------------------------------------

# UUID таблицы определений, объявленной в этой форме.
UUID_TABLE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
# UUID, которого в определениях формы нет.
UUID_ALIEN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
# UUID из служебной записи типа ["0", <uuid>]: в пути не участвует.
UUID_TYPE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

SEG_ROOT = ["3", UUID_TABLE]      # корневой реквизит формы, id=3
SEG_FIELD = ["7"]                 # вложенное поле таблицы определений, id=7
SEG_FIELD_ALIEN = ["7", UUID_ALIEN]
SEG_FIELD_ORPHAN = ["9"]          # id, не объявленный нигде

PROPS_SECTION = [{"name": "Реквизит", "id": "3"}]
ELEMENT_NAME = "РеквизитПоле"     # платформа именует элемент склейкой пути


def form_json() -> dict:
    """Большой JSON формы: таблицы определений лежат в ``$.form[0][0][3]``."""
    field = ["5", "7", "0", '"Поле"']
    table = ["0", ["1", SEG_ROOT], "1", field]
    return {"form": [[["0", "0", "0", [table]]]]}


def block(*items: object) -> list:
    """Корректный блок: счётчик равен числу записей после него."""
    return [str(len(items)), *items]


def raw_record(bind_block: object) -> list:
    """Запись элемента, где блок привязки лежит в слоте BIND_SLOT."""
    return ["0"] * BIND_SLOT + [bind_block]


BOUND_BLOCK = block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD)


# ---------------------------------------------------------------------------
# Сценарии
# ---------------------------------------------------------------------------

def build_context() -> tuple[dict, dict]:
    tables = build_form_segment_tables(form_json())
    attribute_ids = build_form_attribute_ids(PROPS_SECTION)
    return tables, attribute_ids


def demo_bound(tables: dict, attribute_ids: dict) -> None:
    """Подтверждённая привязка: причины нет."""
    path, warnings = decode_chain_data_path(
        BOUND_BLOCK, tables, attribute_ids, ELEMENT_NAME
    )
    reason = classify_element_zero_binding(
        BOUND_BLOCK, tables, attribute_ids, ELEMENT_NAME
    )
    print("Подтверждённая цепочка")
    print("-" * 72)
    print(f"  data_path : {path}")
    print(f"  warnings  : {warnings}")
    print(f"  причина   : {reason}")


def demo_reasons(tables: dict, attribute_ids: dict) -> list:
    """По одному примеру на каждую наблюдаемую структурную ситуацию."""
    cases = [
        ("слота привязки нет вовсе", raw_record, ["0"] * 4, ELEMENT_NAME),
        ("маркер непривязанного элемента", None, ["1", "0"], ELEMENT_NAME),
        ("в слоте скаляр, а не цепочка", None, "0", ELEMENT_NAME),
        ("счётчик не совпал с составом", None,
         ["5", SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD], ELEMENT_NAME),
        ("один сегмент — это не цепочка", None, block(SEG_ROOT), ELEMENT_NAME),
        ("таблица сегмента не объявлена", None,
         block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ALIEN), ELEMENT_NAME),
        ("сегмент не найден нигде", None,
         block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ORPHAN), ELEMENT_NAME),
        ("склейка не равна имени элемента", None,
         BOUND_BLOCK, "СовершенноДругоеИмя"),
    ]

    print("\nПричины по элементам")
    print("-" * 72)
    reasons = []
    for title, wrapper, payload, name in cases:
        if wrapper is raw_record:
            reason = classify_raw_zero_binding(payload, tables, attribute_ids, name)
        else:
            reason = classify_element_zero_binding(
                payload, tables, attribute_ids, name
            )
        reasons.append(reason)
        print(f"  {title:<34} → {reason.value}")
    return reasons


def demo_aggregate(reasons: list) -> None:
    """Причина уровня формы."""
    print("\nАгрегат уровня формы")
    print("-" * 72)
    single = [ZeroBindingReason.NO_BIND_SLOT] * 3
    print(f"  все элементы с одной причиной → {aggregate_form_zero_binding(single).value}")
    print(f"  разные причины                → {aggregate_form_zero_binding(reasons).value}")
    print(f"  непривязанных элементов нет   → {aggregate_form_zero_binding([])}")


def demo_no_invented_binding(tables: dict, attribute_ids: dict) -> None:
    """Ни одна причина не порождает data_path."""
    negatives = [
        "0",
        ["1", "0"],
        ["5", SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD],
        block(SEG_ROOT),
        block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ALIEN),
        block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ORPHAN),
    ]
    paths = [
        decode_chain_data_path(item, tables, attribute_ids, ELEMENT_NAME)[0]
        for item in negatives
    ]
    print("\nПроверка: выдуманных привязок нет")
    print("-" * 72)
    print(f"  блоков проверено : {len(paths)}")
    print(f"  получено путей   : {sum(1 for path in paths if path)}")


def main() -> None:
    tables, attribute_ids = build_context()
    demo_bound(tables, attribute_ids)
    reasons = demo_reasons(tables, attribute_ids)
    demo_aggregate(reasons)
    demo_no_invented_binding(tables, attribute_ids)

    print(
        "\nИтог: причина объясняет, почему привязки нет, и никогда не заменяет"
        "\nотсутствующий data_path догадкой."
    )


if __name__ == "__main__":
    main()
