"""Тесты декодирования data_path элементов формы (issue #85).

Покрываются оба формата секции ``data``:

* обычные формы — записи с ключом ``id``, путь строкой внутри ``raw``;
* управляемые формы — записи ``{"raw", "ver"}`` без ``id``, привязка задана
  UUID реквизита объекта-владельца.

Фрагменты ``raw`` взяты из реальной выгрузки
``Catalog/Банки/CatalogForm/ФормаЭлементаУправляемая`` и сокращены до
значимых позиций с сохранением исходной вложенности.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.elem_parser import (
    _is_element_record,
    decode_element_data_path,
    load_owner_attribute_map,
    parse_elem_json,
)

UUID_FORM = "02023637-7868-4a5f-8576-835a76e0c9ba"
UUID_DECOR = "48312c09-257f-4b29-b280-284dd89efc1e"
UUID_KORRSCHET = "3d446927-2fb8-11d7-85a2-0050bae0a772"
UUID_GOROD = "3d446926-2fb8-11d7-85a2-0050bae0a772"
UUID_ADRES = "3d446928-2fb8-11d7-85a2-0050bae0a772"
UUID_TELEFONY = "3d446929-2fb8-11d7-85a2-0050bae0a772"


def _catalog_attribute(uuid: str, name: str, synonym: str) -> list:
    """Узел реквизита Catalog.json, путь header[0][6][i][0][1][1][1].

    Инвариант, по которому строится карта: UUID лежит в ``node[1][2]``,
    имя реквизита — в ``node[2]``.
    """
    attribute_node = [
        "0",
        ["0", "0", uuid],
        f'"{name}"',
        ["ru", f'"{synonym}"'],
        '"(Общ)"',
    ]
    return [[["1", [["1", [attribute_node]]]]]]


def _catalog_json() -> dict:
    return {
        "header": [
            [
                None,
                None,
                None,
                None,
                None,
                None,
                [
                    "cf4abea7-37b2-11d4-940f-008048da11f9",
                    "11",
                    _catalog_attribute(UUID_KORRSCHET, "КоррСчет", "Корр. счет"),
                    _catalog_attribute(UUID_GOROD, "Город", "Город"),
                    _catalog_attribute(UUID_ADRES, "Адрес", "Адрес"),
                    _catalog_attribute(UUID_TELEFONY, "Телефоны", "Телефоны"),
                ],
            ]
        ]
    }


def _managed_field_raw(name: str, attribute_uuid: str, local_id: str) -> list:
    """Сокращённый raw поля управляемой формы.

    Значимо: raw[1] — идентификатор виджета с UUID формы, raw[11][2][1] —
    UUID реквизита привязки, вложенный блок подсказки — UUID оформления.
    """
    return [
        "37",
        [local_id, UUID_FORM],
        "0",
        "0",
        "0",
        "2",
        f'"{name}"',
        "1",
        "0",
        ["1", "0"],
        ["1", "0"],
        ["2", ["1"], ["0", attribute_uuid]],
        ["0"],
        [
            "12",
            [str(int(local_id) + 2), UUID_FORM],
            f'"{name}РасширеннаяПодсказка"',
            ["3", "0", ["0"], "0", "1", "0", UUID_DECOR],
        ],
    ]


def _managed_elem_json() -> dict:
    return {
        "params": [],
        "props": [],
        "commands": [],
        "tree": [
            {"name": "КоррСчет", "type": "Field"},
            {"name": "Город", "type": "Field"},
            {"name": "Адрес", "type": "Field"},
            {"name": "Телефоны", "type": "Field"},
            {"name": "Команда1", "type": "Button"},
            {
                "name": "Группа1",
                "type": "Group",
                "child": [
                    {
                        "name": "Группа2",
                        "type": "Group",
                        "child": [{"name": "Код", "type": "Field"}],
                    }
                ],
            },
        ],
        "data": {
            "КоррСчет": {
                "raw": _managed_field_raw("КоррСчет", UUID_KORRSCHET, "10"),
                "ver": "1",
            },
            "Город": {
                "raw": _managed_field_raw("Город", UUID_GOROD, "13"),
                "ver": "1",
            },
            "Адрес": {
                "raw": _managed_field_raw("Адрес", UUID_ADRES, "16"),
                "ver": "1",
            },
            "Телефоны": {
                "raw": _managed_field_raw("Телефоны", UUID_TELEFONY, "19"),
                "ver": "1",
            },
            "Команда1": {
                "raw": ["22", ["25", UUID_FORM], "0", '"Команда1"', ["1", "0"]],
                "ver": "1",
            },
            "Группа1": {
                "raw": ["36", ["27", UUID_FORM], "0", '"Группа1"'],
                "ver": "1",
            },
            "Группа1/Группа2": {
                "raw": ["36", ["28", UUID_FORM], "0", '"Группа2"'],
                "ver": "1",
            },
        },
    }


@pytest.fixture()
def managed_form(tmp_path: Path) -> Path:
    catalog_root = tmp_path / "Catalog" / "Банки"
    form_root = catalog_root / "CatalogForm" / "ФормаЭлементаУправляемая"
    form_root.mkdir(parents=True)

    (catalog_root / "Catalog.json").write_text(
        json.dumps(_catalog_json(), ensure_ascii=False),
        encoding="utf-8",
    )
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(_managed_elem_json(), ensure_ascii=False),
        encoding="utf-8",
    )
    return form_root


@pytest.fixture()
def legacy_form(tmp_path: Path) -> Path:
    form_root = tmp_path / "Catalog" / "Банки" / "CatalogForm" / "ФормаЭлемента"
    form_root.mkdir(parents=True)

    payload = {
        "tree": [
            {"name": "Город", "type": "Field"},
            {"name": "НадписьГород", "type": "Label"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/Город": {
                "id": 12,
                "raw": [["2", "1", "0"], ["Объект.Город"]],
            },
            "Страница1/НадписьГород": {
                "id": 13,
                "raw": [["2", "1", "0"], ['"Город:"']],
            },
        },
    }
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return form_root


class TestIsElementRecord:
    def test_legacy_record_with_id(self) -> None:
        assert _is_element_record({"id": 12, "raw": []}) is True

    def test_managed_record_without_id(self) -> None:
        assert _is_element_record({"raw": [], "ver": "1"}) is True

    def test_page_list_is_not_a_record(self) -> None:
        assert _is_element_record(["Страница1"]) is False

    def test_foreign_dict_is_not_a_record(self) -> None:
        assert _is_element_record({"ver": "1"}) is False


class TestOwnerAttributeMap:
    def test_map_is_built_from_catalog_json(self, managed_form: Path) -> None:
        warnings: list[str] = []
        mapping = load_owner_attribute_map(managed_form, warnings)

        assert warnings == []
        assert mapping[UUID_GOROD] == "Город"
        assert mapping[UUID_TELEFONY] == "Телефоны"

    def test_service_uuids_are_absent_from_map(self, managed_form: Path) -> None:
        mapping = load_owner_attribute_map(managed_form, [])

        assert UUID_FORM not in mapping
        assert UUID_DECOR not in mapping

    def test_missing_metadata_produces_warning(self, tmp_path: Path) -> None:
        warnings: list[str] = []
        assert load_owner_attribute_map(tmp_path, warnings) == {}
        assert len(warnings) == 1


class TestDecodeElementDataPath:
    def test_none_raw(self) -> None:
        assert decode_element_data_path(None) == (None, [])

    def test_string_raw_returned_as_is(self) -> None:
        assert decode_element_data_path("Объект.Город") == ("Объект.Город", [])

    def test_dict_raw(self) -> None:
        assert decode_element_data_path({"DataPath": "Объект.Город"}) == (
            "Объект.Город",
            [],
        )

    def test_legacy_list_raw_with_inline_path(self) -> None:
        raw = [["2", "1", "0"], ["Объект.Город"]]
        assert decode_element_data_path(raw) == ("Объект.Город", [])

    def test_managed_raw_resolved_via_attribute_map(self) -> None:
        raw = _managed_field_raw("Город", UUID_GOROD, "13")
        mapping = {UUID_GOROD: "Город"}

        assert decode_element_data_path(raw, mapping) == ("Объект.Город", [])

    def test_service_uuids_do_not_produce_binding(self) -> None:
        raw = ["22", ["25", UUID_FORM], ["3", "0", UUID_DECOR]]
        mapping = {UUID_GOROD: "Город"}

        assert decode_element_data_path(raw, mapping) == (None, [])

    def test_ambiguous_binding_is_refused(self) -> None:
        raw = [["0", UUID_GOROD], ["0", UUID_TELEFONY]]
        mapping = {UUID_GOROD: "Город", UUID_TELEFONY: "Телефоны"}

        path, warnings = decode_element_data_path(raw, mapping)

        assert path is None
        assert len(warnings) == 1
        assert "неоднозначная привязка" in warnings[0]

    def test_custom_object_prefix(self) -> None:
        raw = _managed_field_raw("Город", UUID_GOROD, "13")

        assert decode_element_data_path(
            raw, {UUID_GOROD: "Город"}, object_prefix="Запись"
        ) == ("Запись.Город", [])

    def test_empty_map_produces_warning(self) -> None:
        raw = _managed_field_raw("Город", UUID_GOROD, "13")

        path, warnings = decode_element_data_path(raw, {})

        assert path is None
        assert len(warnings) == 1


class TestParseManagedForm:
    def test_all_data_records_become_elements(self, managed_form: Path) -> None:
        result = parse_elem_json(managed_form)

        assert result.elem_index_ok is True
        names = {element["name"] for element in result.elements}

        assert {"Город", "Адрес", "Телефоны", "КоррСчет", "Команда1"} <= names

    def test_acceptance_criteria_bindings(self, managed_form: Path) -> None:
        result = parse_elem_json(managed_form)
        actual = {
            element["name"]: element.get("data_path")
            for element in result.elements
        }

        assert actual["Город"] == "Объект.Город"
        assert actual["Телефоны"] == "Объект.Телефоны"
        assert actual["Адрес"] == "Объект.Адрес"

    def test_button_has_no_binding(self, managed_form: Path) -> None:
        result = parse_elem_json(managed_form)
        button = next(
            element for element in result.elements
            if element["name"] == "Команда1"
        )

        assert button.get("data_path") is None

    def test_group_path_hierarchy_preserved(self, managed_form: Path) -> None:
        result = parse_elem_json(managed_form)
        group2 = next(
            element for element in result.elements
            if element["name"] == "Группа2"
        )

        assert group2["path"] == "Группа1/Группа2"
        assert group2["parent"] == "Группа1"

    def test_index_file_written(self, managed_form: Path) -> None:
        parse_elem_json(managed_form)
        index_path = managed_form / "form_elements_index.json"

        assert index_path.exists()
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        assert payload["form"] == "ФормаЭлементаУправляемая"


class TestParseLegacyFormRegression:
    def test_inline_path_still_decoded(self, legacy_form: Path) -> None:
        result = parse_elem_json(legacy_form)
        actual = {
            element["name"]: element.get("data_path")
            for element in result.elements
        }

        assert actual["Город"] == "Объект.Город"

    def test_page_hierarchy_preserved(self, legacy_form: Path) -> None:
        result = parse_elem_json(legacy_form)
        field = next(
            element for element in result.elements
            if element["name"] == "Город"
        )

        assert field["path"] == "Страница1/Город"
        assert field["page"] == "Страница1"
