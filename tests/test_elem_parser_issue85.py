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
    _legacy_attribute_name,
    _merge_source_duplicates,
    decode_element_data_path,
    decode_legacy_data_path,
    is_legacy_form_data,
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


UUID_SPRAVOCHNIK = "3d446915-2fb8-11d7-85a2-0050bae0a772"


def _legacy_widget_raw(name: str, synonym: str | None = None) -> list:
    """Сокращённый raw элемента обычной формы.

    Значимо: raw[4] = ["14", "\"Имя\"", ...] — тег есть у всех элементов,
    включая надписи, поэтому сам по себе привязки не означает. UUID в raw
    описывают класс виджета и одинаковы у разных элементов.
    """
    caption = ["1", "1", ['"ru"', f'"{synonym}"']] if synonym else ["1", "0"]
    return [
        "381ed624-9217-4e63-85db-c4c3cb87daae",
        "4",
        ["9", ['"Pattern"', ['"S"', "100", "1"]], [[["16", "1", caption]]]],
        ["8", "94", "61", "354", "80", "1"],
        ["14", f'"{name}"', "4294967295", "0", "0", "0"],
        ["0"],
    ]


def _legacy_elem_json() -> dict:
    """Форма вида Catalog/Банки/CatalogForm/ФормаЭлемента."""
    return {
        "params": [],
        "props": [
            {
                "name": "СправочникОбъект",
                "id": "0",
                "raw": [
                    ["0"], "0", "0", "1", '"СправочникОбъект"',
                    ['"Pattern"', ['"#"', UUID_SPRAVOCHNIK]],
                ],
            }
        ],
        "commands": [],
        "tree": [
            {"name": "НадписьКод", "type": "Label"},
            {"name": "Код", "type": "Field"},
            {"name": "НадписьНаименование", "type": "Label"},
            {"name": "Наименование", "type": "Field"},
            {"name": "НадписьГород", "type": "Label"},
            {"name": "Город", "type": "Field"},
            {"name": "КоррСчет", "type": "Field"},
            {"name": "Родитель", "type": "Field"},
            {"name": "ДействияФормы", "type": "CommandPanel"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1": {"ver": "1", "page_format_version": "1", "raw": [], "info": {}},
            "Страница1/НадписьКод": {
                "id": 1, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("НадписьКод"),
            },
            "Страница1/Код": {
                "id": 2, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("Код"),
                "prop": "СправочникОбъект",
            },
            "Страница1/НадписьНаименование": {
                "id": 3, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("НадписьНаименование"),
            },
            "Страница1/Наименование": {
                "id": 4, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("Наименование"),
                "prop": "СправочникОбъект",
            },
            "Страница1/НадписьГород": {
                "id": 9, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("НадписьГород"),
            },
            "Страница1/Город": {
                "id": 10, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("Город", synonym="Город"),
                "prop": "СправочникОбъект",
            },
            "Страница1/КоррСчет": {
                "id": 8, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("КоррСчет", synonym="Корр. счет"),
                "prop": "СправочникОбъект",
            },
            "Страница1/Родитель": {
                "id": 6, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("Родитель"),
                "prop": "СправочникОбъект",
            },
            "Страница1/ДействияФормы": {
                "id": 15, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("ДействияФормы"),
            },
        },
    }


def _list_form_elem_json() -> dict:
    """Форма списка: prop совпадает с именем реквизита формы."""
    return {
        "props": [
            {"name": "СправочникСписок", "id": "0", "raw": []},
            {"name": "ИнформационнаяНадписьАдрес", "id": "5", "raw": []},
        ],
        "tree": [
            {"name": "СправочникСписок", "type": "Table"},
            {"name": "ИнформационнаяНадписьАдрес", "type": "Label"},
            {"name": "РазделительВертикальный", "type": "Separator"},
        ],
        "data": {
            "-pages-": ["Страница1"],
            "Страница1/СправочникСписок": {
                "id": 1, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("СправочникСписок"),
                "prop": "СправочникСписок",
            },
            "Страница1/ИнформационнаяНадписьАдрес": {
                "id": 6, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("ИнформационнаяНадписьАдрес"),
                "prop": "ИнформационнаяНадписьАдрес",
            },
            "Страница1/РазделительВертикальный": {
                "id": 4, "ver": "1", "page": "Страница1",
                "raw": _legacy_widget_raw("РазделительВертикальный"),
            },
        },
    }


@pytest.fixture()
def legacy_element_form(tmp_path: Path) -> Path:
    catalog_root = tmp_path / "Catalog" / "Банки"
    form_root = catalog_root / "CatalogForm" / "ФормаЭлемента"
    form_root.mkdir(parents=True)
    (catalog_root / "Catalog.json").write_text(
        json.dumps(_catalog_json(), ensure_ascii=False), encoding="utf-8"
    )
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(_legacy_elem_json(), ensure_ascii=False), encoding="utf-8"
    )
    return form_root


