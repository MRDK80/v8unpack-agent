"""Тесты хелперов синтетических фикстур управляемой формы (issue #52).

Все тесты синтетические — реальные файлы 1С не требуются.
Только stdlib + pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._managed_fixtures import (
    make_managed_form_elem_json,
    write_managed_form_elem,
)


# ---------------------------------------------------------------------------
# make_managed_form_elem_json: структура dict
# ---------------------------------------------------------------------------


class TestMakeManagedFormElemJsonStructure:
    def test_top_level_keys_present(self):
        result = make_managed_form_elem_json()
        for key in ("data", "tree", "props", "commands", "params"):
            assert key in result, f"Ключ '{key}' отсутствует в результате"

    def test_data_has_pages_key(self):
        result = make_managed_form_elem_json(pages=["СтраницаА", "СтраницаБ"])
        assert "-pages-" in result["data"]
        assert result["data"]["-pages-"] == ["СтраницаА", "СтраницаБ"]

    def test_data_has_path_keys_for_each_page(self):
        result = make_managed_form_elem_json(pages=["Страница1"])
        data = result["data"]
        # должны быть ключи-пути вида Страница1, Страница1/Панель1, Страница1/Панель1/Кнопка1
        assert "Страница1" in data
        assert "Страница1/Панель1" in data
        assert "Страница1/Панель1/Кнопка1" in data

    def test_data_path_key_value_is_dict(self):
        result = make_managed_form_elem_json(pages=["Страница1"])
        val = result["data"]["Страница1"]
        assert isinstance(val, dict)

    def test_tree_is_list(self):
        result = make_managed_form_elem_json()
        assert isinstance(result["tree"], list)

    def test_tree_nodes_have_name_and_type(self):
        result = make_managed_form_elem_json(pages=["Страница1"])
        tree = result["tree"]
        assert len(tree) >= 1
        node = tree[0]
        assert node.get("name") == "Страница1"
        assert node.get("type") == "Page"

    def test_props_is_list(self):
        result = make_managed_form_elem_json()
        assert isinstance(result["props"], list)

    def test_commands_is_list(self):
        result = make_managed_form_elem_json()
        assert isinstance(result["commands"], list)

    def test_params_is_list(self):
        result = make_managed_form_elem_json()
        assert isinstance(result["params"], list)

    def test_multiple_pages_data_paths(self):
        result = make_managed_form_elem_json(pages=["Стр1", "Стр2"])
        data = result["data"]
        assert "Стр1" in data
        assert "Стр2" in data
        assert "Стр1/Панель1" in data
        assert "Стр2/Панель2" in data


# ---------------------------------------------------------------------------
# make_managed_form_elem_json: шумовые поля
# ---------------------------------------------------------------------------


class TestMakeManagedFormElemJsonNoise:
    def test_with_noise_true_has_guid_fields(self):
        result = make_managed_form_elem_json(with_noise=True)
        data = result["data"]
        page_entry = data.get("Страница1", {})
        assert "id" in page_entry, "Шумовое поле 'id' отсутствует при with_noise=True"
        assert "ref" in page_entry, "Шумовое поле 'ref' отсутствует при with_noise=True"

    def test_with_noise_true_has_coordinate_fields(self):
        result = make_managed_form_elem_json(with_noise=True)
        data = result["data"]
        page_entry = data.get("Страница1", {})
        for field in ("top", "left", "width", "height"):
            assert field in page_entry, f"Координатное поле '{field}' отсутствует при with_noise=True"

    def test_with_noise_true_has_style_fields(self):
        result = make_managed_form_elem_json(with_noise=True)
        data = result["data"]
        page_entry = data.get("Страница1", {})
        for field in ("color", "font"):
            assert field in page_entry, f"Оформительское поле '{field}' отсутствует при with_noise=True"

    def test_with_noise_false_no_guid_fields(self):
        result = make_managed_form_elem_json(with_noise=False)
        data = result["data"]
        page_entry = data.get("Страница1", {})
        assert "id" not in page_entry
        assert "ref" not in page_entry

    def test_tree_node_has_noise_when_requested(self):
        result = make_managed_form_elem_json(with_noise=True)
        tree = result["tree"]
        assert len(tree) >= 1
        node = tree[0]
        assert "id" in node
        assert "ref" in node


# ---------------------------------------------------------------------------
# make_managed_form_elem_json: детерминизм
# ---------------------------------------------------------------------------


class TestMakeManagedFormElemJsonDeterminism:
    def test_two_calls_produce_identical_json(self):
        pages = ["Страница1", "Страница2"]
        payload1 = make_managed_form_elem_json(pages=pages, with_noise=True)
        payload2 = make_managed_form_elem_json(pages=pages, with_noise=True)
        json1 = json.dumps(payload1, ensure_ascii=False, sort_keys=True)
        json2 = json.dumps(payload2, ensure_ascii=False, sort_keys=True)
        assert json1 == json2, "Два вызова с одинаковыми аргументами дали разный JSON"

    def test_different_pages_produce_different_json(self):
        payload1 = make_managed_form_elem_json(pages=["Страница1"])
        payload2 = make_managed_form_elem_json(pages=["ДругаяСтраница"])
        json1 = json.dumps(payload1, ensure_ascii=False, sort_keys=True)
        json2 = json.dumps(payload2, ensure_ascii=False, sort_keys=True)
        assert json1 != json2

    def test_custom_props_override_defaults(self):
        custom_props = [{"name": "МоёСвойство", "type": "String", "value": "X"}]
        result = make_managed_form_elem_json(props=custom_props)
        assert result["props"] == custom_props

    def test_custom_commands_override_defaults(self):
        custom_commands = [{"name": "МояКоманда", "type": "Command"}]
        result = make_managed_form_elem_json(commands=custom_commands)
        assert result["commands"] == custom_commands

    def test_custom_params_override_defaults(self):
        custom_params = [{"name": "МойПараметр", "type": "String"}]
        result = make_managed_form_elem_json(params=custom_params)
        assert result["params"] == custom_params


# ---------------------------------------------------------------------------
# write_managed_form_elem: раскладка на диск
# ---------------------------------------------------------------------------


class TestWriteManagedFormElem:
    def test_returns_path_to_elem_json(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "external_managed", "ВнешнийОтчет", "ФормаОтчетаУправляемая", payload
        )
        assert isinstance(result_path, Path)
        assert result_path.suffix == ".json"
        assert result_path.exists()

    def test_elem_json_name_matches_form_name(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "external_managed", "ВнешнийОтчет", "ФормаОтчетаУправляемая", payload
        )
        assert result_path.name == "ФормаОтчетаУправляемая.elem.json"

    def test_elem_json_is_valid_json(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "external_managed", "ВнешнийОтчет", "ФормаОтчетаУправляемая", payload
        )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert "data" in data
        assert "tree" in data

    def test_report_form_suffix_layout(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "external_managed", "ВнешнийОтчетУправляемый", "ФормаОтчетаУправляемая", payload
        )
        parts = result_path.parts
        assert "ReportForm" in parts, f"Суффикс ReportForm не найден в {result_path}"

    def test_form_suffix_for_generic_form(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "external_managed", "ВнешняяОбработка", "ФормаВнешняяУправляемая", payload
        )
        parts = result_path.parts
        assert "Form" in parts, f"Суффикс Form не найден в {result_path}"

    def test_catalog_form_suffix_layout(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "Catalog", "Банки", "ФормаЭлементаУправляемая", payload
        )
        parts = result_path.parts
        assert "CatalogForm" in parts, f"Суффикс CatalogForm не найден в {result_path}"

    def test_aux_files_created_by_default(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "Catalog", "Банки", "ФормаЭлементаУправляемая", payload
        )
        form_dir = result_path.parent
        aux_json = form_dir / "ФормаЭлементаУправляемая.10.json"
        aux_bsl = form_dir / "ФормаЭлементаУправляемая.obj.10.bsl"
        assert aux_json.exists(), "*.10.json не создан"
        assert aux_bsl.exists(), "*.obj.10.bsl не создан"

    def test_aux_files_skipped_when_write_aux_false(self, tmp_path):
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "Catalog", "Банки", "ФормаЭлементаУправляемая", payload,
            write_aux=False
        )
        form_dir = result_path.parent
        aux_json = form_dir / "ФормаЭлементаУправляемая.10.json"
        aux_bsl = form_dir / "ФормаЭлементаУправляемая.obj.10.bsl"
        assert not aux_json.exists(), "*.10.json создан при write_aux=False"
        assert not aux_bsl.exists(), "*.obj.10.bsl создан при write_aux=False"

    def test_no_absolute_paths_in_layout(self, tmp_path):
        """Проверяет, что write_managed_form_elem не создаёт файлы с абсолютными
        путями за пределами tmp_path."""
        payload = make_managed_form_elem_json()
        result_path = write_managed_form_elem(
            tmp_path, "Catalog", "Банки", "ФормаЭлементаУправляемая", payload
        )
        assert result_path.is_relative_to(tmp_path), (
            f"Путь {result_path} не является дочерним относительно tmp_path"
        )

    def test_all_three_form_suffixes(self, tmp_path):
        """Smoke-тест трёх вариантов суффикса из layout эталона в issue."""
        cases = [
            ("external_managed", "ВнешнийОтчетУправляемый", "ФормаОтчетаУправляемая", "ReportForm"),
            ("external_managed", "ВнешняяОбработкаУпр", "ФормаВнешняяУправляемая", "Form"),
            ("Catalog", "Банки", "ФормаЭлементаУправляемая", "CatalogForm"),
        ]
        for obj_type, obj_name, form_name, expected_suffix in cases:
            payload = make_managed_form_elem_json()
            result_path = write_managed_form_elem(tmp_path, obj_type, obj_name, form_name, payload)
            assert expected_suffix in result_path.parts, (
                f"Ожидался суффикс '{expected_suffix}' для формы '{form_name}', "
                f"получен путь: {result_path}"
            )
