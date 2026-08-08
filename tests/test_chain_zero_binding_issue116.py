"""Машиночитаемые причины нулевой привязки data_path (issue #116).

Все фикстуры синтетические. Реальная выгрузка, реальные UUID, реальные имена
объектов и абсолютные пути здесь не используются и не требуются.

Разбор структуры повторяет контракт блока привязки ``raw[BIND_SLOT]``,
уже зафиксированный в ``chain_data_path`` (issue #89):

    [<счётчик>, <сегмент>, ["0", <uuid типа>], <сегмент>, ...]

Проверяемое свойство #116: каждая наблюдаемая структурная ситуация получает
СВОЮ причину, а ни одна из них не порождает выдуманный ``data_path``.

Категория ``BIND_SLOT_NOT_A_CHAIN`` добавлена по результатам прогона на
реальных данных: все элементы, ранее получавшие ``CHAIN_MALFORMED``,
имели в слоте скаляр вместо списка — это другой layout, а не поломка
структуры. ``CHAIN_MALFORMED`` остаётся за расхождением счётчика.
"""
from __future__ import annotations

import json

import pytest

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
# Синтетические идентификаторы
# ---------------------------------------------------------------------------

# UUID таблицы определений, объявленной в синтетической форме.
UUID_TABLE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
# UUID, не объявленный нигде в определениях формы.
UUID_ALIEN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
# UUID из служебной записи типа ["0", <uuid>] — в пути не участвует.
UUID_TYPE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

# Корневой сегмент: id=3, несёт UUID таблицы вложенных полей.
SEG_ROOT = ["3", UUID_TABLE]
# Вложенный сегмент: id=7, имя берётся из таблицы определений.
SEG_FIELD = ["7"]
# Вложенный сегмент с чужим UUID таблицы.
SEG_FIELD_ALIEN = ["7", UUID_ALIEN]
# Вложенный сегмент, id которого не объявлен нигде.
SEG_FIELD_ORPHAN = ["9"]

# Дерево реквизитов формы: корневой реквизит с id=3.
PROPS_SECTION = [{"name": "Реквизит", "id": "3"}]

# Ожидаемое имя элемента: платформа именует элемент склейкой имён сегментов.
ELEMENT_NAME = "РеквизитПоле"
EXPECTED_PATH = "Реквизит.Поле"


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

def _form_json() -> dict:
    """Большой JSON формы с одной таблицей определений.

    Секция таблиц лежит по адресу ``$.form[0][0][3]``.
    """
    field = ["5", "7", "0", '"Поле"']
    table = ["0", ["1", SEG_ROOT], "1", field]
    return {"form": [[["0", "0", "0", [table]]]]}


@pytest.fixture()
def segment_tables() -> dict:
    tables = build_form_segment_tables(_form_json())
    assert tables, "фикстура сломана: таблицы определений не собрались"
    assert tables[(json.dumps(SEG_ROOT, ensure_ascii=False),)] == {7: "Поле"}
    return tables


@pytest.fixture()
def attribute_ids() -> dict:
    ids = build_form_attribute_ids(PROPS_SECTION)
    assert ids == {(None, 3): "Реквизит"}
    return ids


def _block(*items: object) -> list:
    """Корректный блок привязки: счётчик равен числу элементов после него."""
    return [str(len(items)), *items]


def _raw(block: object) -> list:
    """Запись элемента, где блок привязки лежит в слоте BIND_SLOT."""
    return ["0"] * BIND_SLOT + [block]


BOUND_BLOCK = _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD)


# ---------------------------------------------------------------------------
# Контракт enum: причины машиночитаемы и стабильны
# ---------------------------------------------------------------------------