@pytest.fixture()
def legacy_list_form(tmp_path: Path) -> Path:
    catalog_root = tmp_path / "Catalog" / "Банки"
    form_root = catalog_root / "CatalogForm" / "ФормаСписка"
    form_root.mkdir(parents=True)
    (catalog_root / "Catalog.json").write_text(
        json.dumps(_catalog_json(), ensure_ascii=False), encoding="utf-8"
    )
    (form_root / "CatalogForm.elem.json").write_text(
        json.dumps(_list_form_elem_json(), ensure_ascii=False), encoding="utf-8"
    )
    return form_root


class TestLegacyAttributeName:
    def test_tag_14_node_is_decoded(self) -> None:
        raw = _legacy_widget_raw("Город")
        assert _legacy_attribute_name(raw) == "Город"

    def test_foreign_tag_is_rejected(self) -> None:
        raw = ["x", "1", "2", "3", ["37", '"Город"']]
        assert _legacy_attribute_name(raw) is None

    def test_short_raw_is_safe(self) -> None:
        assert _legacy_attribute_name(["14"]) is None

    def test_non_list_raw_is_safe(self) -> None:
        assert _legacy_attribute_name("Объект.Город") is None


class TestDecodeLegacyDataPath:
    def test_object_attribute_gets_prefix(self) -> None:
        record = {
            "raw": _legacy_widget_raw("Город"),
            "prop": "СправочникОбъект",
        }
        assert decode_legacy_data_path(record) == ("СправочникОбъект.Город", [])

    def test_form_attribute_has_no_prefix(self) -> None:
        record = {
            "raw": _legacy_widget_raw("ИнформационнаяНадписьАдрес"),
            "prop": "ИнформационнаяНадписьАдрес",
        }
        assert decode_legacy_data_path(record) == (
            "ИнформационнаяНадписьАдрес",
            [],
        )

    def test_record_without_prop_has_no_binding(self) -> None:
        record = {"raw": _legacy_widget_raw("НадписьГород")}
        assert decode_legacy_data_path(record) == (None, [])

    def test_empty_prop_has_no_binding(self) -> None:
        record = {"raw": _legacy_widget_raw("Город"), "prop": "  "}
        assert decode_legacy_data_path(record) == (None, [])

    def test_missing_attribute_falls_back_to_prop(self) -> None:
        record = {"raw": ["a", "b"], "prop": "СправочникОбъект"}
        path, warnings = decode_legacy_data_path(record)
        assert path == "СправочникОбъект"
        assert len(warnings) == 1


class TestIsLegacyFormData:
    def test_legacy_section_detected(self) -> None:
        assert is_legacy_form_data(_legacy_elem_json()["data"]) is True

    def test_managed_section_not_detected(self) -> None:
        assert is_legacy_form_data(_managed_elem_json()["data"]) is False


