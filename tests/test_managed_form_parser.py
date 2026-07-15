"""Тесты парсера управляемой формы (issue #53).

Все тесты синтетические: XML порождается из tests/_managed_fixtures.py
(issue #52) или вручную строковыми литералами — ни одно обращение к
реальной файловой системе не требуется.

Покрываемые сценарии (acceptance criteria issue #53):
- Вызов со str, bytes, io.StringIO, io.BytesIO -> идентичный результат.
- Форма с namespace-префиксами разбирается без ошибок.
- Битый XML -> ok=False, непустые warnings, без исключения.
- Пустая форма -> ok=True, пустые секции.
- Форма с реквизитами / командами / элементами / событиями / связями.
- В модуле нет импорта ФС: тест парсит строку без создания файлов.
"""
from __future__ import annotations

import io

import pytest

from tests._managed_fixtures import make_managed_form_xml
from v8unpack_agent.managed_form_parser import ParsedManagedForm, parse_managed_form


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture()
def simple_form_xml() -> str:
    """Синтетический Form.xml с одним реквизитом, одной командой,
    одним элементом, одним событием и одной связью."""
    return make_managed_form_xml(
        attributes=[{"name": "Организация", "type": "CatalogRef.Organizations"}],
        commands=[{"name": "Записать", "action": "Write"}],
        elements=[{"name": "ПолеОрганизация", "type": "InputField", "data_path": "Организация"}],
        events=[{"name": "ПриСозданииНаСервере", "handler": "ПриСозданииНаСервереНаСервере"}],
        relations=[{"attribute": "Организация", "element": "ПолеОрганизация"}],
    )


@pytest.fixture()
def empty_form_xml() -> str:
    """Form.xml без реквизитов, команд, элементов, событий и связей."""
    return make_managed_form_xml(
        attributes=[],
        commands=[],
        elements=[],
        events=[],
        relations=[],
        with_noise=False,
    )


@pytest.fixture()
def noisy_form_xml() -> str:
    """Form.xml с namespace-шумом и оформительскими атрибутами (with_noise=True)."""
    return make_managed_form_xml(
        attributes=[
            {"name": "Сумма", "type": "Number"},
            {"name": "Контрагент", "type": "CatalogRef.Contractors"},
        ],
        commands=[{"name": "Провести", "action": "Post"}],
        elements=[
            {"name": "ПолеСумма", "type": "InputField"},
            {"name": "ГруппаШапка", "type": "Group"},
        ],
        events=[
            {"name": "ПриЗаписи", "handler": "ПриЗаписиНаСервере"},
            {"name": "ПередЗаписью", "handler": "ПередЗаписьюНаСервере"},
        ],
        relations=[
            {"attribute": "Сумма", "element": "ПолеСумма"},
        ],
        with_noise=True,
    )


# ---------------------------------------------------------------------------
# Тест: нет импорта ФС в модуле managed_form_parser
# ---------------------------------------------------------------------------

def test_no_filesystem_import_in_module():
    """Модуль managed_form_parser не импортирует pathlib, os, os.path.

    Это гарантирует, что парсер не обращается к файловой системе напрямую.
    """
    import importlib
    import importlib.util
    import sys

    # Получаем спецификацию модуля без его исполнения
    spec = importlib.util.find_spec("v8unpack_agent.managed_form_parser")
    assert spec is not None, "managed_form_parser не найден"

    # Читаем исходный код модуля
    source_path = spec.origin
    assert source_path is not None
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()

    forbidden = ["import pathlib", "from pathlib", "import os", "from os"]
    found = [f for f in forbidden if f in source]
    assert not found, (
        f"managed_form_parser содержит запрещённые импорты ФС: {found}"
    )


# ---------------------------------------------------------------------------
# Тест: идентичность результата для str / bytes / StringIO / BytesIO
# ---------------------------------------------------------------------------