EXPECTED_REASONS = {
    "NO_BIND_SLOT": "no_bind_slot",
    "BIND_SLOT_UNBOUND_MARKER": "bind_slot_unbound_marker",
    "BIND_SLOT_NOT_A_CHAIN": "bind_slot_not_a_chain",
    "CHAIN_MALFORMED": "chain_malformed",
    "CHAIN_TOO_SHORT": "chain_too_short",
    "CHAIN_TABLE_NOT_DECLARED": "chain_table_not_declared",
    "CHAIN_SEGMENT_UNRESOLVED": "chain_segment_unresolved",
    "CHAIN_NAME_MISMATCH": "chain_name_mismatch",
    "MIXED": "mixed",
}


@pytest.mark.parametrize(("member", "value"), sorted(EXPECTED_REASONS.items()))
def test_reason_member_has_stable_value(member: str, value: str) -> None:
    """Каждая причина — стабильный snake_case-литерал, пригодный для отчётов."""
    assert getattr(ZeroBindingReason, member).value == value


def test_reason_members_are_exactly_expected() -> None:
    """Состав enum зафиксирован: лишних и пропущенных причин нет."""
    assert {reason.name for reason in ZeroBindingReason} == set(EXPECTED_REASONS)


def test_reason_values_are_unique() -> None:
    values = [reason.value for reason in ZeroBindingReason]
    assert len(values) == len(set(values))


def test_no_generic_status_member() -> None:
    """DoD #116: общий статус вместо конкретной причины недопустим."""
    forbidden = {"unknown", "other", "zero_binding", "none"}
    assert not {reason.value for reason in ZeroBindingReason} & forbidden