class TestParseLegacyElementForm:
    def test_object_attributes_are_bound(self, legacy_element_form: Path) -> None:
        result = parse_elem_json(legacy_element_form)
        actual = {
            element["name"]: element.get("data_path")
            for element in result.elements
        }

        assert actual["Город"] == "СправочникОбъект.Город"
        assert actual["КоррСчет"] == "СправочникОбъект.КоррСчет"

    def test_standard_attributes_are_bound(self, legacy_element_form: Path) -> None:
        """Код, Наименование, Родитель отсутствуют в Catalog.json,
        но в обычных формах привязка идёт по имени и разрешается."""
        result = parse_elem_json(legacy_element_form)
        actual = {
            element["name"]: element.get("data_path")
            for element in result.elements
        }

        assert actual["Код"] == "СправочникОбъект.Код"
        assert actual["Наименование"] == "СправочникОбъект.Наименование"
        assert actual["Родитель"] == "СправочникОбъект.Родитель"

    def test_labels_have_no_binding(self, legacy_element_form: Path) -> None:
        result = parse_elem_json(legacy_element_form)
        labels = [
            element for element in result.elements
            if element["name"].startswith("Надпись")
        ]

        assert len(labels) == 3
        assert all(element.get("data_path") is None for element in labels)

    def test_command_panel_has_no_binding(self, legacy_element_form: Path) -> None:
        result = parse_elem_json(legacy_element_form)
        panel = next(
            element for element in result.elements
            if element["name"] == "ДействияФормы"
        )

        assert panel.get("data_path") is None

    def test_no_warnings_on_legacy_form(self, legacy_element_form: Path) -> None:
        result = parse_elem_json(legacy_element_form)
        assert result.warnings == []

    def test_uuid_branch_not_used_for_legacy(self, legacy_element_form: Path) -> None:
        """UUID в raw обычной формы описывают класс виджета; префикс
        «Объект.» из ветки управляемых форм появляться не должен."""
        result = parse_elem_json(legacy_element_form)
        paths = [
            element.get("data_path") or "" for element in result.elements
        ]

        assert not any(path.startswith("Объект.") for path in paths)


class TestParseLegacyListForm:
    """Форма списка: имена реквизитов формы совпадают с именами элементов,
    поэтому один и тот же name приходит и из ``data``, и из ``props``.
    Привязку несёт запись из ``data`` — по ней и проверяем.
    """

    @staticmethod
    def _by_name(result, name: str) -> dict:
        return next(
            element for element in result.elements
            if element["name"] == name and element.get("source") == "data"
        )

    def test_form_attribute_bound_without_prefix(self, legacy_list_form: Path) -> None:
        result = parse_elem_json(legacy_list_form)

        assert self._by_name(result, "СправочникСписок")["data_path"] == (
            "СправочникСписок"
        )
        assert self._by_name(result, "ИнформационнаяНадписьАдрес")["data_path"] == (
            "ИнформационнаяНадписьАдрес"
        )

    def test_props_duplicate_is_merged_into_data(self, legacy_list_form: Path) -> None:
        """Записи об одном элементе из data и props схлопываются в одну,
        побеждает data — она несёт path, page и data_path."""
        result = parse_elem_json(legacy_list_form)
        duplicates = [
            element for element in result.elements
            if element["name"] == "СправочникСписок"
        ]

        assert len(duplicates) == 1
        merged = duplicates[0]
        assert merged["source"] == "data"
        assert merged["data_path"] == "СправочникСписок"
        assert merged["path"] == "Страница1/СправочникСписок"
        assert merged["merged_sources"] == ["data", "props"]

    def test_lookup_by_name_is_unambiguous(self, legacy_list_form: Path) -> None:
        """Потребитель индекса может искать элемент через next() по имени
        и гарантированно получит запись с привязкой."""
        result = parse_elem_json(legacy_list_form)
        names = [element["name"] for element in result.elements]

        assert len(names) == len(set(names))

    def test_separator_has_no_binding(self, legacy_list_form: Path) -> None:
        result = parse_elem_json(legacy_list_form)
        separator = self._by_name(result, "РазделительВертикальный")

        assert separator.get("data_path") is None

    def test_no_ambiguity_warning(self, legacy_list_form: Path) -> None:
        """Раньше форма списка давала «неоднозначная привязка»."""
        result = parse_elem_json(legacy_list_form)
        assert not any("неоднозначн" in w for w in result.warnings)