class TestSourceEquivalence:
    """str / bytes / io.StringIO / io.BytesIO дают одинаковый ParsedManagedForm."""

    def test_str_and_bytes_identical(self, simple_form_xml: str):
        result_str = parse_managed_form(simple_form_xml)
        result_bytes = parse_managed_form(simple_form_xml.encode("utf-8"))
        assert result_str.ok
        assert result_bytes.ok
        assert result_str.attributes == result_bytes.attributes
        assert result_str.commands == result_bytes.commands
        assert result_str.elements == result_bytes.elements
        assert result_str.events == result_bytes.events
        assert result_str.relations == result_bytes.relations

    def test_str_and_stringio_identical(self, simple_form_xml: str):
        result_str = parse_managed_form(simple_form_xml)
        result_sio = parse_managed_form(io.StringIO(simple_form_xml))
        assert result_sio.ok
        assert result_str.attributes == result_sio.attributes
        assert result_str.commands == result_sio.commands
        assert result_str.elements == result_sio.elements

    def test_str_and_bytesio_identical(self, simple_form_xml: str):
        result_str = parse_managed_form(simple_form_xml)
        result_bio = parse_managed_form(io.BytesIO(simple_form_xml.encode("utf-8")))
        assert result_bio.ok
        assert result_str.attributes == result_bio.attributes
        assert result_str.commands == result_bio.commands
        assert result_str.elements == result_bio.elements

    def test_all_four_sources_identical(self, simple_form_xml: str):
        """Все четыре источника дают побайтово одинаковые данные."""
        xml_bytes = simple_form_xml.encode("utf-8")
        r_str = parse_managed_form(simple_form_xml)
        r_bytes = parse_managed_form(xml_bytes)
        r_sio = parse_managed_form(io.StringIO(simple_form_xml))
        r_bio = parse_managed_form(io.BytesIO(xml_bytes))

        for r in (r_bytes, r_sio, r_bio):
            assert r.ok
            assert r.attributes == r_str.attributes
            assert r.commands == r_str.commands
            assert r.elements == r_str.elements
            assert r.events == r_str.events
            assert r.relations == r_str.relations


# ---------------------------------------------------------------------------
# Тест: пустая форма
# ---------------------------------------------------------------------------

class TestEmptyForm:
    def test_ok_true(self, empty_form_xml: str):
        result = parse_managed_form(empty_form_xml)
        assert result.ok

    def test_all_sections_empty(self, empty_form_xml: str):
        result = parse_managed_form(empty_form_xml)
        assert result.attributes == []
        assert result.commands == []
        assert result.elements == []
        assert result.events == []
        assert result.relations == []

    def test_warnings_empty_for_valid_xml(self, empty_form_xml: str):
        result = parse_managed_form(empty_form_xml)
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Тест: форма с namespace-префиксами (шум из _managed_fixtures)
# ---------------------------------------------------------------------------

class TestNamespacePrefixes:
    def test_parses_without_error(self, noisy_form_xml: str):
        """Форма с namespace-объявлениями и шумовыми атрибутами разбирается без исключения."""
        result = parse_managed_form(noisy_form_xml)
        assert result.ok

    def test_attributes_count(self, noisy_form_xml: str):
        result = parse_managed_form(noisy_form_xml)
        assert len(result.attributes) == 2

    def test_commands_count(self, noisy_form_xml: str):
        result = parse_managed_form(noisy_form_xml)
        assert len(result.commands) == 1

    def test_elements_count(self, noisy_form_xml: str):
        result = parse_managed_form(noisy_form_xml)
        assert len(result.elements) == 2

    def test_events_count(self, noisy_form_xml: str):
        result = parse_managed_form(noisy_form_xml)
        assert len(result.events) == 2

    def test_relations_count(self, noisy_form_xml: str):
        result = parse_managed_form(noisy_form_xml)
        assert len(result.relations) == 1

    def test_attribute_name_extracted(self, noisy_form_xml: str):
        """Имена реквизитов доступны в сырых атрибутах."""
        result = parse_managed_form(noisy_form_xml)
        names = [a.get("name") for a in result.attributes]
        assert "Сумма" in names
        assert "Контрагент" in names

    def test_noise_attrs_present_in_raw(self, noisy_form_xml: str):
        """Шумовые атрибуты (left, top, width…) присутствуют в сырых данных.
        Отсечение шума — задача issue #54, парсер их сохраняет."""
        result = parse_managed_form(noisy_form_xml)
        assert result.attributes, "ожидаются реквизиты"
        first_attr = result.attributes[0]
        # Шумовые атрибуты должны быть в сырых данных
        assert "width" in first_attr or "left" in first_attr, (
            f"ожидаются шумовые атрибуты, получено: {list(first_attr.keys())}"
        )


# ---------------------------------------------------------------------------
# Тест: явный namespace-префикс (не через make_managed_form_xml)
# ---------------------------------------------------------------------------