# ---------------------------------------------------------------------------
# Категория: блока привязки нет вовсе
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("length", [0, 1, BIND_SLOT - 1, BIND_SLOT])
def test_no_bind_slot(length: int, segment_tables: dict, attribute_ids: dict) -> None:
    """len(raw) <= BIND_SLOT: слота привязки в записи элемента нет."""
    raw = ["0"] * length
    reason = classify_raw_zero_binding(
        raw, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.NO_BIND_SLOT


def test_raw_not_a_list_is_no_bind_slot(
    segment_tables: dict, attribute_ids: dict
) -> None:
    for raw in (None, "0", {}, 11):
        assert (
            classify_raw_zero_binding(
                raw, segment_tables, attribute_ids, ELEMENT_NAME
            )
            is ZeroBindingReason.NO_BIND_SLOT
        )


# ---------------------------------------------------------------------------
# Категория: слот содержит скаляр, а не цепочку
# ---------------------------------------------------------------------------

SCALAR_BLOCKS = ["0", "1", "", "строка", 0, 1, 3.5, True, None, {}, {"a": 1}, ()]


@pytest.mark.parametrize("block", SCALAR_BLOCKS)
def test_bind_slot_not_a_chain(
    block: object, segment_tables: dict, attribute_ids: dict
) -> None:
    """Скаляр в слоте — другой layout, а не повреждённая цепочка."""
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.BIND_SLOT_NOT_A_CHAIN


def test_scalar_slot_through_raw(segment_tables: dict, attribute_ids: dict) -> None:
    reason = classify_raw_zero_binding(
        _raw("0"), segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.BIND_SLOT_NOT_A_CHAIN


@pytest.mark.parametrize("block", SCALAR_BLOCKS)
def test_scalar_slot_is_not_malformed(
    block: object, segment_tables: dict, attribute_ids: dict
) -> None:
    """CHAIN_MALFORMED зарезервирован за расхождением счётчика."""
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is not ZeroBindingReason.CHAIN_MALFORMED


# ---------------------------------------------------------------------------
# Категория: маркер заведомо непривязанного элемента ["1", "0"]
# ---------------------------------------------------------------------------

def test_unbound_marker_block(segment_tables: dict, attribute_ids: dict) -> None:
    """["1", "0"] — то же значение, что у непривязанных элементов рабочих форм."""
    reason = classify_element_zero_binding(
        ["1", "0"], segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.BIND_SLOT_UNBOUND_MARKER


def test_unbound_marker_through_raw(
    segment_tables: dict, attribute_ids: dict
) -> None:
    reason = classify_raw_zero_binding(
        _raw(["1", "0"]), segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.BIND_SLOT_UNBOUND_MARKER


def test_unbound_marker_is_not_malformed(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Маркер не должен маскироваться под повреждённую структуру."""
    reason = classify_element_zero_binding(
        ["1", "0"], segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is not ZeroBindingReason.CHAIN_MALFORMED


# ---------------------------------------------------------------------------
# Категория: счётчик не совпал с числом элементов
# ---------------------------------------------------------------------------

def test_chain_malformed_counter_mismatch(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = ["5", SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD]
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_MALFORMED


def test_chain_malformed_non_digit_counter(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = ["x", SEG_ROOT, SEG_FIELD]
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_MALFORMED


def test_chain_malformed_scalar_item_inside_list(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Блок — список, но среди записей скаляр: структура не согласована."""
    block = ["2", SEG_ROOT, "0"]
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_MALFORMED


# ---------------------------------------------------------------------------
# Категория: цепочка короче двух сегментов
# ---------------------------------------------------------------------------

def test_chain_too_short(segment_tables: dict, attribute_ids: dict) -> None:
    """Один сегмент — старый формат ссылки на реквизит объекта, не цепочка."""
    reason = classify_element_zero_binding(
        _block(SEG_ROOT), segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_TOO_SHORT


def test_type_separator_alone_is_too_short(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Служебная запись типа сегментом не является."""
    reason = classify_element_zero_binding(
        _block(["0", UUID_TYPE]), segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_TOO_SHORT


# ---------------------------------------------------------------------------
# Категория: таблица сегмента не объявлена в определениях формы
# ---------------------------------------------------------------------------

def test_chain_table_not_declared(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ALIEN)
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_TABLE_NOT_DECLARED


def test_table_not_declared_wins_over_name_mismatch(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Первый структурный отказ важнее последующей проверки имени."""
    block = _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ALIEN)
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, "СовершенноДругоеИмя"
    )
    assert reason is ZeroBindingReason.CHAIN_TABLE_NOT_DECLARED


# ---------------------------------------------------------------------------
# Категория: сегмент не найден ни в таблицах, ни среди реквизитов формы
# ---------------------------------------------------------------------------

def test_chain_segment_unresolved(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ORPHAN)
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_SEGMENT_UNRESOLVED


def test_root_segment_unresolved_when_props_empty(segment_tables: dict) -> None:
    """Пустое дерево реквизитов формы не даёт угадывать корневой сегмент."""
    reason = classify_element_zero_binding(
        BOUND_BLOCK, segment_tables, {}, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_SEGMENT_UNRESOLVED


def test_non_digit_segment_id_is_unresolved(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = _block(SEG_ROOT, ["0", UUID_TYPE], ["нет-id"])
    reason = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is ZeroBindingReason.CHAIN_SEGMENT_UNRESOLVED


# ---------------------------------------------------------------------------
# Категория: склейка сегментов не совпала с именем элемента
# ---------------------------------------------------------------------------

def test_chain_name_mismatch(segment_tables: dict, attribute_ids: dict) -> None:
    reason = classify_element_zero_binding(
        BOUND_BLOCK, segment_tables, attribute_ids, "СовершенноДругоеИмя"
    )
    assert reason is ZeroBindingReason.CHAIN_NAME_MISMATCH


# ---------------------------------------------------------------------------
# Привязка найдена: причины нет
# ---------------------------------------------------------------------------

def test_bound_chain_has_no_reason(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Успешная привязка не должна получать диагностическую причину."""
    reason = classify_element_zero_binding(
        BOUND_BLOCK, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is None


def test_bound_chain_has_no_reason_through_raw(
    segment_tables: dict, attribute_ids: dict
) -> None:
    reason = classify_raw_zero_binding(
        _raw(BOUND_BLOCK), segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert reason is None


# ---------------------------------------------------------------------------
# Агрегация причины на уровне формы
# ---------------------------------------------------------------------------

def test_aggregate_single_reason() -> None:
    reasons = [ZeroBindingReason.NO_BIND_SLOT] * 3
    assert aggregate_form_zero_binding(reasons) is ZeroBindingReason.NO_BIND_SLOT


def test_aggregate_distinct_reasons_is_mixed() -> None:
    reasons = [
        ZeroBindingReason.NO_BIND_SLOT,
        ZeroBindingReason.CHAIN_NAME_MISMATCH,
    ]
    assert aggregate_form_zero_binding(reasons) is ZeroBindingReason.MIXED


def test_aggregate_scalar_and_too_short_is_mixed() -> None:
    """Реальная смесь на выгрузке: скаляр рядом с односегментной цепочкой."""
    reasons = [
        ZeroBindingReason.BIND_SLOT_NOT_A_CHAIN,
        ZeroBindingReason.CHAIN_TOO_SHORT,
    ]
    assert aggregate_form_zero_binding(reasons) is ZeroBindingReason.MIXED


def test_aggregate_empty_is_none() -> None:
    """Формы без непривязанных элементов в остаток #116 не попадают."""
    assert aggregate_form_zero_binding([]) is None


def test_aggregate_ignores_none_entries() -> None:
    reasons = [None, ZeroBindingReason.NO_BIND_SLOT, None]
    assert aggregate_form_zero_binding(reasons) is ZeroBindingReason.NO_BIND_SLOT


def test_aggregate_is_order_independent() -> None:
    first = [ZeroBindingReason.NO_BIND_SLOT, ZeroBindingReason.CHAIN_MALFORMED]
    assert aggregate_form_zero_binding(first) is aggregate_form_zero_binding(
        list(reversed(first))
    )


# ---------------------------------------------------------------------------
# Регрессии: ни одной выдуманной привязки, прежние пути не меняются
# ---------------------------------------------------------------------------

NEGATIVE_BLOCKS = [
    "0",
    None,
    ["1", "0"],
    ["5", SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD],
    ["x", SEG_ROOT, SEG_FIELD],
    _block(SEG_ROOT),
    _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ALIEN),
    _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ORPHAN),
]


@pytest.mark.parametrize("block", NEGATIVE_BLOCKS)
def test_no_invented_binding(
    block: object, segment_tables: dict, attribute_ids: dict
) -> None:
    """Классификация причины не смеет породить data_path."""
    path, _warnings = decode_chain_data_path(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert path is None


def test_confirmed_path_unchanged(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Существующее поведение #89 не меняется."""
    path, warnings = decode_chain_data_path(
        BOUND_BLOCK, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert path == EXPECTED_PATH
    assert warnings == []


def test_classification_does_not_mutate_input(
    segment_tables: dict, attribute_ids: dict
) -> None:
    block = _block(SEG_ROOT, ["0", UUID_TYPE], SEG_FIELD_ORPHAN)
    snapshot = json.dumps(block, ensure_ascii=False)
    tables_snapshot = json.dumps(
        {"|".join(k): v for k, v in segment_tables.items()},
        ensure_ascii=False,
        sort_keys=True,
    )
    classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert json.dumps(block, ensure_ascii=False) == snapshot
    assert (
        json.dumps(
            {"|".join(k): v for k, v in segment_tables.items()},
            ensure_ascii=False,
            sort_keys=True,
        )
        == tables_snapshot
    )


@pytest.mark.parametrize("block", NEGATIVE_BLOCKS)
def test_classification_is_deterministic(
    block: object, segment_tables: dict, attribute_ids: dict
) -> None:
    first = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    second = classify_element_zero_binding(
        block, segment_tables, attribute_ids, ELEMENT_NAME
    )
    assert first is second
    assert first is not None


def test_every_negative_block_gets_distinct_diagnosis(
    segment_tables: dict, attribute_ids: dict
) -> None:
    """Ни один структурный случай не остаётся без причины."""
    reasons = [
        classify_element_zero_binding(
            block, segment_tables, attribute_ids, ELEMENT_NAME
        )
        for block in NEGATIVE_BLOCKS
    ]
    assert all(reason is not None for reason in reasons)
    assert len(set(reasons)) >= 6