class TestMergeSourceDuplicates:
    def test_data_wins_over_props(self) -> None:
        elements = [
            {"name": "Список", "type": "Table", "path": "Страница1/Список",
             "parent_path": "Страница1", "page": "Страница1",
             "source": "data", "data_path": "Список"},
            {"name": "Список", "type": "Unknown", "path": None,
             "parent_path": None, "page": None, "source": "props"},
        ]
        merged = _merge_source_duplicates(elements)

        assert len(merged) == 1
        assert merged[0]["source"] == "data"
        assert merged[0]["type"] == "Table"

    def test_missing_fields_are_taken_from_loser(self) -> None:
        elements = [
            {"name": "Список", "type": "Unknown", "path": "Страница1/Список",
             "source": "data"},
            {"name": "Список", "type": "Table", "path": None,
             "source": "props", "comment": "из props"},
        ]
        merged = _merge_source_duplicates(elements)

        assert merged[0]["type"] == "Table"
        assert merged[0]["comment"] == "из props"

    def test_distinct_paths_are_not_merged(self) -> None:
        """Два разных элемента с одинаковым именем в разных группах
        остаются раздельными."""
        elements = [
            {"name": "Код", "type": "Field", "path": "Группа1/Код",
             "source": "data"},
            {"name": "Код", "type": "Field", "path": "Группа2/Код",
             "source": "data"},
        ]
        merged = _merge_source_duplicates(elements)

        assert len(merged) == 2

    def test_order_is_preserved(self) -> None:
        elements = [
            {"name": "А", "type": "Field", "path": "П/А", "source": "data"},
            {"name": "Б", "type": "Field", "path": "П/Б", "source": "data"},
            {"name": "А", "type": "Unknown", "path": None, "source": "props"},
        ]
        merged = _merge_source_duplicates(elements)

        assert [element["name"] for element in merged] == ["А", "Б"]

    def test_single_source_element_is_untouched(self) -> None:
        elements = [
            {"name": "Город", "type": "Field", "path": "Страница1/Город",
             "source": "data", "data_path": "СправочникОбъект.Город"},
        ]
        merged = _merge_source_duplicates(elements)

        assert merged == elements
        assert "merged_sources" not in merged[0]


class TestOwnerMetadataDiscoveryRegression:
    def test_dynamic_owner_type_json(self, tmp_path: Path) -> None:
        owner = tmp_path / "CustomMetadata" / "Объект1"
        form_root = owner / "CustomForm" / "Форма1"
        form_root.mkdir(parents=True)
        (owner / "CustomMetadata.json").write_text(
            json.dumps(_catalog_json(), ensure_ascii=False), encoding="utf-8"
        )
        warnings: list[str] = []
        mapping = load_owner_attribute_map(form_root, warnings)
        assert warnings == []
        assert mapping[UUID_GOROD] == "Город"

    def test_common_form_has_no_missing_owner_warning(self, tmp_path: Path) -> None:
        form_root = tmp_path / "CommonForm" / "ОбщаяФорма1"
        form_root.mkdir(parents=True)
        warnings: list[str] = []
        assert load_owner_attribute_map(form_root, warnings) == {}
        assert warnings == []


