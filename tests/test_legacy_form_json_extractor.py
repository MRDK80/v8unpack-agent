"""TDD: issue #100 — извлечение элементов из большого *.json обычной формы.

Проблема: для форм-записей регистров сведений (обычная форма) elem.json
содержит пустой tree и пустую секцию data. Большой *.json (объект формы)
содержит реквизиты в виде узлов ["14", '"ИмяРеквизита"', ...] внутри
родительского вектора с известным UUID виджета (InputField, ComboBox).

Criteria of Done:
1. extract_legacy_form_elements(json_data) возвращает список dict с полями
   name, type, data_path для реквизитов InputField и ComboBox.
2. Надписи (Label UUID 0fc7e20d-...), CommandBar и прочие — не включаются.
3. data_path = "Объект.<Имя>" для каждого реквизита.
4. parse_elem_json использует новый источник, если elem.json пуст (tree/data пусты)
   и рядом есть большой *.json.
5. FormClass у таких форм — OBJECT (не UNKNOWN).
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Минимальные фикстуры — синтетические данные, воспроизводящие структуру
# реального InformationRegisterForm.json (АдресныйКлассификатор)
# ---------------------------------------------------------------------------

# UUID виджетов — константы из исследования probe4
_UUID_INPUT_FIELD = "381ed624-9217-4e63-85db-c4c3cb87daae"
_UUID_COMBO_BOX   = "64483e7f-3833-48e2-8c75-2c31aac49f6e"
_UUID_LABEL       = "0fc7e20d-f241-460c-bdf4-5ad88e5474a5"
_UUID_COMMAND_BAR = "e69bf21d-97b2-4f37-86db-675aea9ec2cb"


def _make_tag14_node(name: str) -> list:
    """Создаёт узел ["14", '"Имя"', "4294967295", "0", "0", "0"]."""
    return ["14", f'"{name}"', "4294967295", "0", "0", "0"]


def _make_element_block(widget_uuid: str, element_id: str, name: str) -> list:
    """Воспроизводит структуру блока элемента из InformationRegisterForm.json.

    Структура: [widget_uuid, element_id, <data block>, <positions block>,
                ["14", '"Имя"', ...], ["0"]]
    """
    data_block = ["9", ["\"Pattern\""], [], [], "0", "1", "0", ["1", "0"], "0"]
    positions = ["8", "98", "33", "318", "52", "1"]
    return [
        widget_uuid,
        element_id,
        data_block,
        positions,
        _make_tag14_node(name),
        ["0"],
    ]


MINIMAL_FORM_JSON = {
    "name": "ФормаЗаписи",
    "form": [
        [
            [
                "25",
                ["uuid-form", ["1", []]],
                [
                    "16",
                    _make_element_block(_UUID_INPUT_FIELD, "2",  "Код"),
                    _make_element_block(_UUID_INPUT_FIELD, "4",  "Наименование"),
                    _make_element_block(_UUID_INPUT_FIELD, "6",  "Сокращение"),
                    _make_element_block(_UUID_COMBO_BOX,   "8",  "ТипАдресногоЭлемента"),
                    _make_element_block(_UUID_INPUT_FIELD, "10", "Индекс"),
                    _make_element_block(_UUID_LABEL,       "23", "Надпись1"),
                    _make_element_block(_UUID_LABEL,       "24", "Надпись2"),
                    _make_element_block(_UUID_COMMAND_BAR, "21", "ДействияФормы"),
                    ["0"],
                ],
            ]
        ]
    ],
}


# ---------------------------------------------------------------------------
# 1. Unit-тест функции extract_legacy_form_elements
# ---------------------------------------------------------------------------

class TestExtractLegacyFormElements:
    """Тестирует новую функцию extract_legacy_form_elements."""

    def test_returns_data_elements_only(self):
        """Должны вернуться только InputField и ComboBox, без Label/CommandBar."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        result = extract_legacy_form_elements(MINIMAL_FORM_JSON)
        names = [e["name"] for e in result]

        assert "Код" in names
        assert "Наименование" in names
        assert "Сокращение" in names
        assert "ТипАдресногоЭлемента" in names
        assert "Индекс" in names

    def test_excludes_labels_and_commandbars(self):
        """Надписи и командные панели не должны попасть в результат."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        result = extract_legacy_form_elements(MINIMAL_FORM_JSON)
        names = [e["name"] for e in result]

        assert "Надпись1" not in names
        assert "Надпись2" not in names
        assert "ДействияФормы" not in names

    def test_data_path_format(self):
        """data_path каждого реквизита должен быть 'Объект.<Имя>'."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        result = extract_legacy_form_elements(MINIMAL_FORM_JSON)
        by_name = {e["name"]: e for e in result}

        assert by_name["Код"]["data_path"] == "Объект.Код"
        assert by_name["Наименование"]["data_path"] == "Объект.Наименование"
        assert by_name["ТипАдресногоЭлемента"]["data_path"] == "Объект.ТипАдресногоЭлемента"

    def test_element_type_field_present(self):
        """Каждый элемент должен иметь поле type."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        result = extract_legacy_form_elements(MINIMAL_FORM_JSON)
        for elem in result:
            assert "type" in elem, f"Нет поля type у элемента {elem!r}"

    def test_empty_form_returns_empty_list(self):
        """Форма без элементов — пустой список, без исключений."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        assert extract_legacy_form_elements({}) == []
        assert extract_legacy_form_elements({"form": []}) == []

    def test_uniqueness_no_duplicates(self):
        """Дублирующиеся имена в json не должны дублироваться в результате."""
        from v8unpack_agent.elem_parser import extract_legacy_form_elements

        dup_json = json.loads(json.dumps(MINIMAL_FORM_JSON))
        inner = dup_json["form"][0][0][2]
        inner.append(_make_element_block(_UUID_INPUT_FIELD, "99", "Код"))

        result = extract_legacy_form_elements(dup_json)
        names = [e["name"] for e in result]
        assert names.count("Код") == 1


