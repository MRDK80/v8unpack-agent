"""TDD-тесты сегментных цепочек data_path в управляемых формах (issue #89).

Часть форм с непустой картой реквизитов объекта не получала ни одной
привязки: элементы ссылаются не на реквизиты объекта по UUID, а на
вложенные поля реквизита формы через цепочку сегментов в ``raw[11]``.

Подтверждённый контракт::

    raw[11] = [<счётчик>, <сегмент>, ["0", <uuid типа>], <сегмент>, ...]

* ``<счётчик>`` — количество элементов после него;
* ``["0", <uuid>]`` — разделитель-тип, в пути не участвует;
* ``[<id>]`` — сегмент, разрешаемый по дереву реквизитов формы (``props``);
* ``[<id>, <uuid таблицы>]`` — сегмент, разрешаемый по таблице определений
  из большого JSON формы (``$.form[0][0][3]``).

Привязка принимается только при точном совпадении склейки имён сегментов
с именем элемента. Любое расхождение оставляет элемент без ``data_path``.

Все структуры в этом файле синтетические и обезличенные.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from v8unpack_agent.elem_parser import (
    build_form_attribute_ids,
    build_form_segment_tables,
    decode_chain_data_path,
    parse_elem_json,
)

TYPE_UUID = "11111111-1111-4111-8111-111111111111"
TABLE_UUID = "22222222-2222-4222-8222-222222222222"
ALIEN_UUID = "33333333-3333-4333-8333-333333333333"
OBJECT_ATTR_UUID = "44444444-4444-4444-8444-444444444444"


# ---------------------------------------------------------------------------
# Синтетические структуры
# ---------------------------------------------------------------------------


def _field(field_id: str, name: str) -> list:
    """Поле таблицы определений: id в позиции 1, имя в позиции 3."""
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
    """Узел таблицы определений: ["0", <адрес>, <кол-во>, <поле>, ...]."""
    return ["0", [str(len(address))] + address, str(len(fields)), *fields]


def _form_json() -> dict:
    """Большой JSON формы: таблицы определений лежат в $.form[0][0][3]."""
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
        {"name": "СписокВыбора", "id": "9", "raw": []},
    ]


def _chain_full() -> list:
    """Полная цепочка: props → props → таблица → таблица."""
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
    """Короткая цепочка целиком по реквизитам формы."""
    return ["3", ["1"], ["0", TYPE_UUID], ["3"]]


def _widget_raw(name: str, block: object | None) -> list:
    """Запись элемента управляемой формы: блок привязки в позиции 11."""
    raw: list = [
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
    return raw


@pytest.fixture()
def tables() -> dict:
    return build_form_segment_tables(_form_json())


@pytest.fixture()
def attr_ids() -> dict:
    return build_form_attribute_ids(_props())


# ---------------------------------------------------------------------------
# Положительные случаи
# ---------------------------------------------------------------------------


class TestChainResolution:
    def test_full_chain_resolves_to_dotted_path(self, tables, attr_ids) -> None:
        path, warnings = decode_chain_data_path(
            _chain_full(), tables, attr_ids, "СтруктураДанныхУзелГруппаБПолеПять"
        )

        assert path == "СтруктураДанных.Узел.ГруппаБ.ПолеПять"
        assert warnings == []

    def test_short_chain_uses_form_attributes_only(self, tables, attr_ids) -> None:
        path, warnings = decode_chain_data_path(
            _chain_short(), tables, attr_ids, "СтруктураДанных_Прочее"
        )

        assert path == "СтруктураДанных._Прочее"
        assert warnings == []

    def test_segment_tables_are_keyed_by_address(self) -> None:
        tables = build_form_segment_tables(_form_json())

        assert len(tables) == 2
        assert "ПолеПять" in {
            name for fields in tables.values() for name in fields.values()
        }

    def test_form_attribute_ids_include_nested_children(self) -> None:
        attr_ids = build_form_attribute_ids(_props())

        assert attr_ids[(None, 1)] == "СтруктураДанных"
        assert attr_ids[("СтруктураДанных", 2)] == "Узел"
        assert attr_ids[("СтруктураДанных", 3)] == "_Прочее"


# ---------------------------------------------------------------------------
# Отрицательные случаи: ложные привязки недопустимы
# ---------------------------------------------------------------------------


class TestChainRefusals:
    def test_name_mismatch_is_refused(self, tables, attr_ids) -> None:
        """Склейка сегментов не совпала с именем элемента — привязки нет."""
        path, warnings = decode_chain_data_path(
            _chain_full(), tables, attr_ids, "СовершенноДругоеИмя"
        )

        assert path is None
        assert len(warnings) == 1
        assert "склейк" in warnings[0].lower() or "имя" in warnings[0].lower()

    def test_unknown_segment_id_is_refused(self, tables, attr_ids) -> None:
        block = ["3", ["1"], ["0", TYPE_UUID], ["77"]]

        path, warnings = decode_chain_data_path(
            block, tables, attr_ids, "СтруктураДанныхНеизвестно"
        )

        assert path is None
        assert warnings

    def test_alien_table_uuid_is_refused(self, tables, attr_ids) -> None:
        """UUID таблицы, которой нет в определениях, не даёт привязку."""
        block = [
            "5",
            ["1"],
            ["0", TYPE_UUID],
            ["2"],
            ["0", TYPE_UUID],
            ["5", ALIEN_UUID],
        ]

        path, _warnings = decode_chain_data_path(
            block, tables, attr_ids, "СтруктураДанныхУзелПолеПять"
        )

        assert path is None

    def test_broken_counter_is_refused(self, tables, attr_ids) -> None:
        """Счётчик не совпадает с длиной блока — структура повреждена."""
        block = ["9", ["1"], ["0", TYPE_UUID], ["3"]]

        path, _warnings = decode_chain_data_path(
            block, tables, attr_ids, "СтруктураДанных_Прочее"
        )

        assert path is None

    def test_legacy_object_uuid_block_is_not_touched(self, tables, attr_ids) -> None:
        """Блок старого формата (ссылка на реквизит объекта) не наш случай."""
        block = ["2", ["1"], ["0", OBJECT_ATTR_UUID]]

        path, _warnings = decode_chain_data_path(
            block, tables, attr_ids, "Адрес"
        )

        assert path is None

    def test_empty_tables_do_not_invent_path(self, attr_ids) -> None:
        path, _warnings = decode_chain_data_path(
            _chain_full(), {}, attr_ids, "СтруктураДанныхУзелГруппаБПолеПять"
        )

        assert path is None

    def test_not_a_list_is_safe(self, tables, attr_ids) -> None:
        assert decode_chain_data_path(None, tables, attr_ids, "X") == (None, [])
        assert decode_chain_data_path("строка", tables, attr_ids, "X") == (None, [])

    def test_input_is_not_mutated(self, tables, attr_ids) -> None:
        block = _chain_full()
        snapshot = copy.deepcopy(block)
        tables_snapshot = copy.deepcopy(tables)

        decode_chain_data_path(
            block, tables, attr_ids, "СтруктураДанныхУзелГруппаБПолеПять"
        )

        assert block == snapshot
        assert tables == tables_snapshot


# ---------------------------------------------------------------------------
# Интеграция через parse_elem_json
# ---------------------------------------------------------------------------


def _write_form(tmp_path: Path) -> Path:
    form_root = tmp_path / "Catalog" / "Объект1" / "CatalogForm" / "Форма1"
    form_root.mkdir(parents=True)

    payload = {
        "params": [],
        "props": _props(),
        "commands": [],
        "tree": [
            {"name": "СтруктураДанныхУзелГруппаБПолеПять", "type": "Field"},
            {"name": "НадписьБезПривязки", "type": "Label"},
        ],
        "data": {
            "Группа1/СтруктураДанныхУзелГруппаБПолеПять": {
                "ver": "1",
                "raw": _widget_raw(
                    "СтруктураДанныхУзелГруппаБПолеПять", _chain_full()
                ),
            },
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


class TestParseElemJsonIntegration:
    def test_chain_element_gets_data_path(self, tmp_path: Path) -> None:
        result = parse_elem_json(_write_form(tmp_path))
        by_name = {element["name"]: element for element in result.elements}

        assert by_name["СтруктураДанныхУзелГруппаБПолеПять"]["data_path"] == (
            "СтруктураДанных.Узел.ГруппаБ.ПолеПять"
        )

    def test_element_without_block_has_no_binding(self, tmp_path: Path) -> None:
        result = parse_elem_json(_write_form(tmp_path))
        label = next(
            element for element in result.elements
            if element["name"] == "НадписьБезПривязки"
        )

        assert label.get("data_path") is None

    def test_elem_json_file_is_not_modified(self, tmp_path: Path) -> None:
        form_root = _write_form(tmp_path)
        elem_path = form_root / "CatalogForm.elem.json"
        before = elem_path.read_text(encoding="utf-8")

        parse_elem_json(form_root)

        assert elem_path.read_text(encoding="utf-8") == before