class TestManagedBindingDiagnosticsRegression:
    def test_empty_map_warning_emitted_once_per_form(self, tmp_path: Path) -> None:
        form_root = tmp_path / "Catalog" / "Банки" / "CatalogForm" / "Форма1"
        form_root.mkdir(parents=True)
        (form_root / "CatalogForm.elem.json").write_text(
            json.dumps(_managed_elem_json(), ensure_ascii=False), encoding="utf-8"
        )
        result = parse_elem_json(form_root)
        empty_map = [
            warning for warning in result.warnings
            if "карта реквизитов владельца пуста" in warning
        ]
        assert len(empty_map) == 1

    def test_ambiguous_candidates_resolved_by_element_name(self) -> None:
        raw = [["0", UUID_GOROD], ["0", UUID_TELEFONY]]
        mapping = {UUID_GOROD: "Город", UUID_TELEFONY: "Телефоны"}
        assert decode_element_data_path(
            raw, mapping, element_name="Город"
        ) == ("Объект.Город", [])

    def test_unresolved_ambiguity_mentions_element_name(self) -> None:
        raw = [["0", UUID_GOROD], ["0", UUID_TELEFONY]]
        mapping = {UUID_GOROD: "Город", UUID_TELEFONY: "Телефоны"}
        path, warnings = decode_element_data_path(
            raw, mapping, element_name="Адрес"
        )
        assert path is None
        assert len(warnings) == 1
        assert "элемент='Адрес'" in warnings[0]


    def test_common_form_has_no_empty_map_warning(self, tmp_path: Path) -> None:
        form_root = tmp_path / "CommonForm" / "ОбщаяФорма1"
        form_root.mkdir(parents=True)
        (form_root / "CommonForm.elem.json").write_text(
            json.dumps(_managed_elem_json(), ensure_ascii=False), encoding="utf-8"
        )
        result = parse_elem_json(form_root)
        assert not any(
            "карта реквизитов владельца пуста" in warning
            for warning in result.warnings
        )


class TestManagedStructuralDataPaths:
    @pytest.fixture
    def structural_form(self, tmp_path: Path) -> Path:
        form_root = tmp_path / "CommonForm" / "СтруктурныеПривязки"
        form_root.mkdir(parents=True)

        payload = {
            "params": [],
            "props": [
                {"name": "Список", "id": "1", "raw": []},
                {"name": "Фильтр", "id": "2", "raw": []},
                {
                    "name": "КонтейнерРеквизитов",
                    "id": "3",
                    "raw": [],
                    "child": [
                        {
                            "name": "ВложенныйРеквизит",
                            "id": "4",
                            "raw": [],
                        },
                    ],
                },
            ],
            "commands": [],
            "tree": [
                {"name": "СписокНоменклатура", "type": "Field"},
                {"name": "Фильтр", "type": "Field"},
                {"name": "ВложенныйРеквизит", "type": "Field"},
                {"name": "Код", "type": "Field"},
            ],
            "data": {
                "Список/СписокНоменклатура": {
                    "raw": [UUID_FORM, UUID_DECOR],
                    "ver": 1,
                },
                "ГруппаОтбора/Фильтр": {
                    "raw": [UUID_FORM, UUID_DECOR],
                    "ver": 1,
                },
                "Группа/ВложенныйРеквизит": {
                    "raw": [UUID_FORM, UUID_DECOR],
                    "ver": 1,
                },
                "Код": {
                    "raw": [UUID_FORM, UUID_DECOR],
                    "ver": 1,
                },
            },
        }

        (form_root / "CommonForm.elem.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return form_root

    @staticmethod
    def _by_name(result, name: str) -> dict:
        return next(
            element
            for element in result.elements
            if element["name"] == name
            and element.get("source") == "data"
        )

    def test_table_column_uses_form_attribute_path(
        self,
        structural_form: Path,
    ) -> None:
        result = parse_elem_json(structural_form)

        element = self._by_name(result, "СписокНоменклатура")

        assert element["data_path"] == "Список.Номенклатура"

    def test_exact_form_attribute_uses_own_name(
        self,
        structural_form: Path,
    ) -> None:
        result = parse_elem_json(structural_form)

        element = self._by_name(result, "Фильтр")

        assert element["data_path"] == "Фильтр"

    def test_nested_form_attribute_uses_own_name(
        self,
        structural_form: Path,
    ) -> None:
        result = parse_elem_json(structural_form)

        element = self._by_name(result, "ВложенныйРеквизит")

        assert element["data_path"] == "ВложенныйРеквизит"

    def test_unknown_name_is_not_guessed(
        self,
        structural_form: Path,
    ) -> None:
        result = parse_elem_json(structural_form)

        element = self._by_name(result, "Код")

        assert element.get("data_path") is None