# ---------------------------------------------------------------------------
# 2. Интеграционный тест: parse_elem_json использует новый источник
# ---------------------------------------------------------------------------

class TestParseElemJsonFallbackToLegacyFormJson:
    """parse_elem_json должен использовать большой *.json, когда elem.json пуст."""

    def _make_form_dir(self, tmp_path: Path) -> Path:
        form_dir = tmp_path / "InformationRegister" / "АдресныйКлассификатор" \
                   / "InformationRegisterForm" / "ФормаЗаписи"
        form_dir.mkdir(parents=True)
        (form_dir / "InformationRegisterForm.elem.json").write_text(
            json.dumps({"params": [], "props": [], "commands": [], "tree": [], "data": {}}),
            encoding="utf-8",
        )
        (form_dir / "InformationRegisterForm.json").write_text(
            json.dumps(MINIMAL_FORM_JSON, ensure_ascii=False),
            encoding="utf-8",
        )
        return form_dir

    def test_fallback_produces_elements(self, tmp_path):
        from v8unpack_agent.elem_parser import parse_elem_json

        form_dir = self._make_form_dir(tmp_path)
        result = parse_elem_json(form_dir)

        assert result.elem_index_ok is True, (
            f"elem_index_ok=False, warnings={result.warnings}"
        )
        names = [e["name"] for e in result.elements]
        assert "Код" in names
        assert "Наименование" in names
        assert "Надпись1" not in names

    def test_fallback_data_paths_are_object_prefixed(self, tmp_path):
        from v8unpack_agent.elem_parser import parse_elem_json

        form_dir = self._make_form_dir(tmp_path)
        result = parse_elem_json(form_dir)

        by_name = {e["name"]: e for e in result.elements}
        assert by_name["Код"]["data_path"] == "Объект.Код"

    def test_no_fallback_when_elem_json_has_elements(self, tmp_path):
        from v8unpack_agent.elem_parser import parse_elem_json

        form_dir = tmp_path / "InformationRegister" / "Тест" / "InformationRegisterForm" / "ФормаЗаписи"
        form_dir.mkdir(parents=True)

        elem_data = {
            "params": [], "props": [], "commands": [],
            "tree": [{"name": "ПолеИзElemJson", "type": "Field"}],
            "data": {"ПолеИзElemJson": {"id": 1, "type": "Field"}},
        }
        (form_dir / "InformationRegisterForm.elem.json").write_text(
            json.dumps(elem_data), encoding="utf-8"
        )
        (form_dir / "InformationRegisterForm.json").write_text(
            json.dumps(MINIMAL_FORM_JSON, ensure_ascii=False), encoding="utf-8"
        )

        result = parse_elem_json(form_dir)
        names = [e["name"] for e in result.elements]
        assert "ПолеИзElemJson" in names
        assert "Надпись1" not in names


# ---------------------------------------------------------------------------
# 3. Тест form_classifier: OBJECT для форм с legacy-элементами
# ---------------------------------------------------------------------------

class TestFormClassifierWithLegacyElements:
    """FormClass для ФормаЗаписи с legacy-элементами должен быть OBJECT."""

    def test_form_with_object_data_paths_classified_as_object(self):
        from v8unpack_agent.form_classifier import FormClass, classify_form_by_bindings

        # type быть в DATA_ELEMENT_TYPES (из coverage_metric) — иначе classify_form_by_bindings
        # игнорирует элемент и возвращает SERVICE
        elements = [
            {"name": "Код",          "type": "InputField", "data_path": "Объект.Код"},
            {"name": "Наименование", "type": "InputField", "data_path": "Объект.Наименование"},
            {"name": "Индекс",       "type": "InputField", "data_path": "Объект.Индекс"},
        ]
        # classify_form_by_bindings(elements) → FormClass (без распаковки на кортеж)
        form_class = classify_form_by_bindings(elements)
        assert form_class == FormClass.OBJECT, (
            f"Ожидали OBJECT, получили {form_class!r}"
        )

    def test_classify_empty_tree_form_returns_unknown_not_service(self):
        """classify_empty_tree_form для 'ФормаЗаписи' → UNKNOWN, reason='platform_object_name_unparsed'."""
        from v8unpack_agent.form_classifier import FormClass, classify_empty_tree_form

        form_class, reason = classify_empty_tree_form("ФормаЗаписи")
        assert form_class == FormClass.UNKNOWN
        assert reason == "platform_object_name_unparsed"