class TestExplicitNamespacePrefix:
    """Форма с явным namespace-префиксом в тегах (не дефолтный namespace)."""

    PREFIXED_XML = """<?xml version='1.0' encoding='utf-8'?>
<mf:Form xmlns:mf="http://v8.1c.ru/8.2/managed-application/core"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         id="{12345678-1234-1234-1234-123456789012}">
  <mf:Attributes>
    <mf:Attribute name="Товар" type="CatalogRef.Products" id="{aaa}"/>
  </mf:Attributes>
  <mf:Commands>
    <mf:Command name="ОК" action="OK" id="{bbb}"/>
  </mf:Commands>
  <mf:Elements>
    <mf:Element name="ПолеТовар" type="InputField" id="{ccc}"/>
  </mf:Elements>
  <mf:Events>
    <mf:Event name="ПриОткрытии" handler="ПриОткрытииНаСервере" id="{ddd}"/>
  </mf:Events>
  <mf:Relations>
    <mf:Relation attribute="Товар" element="ПолеТовар"/>
  </mf:Relations>
</mf:Form>
"""

    def test_parses_without_error(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert result.ok, f"warnings: {result.warnings}"

    def test_attributes_extracted(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert len(result.attributes) == 1
        assert result.attributes[0]["name"] == "Товар"

    def test_commands_extracted(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert len(result.commands) == 1
        assert result.commands[0]["name"] == "ОК"

    def test_elements_extracted(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert len(result.elements) == 1
        assert result.elements[0]["name"] == "ПолеТовар"

    def test_events_extracted(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert len(result.events) == 1
        assert result.events[0]["name"] == "ПриОткрытии"

    def test_relations_extracted(self):
        result = parse_managed_form(self.PREFIXED_XML)
        assert len(result.relations) == 1
        assert result.relations[0]["attribute"] == "Товар"


# ---------------------------------------------------------------------------
# Тест: битый XML
# ---------------------------------------------------------------------------

class TestBrokenXml:
    def test_broken_xml_returns_ok_false(self):
        result = parse_managed_form("<Form><Unclosed")
        assert not result.ok

    def test_broken_xml_has_warnings(self):
        result = parse_managed_form("<Form><Unclosed")
        assert result.warnings, "warnings должны быть непустыми при ошибке"

    def test_broken_xml_no_exception(self):
        """Битый XML не бросает исключение наружу."""
        try:
            result = parse_managed_form("NOT XML AT ALL <<<>>>")
            assert not result.ok
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"parse_managed_form бросил исключение: {exc}")

    def test_empty_string_returns_ok_false(self):
        result = parse_managed_form("")
        assert not result.ok
        assert result.warnings

    def test_empty_bytes_returns_ok_false(self):
        result = parse_managed_form(b"")
        assert not result.ok
        assert result.warnings

    def test_warnings_contain_parse_error_hint(self):
        result = parse_managed_form("<broken>")
        assert any("parse" in w.lower() or "xml" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Тест: полная форма с простым XML (без namespace-шума)
# ---------------------------------------------------------------------------

class TestSimpleForm:
    def test_ok_true(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert result.ok

    def test_one_attribute(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert len(result.attributes) == 1
        assert result.attributes[0]["name"] == "Организация"

    def test_one_command(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert len(result.commands) == 1
        assert result.commands[0]["name"] == "Записать"

    def test_one_element(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert len(result.elements) == 1
        assert result.elements[0]["name"] == "ПолеОрганизация"

    def test_one_event(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert len(result.events) == 1
        assert result.events[0]["name"] == "ПриСозданииНаСервере"

    def test_one_relation(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert len(result.relations) == 1
        assert result.relations[0]["attribute"] == "Организация"
        assert result.relations[0]["element"] == "ПолеОрганизация"

    def test_result_type(self, simple_form_xml: str):
        result = parse_managed_form(simple_form_xml)
        assert isinstance(result, ParsedManagedForm)


# ---------------------------------------------------------------------------
# Тест: ParsedManagedForm как dataclass
# ---------------------------------------------------------------------------

class TestParsedManagedFormDataclass:
    def test_default_ok_true(self):
        r = ParsedManagedForm()
        assert r.ok is True

    def test_default_lists_empty(self):
        r = ParsedManagedForm()
        assert r.attributes == []
        assert r.commands == []
        assert r.elements == []
        assert r.events == []
        assert r.relations == []
        assert r.warnings == []

    def test_ok_false_constructable(self):
        r = ParsedManagedForm(ok=False, warnings=["test error"])
        assert not r.ok
        assert r.warnings == ["test error"]
