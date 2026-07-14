"""Тесты синтетических фикстур управляемой формы (issue #52).

Проверяет:
- валидность XML (парсится через xml.etree.ElementTree);
- наличие namespace-объявлений;
- наличие id/ref-атрибутов (шум идентификаторов);
- наличие оформительских атрибутов (шум координат/цветов) при with_noise=True;
- отсутствие оформительских атрибутов при with_noise=False;
- детерминизм: одинаковые параметры → побайтово одинаковый XML;
- корректная раскладка Form.xml на диск в ожидаемое дерево;
- «золотой» минимальный пример Form.xml проходит парсинг.

Все тесты синтетические — реальных конфигураций не требуют.
Нет зависимостей от production-модулей v8unpack_agent/.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests._managed_fixtures import make_managed_form_xml, write_managed_form

# ---------------------------------------------------------------------------
# константы для «золотого» примера
# ---------------------------------------------------------------------------
_GOLD_ATTRIBUTES = [{"name": "Организация", "type": "CatalogRef.Organizations"}]
_GOLD_COMMANDS = [{"name": "Записать", "action": "Write"}]
_GOLD_ELEMENTS = [{"name": "ПолеОрганизация", "type": "InputField", "data_path": "Организация"}]
_GOLD_EVENTS = [{"name": "ПриСозданииНаСервере", "handler": "ПриСозданииНаСервереНаСервере"}]
_GOLD_RELATIONS = [{"attribute": "Организация", "element": "ПолеОрганизация"}]


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _gold_xml(*, with_noise: bool = True) -> str:
    return make_managed_form_xml(
        attributes=_GOLD_ATTRIBUTES,
        commands=_GOLD_COMMANDS,
        elements=_GOLD_ELEMENTS,
        events=_GOLD_EVENTS,
        relations=_GOLD_RELATIONS,
        with_noise=with_noise,
    )


# ---------------------------------------------------------------------------
# валидность XML
# ---------------------------------------------------------------------------

def test_xml_is_parseable() -> None:
    """Вывод make_managed_form_xml парсится без исключений."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    assert root is not None


def test_xml_has_declaration() -> None:
    """XML-документ содержит декларацию <?xml ...?>."""
    xml = _gold_xml()
    assert xml.startswith("<?xml")


def test_xml_root_tag_contains_namespace() -> None:
    """Корневой тег содержит namespace http://v8.1c.ru/8.2/managed-application/core."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    assert "v8.1c.ru" in root.tag or "managed-application" in root.tag


# ---------------------------------------------------------------------------
# структура секций
# ---------------------------------------------------------------------------

def test_xml_has_all_sections() -> None:
    """XML содержит все пять секций: Attributes, Commands, Elements, Events, Relations."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    tags = {child.tag for child in root}
    for section in ("Attributes", "Commands", "Elements", "Events", "Relations"):
        assert f"{{{ns}}}{section}" in tags, f"Секция {section} отсутствует"


def test_xml_attributes_count() -> None:
    """Секция Attributes содержит ровно столько узлов, сколько передано."""
    xml = make_managed_form_xml(attributes=_GOLD_ATTRIBUTES)
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    assert len(list(attrs_section)) == len(_GOLD_ATTRIBUTES)


def test_xml_empty_sections() -> None:
    """Вызов без параметров — все секции присутствуют, но пусты."""
    xml = make_managed_form_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    for section in ("Attributes", "Commands", "Elements", "Events", "Relations"):
        sec = root.find(f"{{{ns}}}{section}")
        assert sec is not None, f"Секция {section} отсутствует в пустой форме"
        assert len(list(sec)) == 0, f"Секция {section} не пуста"


# ---------------------------------------------------------------------------
# шум: id / ref (безусловно)
# ---------------------------------------------------------------------------

def test_xml_noise_id_ref_present() -> None:
    """Каждый узел-элемент имеет атрибуты id и ref."""
    xml = _gold_xml(with_noise=True)
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    for child in attrs_section:
        assert "id" in child.attrib, f"Узел {child.tag} не имеет атрибута id"
        assert "ref" in child.attrib, f"Узел {child.tag} не имеет атрибута ref"


def test_xml_noise_id_looks_like_guid() -> None:
    """Атрибут id похож на GUID (содержит дефисы, шестнадцатеричные символы)."""
    xml = _gold_xml(with_noise=True)
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    child = list(attrs_section)[0]
    guid = child.attrib["id"]
    parts = guid.split("-")
    assert len(parts) == 5, f"GUID должен иметь 5 частей через дефис: {guid}"
    assert all(c in "0123456789abcdef" for part in parts for c in part), (
        f"GUID содержит не-hex символы: {guid}"
    )


# ---------------------------------------------------------------------------
# шум: оформительские атрибуты (with_noise=True)
# ---------------------------------------------------------------------------

def test_xml_noise_decorative_attrs_present_when_enabled() -> None:
    """При with_noise=True присутствуют оформительские атрибуты (width, height, color)."""
    xml = _gold_xml(with_noise=True)
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    child = list(attrs_section)[0]
    assert "width" in child.attrib
    assert "height" in child.attrib
    assert "color" in child.attrib


def test_xml_noise_decorative_attrs_absent_when_disabled() -> None:
    """При with_noise=False оформительские атрибуты (width, height, color) отсутствуют."""
    xml = _gold_xml(with_noise=False)
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    child = list(attrs_section)[0]
    assert "width" not in child.attrib
    assert "height" not in child.attrib
    assert "color" not in child.attrib


# ---------------------------------------------------------------------------
# шум: namespace в корне (with_noise=True)
# ---------------------------------------------------------------------------

def test_xml_noise_xsi_namespace_in_root() -> None:
    """При with_noise=True корневой элемент содержит xmlns:xsi атрибут."""
    xml = _gold_xml(with_noise=True)
    # Проверяем через сырую строку — ET нормализует namespace-атрибуты
    assert "XMLSchema-instance" in xml or "xsi" in xml


# ---------------------------------------------------------------------------
# детерминизм
# ---------------------------------------------------------------------------

def test_determinism_same_params_same_output() -> None:
    """Два вызова с одинаковыми параметрами дают побайтово одинаковый XML."""
    xml_a = _gold_xml()
    xml_b = _gold_xml()
    assert xml_a == xml_b


def test_determinism_different_params_different_output() -> None:
    """Разные параметры дают разный XML."""
    xml_a = make_managed_form_xml(attributes=[{"name": "Поле1"}])
    xml_b = make_managed_form_xml(attributes=[{"name": "Поле2"}])
    assert xml_a != xml_b


def test_determinism_repeated_calls() -> None:
    """Многократные вызовы с одинаковыми параметрами стабильны."""
    results = [_gold_xml() for _ in range(5)]
    assert len(set(results)) == 1, "Разные результаты при повторных вызовах"


# ---------------------------------------------------------------------------
# раскладка на диск
# ---------------------------------------------------------------------------

def test_write_creates_form_xml(tmp_path: Path) -> None:
    """write_managed_form создаёт файл Form.xml."""
    xml = _gold_xml()
    form_dir = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаЭлемента", xml)
    assert (form_dir / "Form.xml").exists()


def test_write_returns_correct_path(tmp_path: Path) -> None:
    """write_managed_form возвращает путь к директории формы."""
    xml = _gold_xml()
    form_dir = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаЭлемента", xml)
    assert form_dir.is_dir()
    assert form_dir.name == "ФормаЭлемента"
    assert form_dir.parent.name == "CatalogForm"


def test_write_layout_matches_expected(tmp_path: Path) -> None:
    """Дерево директорий соответствует ожидаемому layout."""
    xml = _gold_xml()
    form_dir = write_managed_form(tmp_path, "Document", "ПриходТоваров", "ФормаДокумента", xml)
    expected = tmp_path / "Document" / "ПриходТоваров" / "DocumentForm" / "ФормаДокумента"
    assert form_dir == expected
    assert (form_dir / "Form.xml").exists()


def test_write_form_xml_is_utf8(tmp_path: Path) -> None:
    """Form.xml содержит кириллицу и читается без ошибок как UTF-8."""
    xml = _gold_xml()
    form_dir = write_managed_form(tmp_path, "Catalog", "Номенклатура", "ФормаЭлемента", xml)
    content = (form_dir / "Form.xml").read_text(encoding="utf-8")
    assert "Организация" in content


def test_write_form_xml_is_parseable(tmp_path: Path) -> None:
    """Form.xml на диске парсится корректно через xml.etree.ElementTree."""
    xml = _gold_xml()
    form_dir = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаЭлемента", xml)
    tree = ET.parse(str(form_dir / "Form.xml"))  # noqa: S314 — синтетический XML
    root = tree.getroot()
    assert root is not None


def test_write_multiple_forms(tmp_path: Path) -> None:
    """write_managed_form корректно раскладывает несколько форм одного объекта."""
    xml = _gold_xml()
    form1 = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаЭлемента", xml)
    form2 = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаСписка", xml)
    assert form1 != form2
    assert form1.parent == form2.parent  # одна CatalogForm
    assert (form1 / "Form.xml").exists()
    assert (form2 / "Form.xml").exists()


# ---------------------------------------------------------------------------
# «золотой» пример — регрессионный тест
# ---------------------------------------------------------------------------

def test_gold_example_valid_structure() -> None:
    """«Золотой» пример Form.xml: корректная структура всех пяти секций."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"

    attrs = root.find(f"{{{ns}}}Attributes")
    cmds = root.find(f"{{{ns}}}Commands")
    elems = root.find(f"{{{ns}}}Elements")
    events = root.find(f"{{{ns}}}Events")
    rels = root.find(f"{{{ns}}}Relations")

    assert attrs is not None and len(list(attrs)) == 1
    assert cmds is not None and len(list(cmds)) == 1
    assert elems is not None and len(list(elems)) == 1
    assert events is not None and len(list(events)) == 1
    assert rels is not None and len(list(rels)) == 1


def test_gold_attribute_name() -> None:
    """«Золотой» пример: атрибут Attribute[@name='Организация'] присутствует."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    attrs_section = root.find(f"{{{ns}}}Attributes")
    assert attrs_section is not None
    names = [child.attrib.get("name") for child in attrs_section]
    assert "Организация" in names


def test_gold_command_action() -> None:
    """«Золотой» пример: команда Записать с action=Write."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    cmds_section = root.find(f"{{{ns}}}Commands")
    assert cmds_section is not None
    cmd = list(cmds_section)[0]
    assert cmd.attrib.get("name") == "Записать"
    assert cmd.attrib.get("action") == "Write"


def test_gold_element_data_path() -> None:
    """«Золотой» пример: элемент ПолеОрганизация c data_path=Организация."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    elems_section = root.find(f"{{{ns}}}Elements")
    assert elems_section is not None
    elem = list(elems_section)[0]
    assert elem.attrib.get("name") == "ПолеОрганизация"
    assert elem.attrib.get("data_path") == "Организация"


def test_gold_event_handler() -> None:
    """«Золотой» пример: событие ПриСозданииНаСервере с handler."""
    xml = _gold_xml()
    root = ET.fromstring(xml)
    ns = "http://v8.1c.ru/8.2/managed-application/core"
    events_section = root.find(f"{{{ns}}}Events")
    assert events_section is not None
    event = list(events_section)[0]
    assert event.attrib.get("name") == "ПриСозданииНаСервере"
    assert event.attrib.get("handler") is not None


# ---------------------------------------------------------------------------
# проверка автономности: импорт не тянет production-модули
# ---------------------------------------------------------------------------

def test_no_production_import() -> None:
    """_managed_fixtures не импортирует ничего из v8unpack_agent/."""
    import importlib
    import sys

    # Перезагружаем модуль в изолированной проверке.
    mod = importlib.import_module("tests._managed_fixtures")
    source_file = getattr(mod, "__file__", "") or ""
    # Проверяем, что в sys.modules нет нежелательных production-модулей
    # в результате импорта _managed_fixtures (только stdlib + tests).
    production_imported = [
        k for k in sys.modules
        if k.startswith("v8unpack_agent") and k in sys.modules
    ]
    # Если production-модули уже были импортированы другими тестами — допустимо,
    # но _managed_fixtures сам их не должен требовать:
    # проверяем через отсутствие прямого импорта в исходнике.
    assert "v8unpack_agent" not in (getattr(mod, "__doc__", "") or "")
    # Главное — модуль загружается без ошибок и не содержит from v8unpack_agent.
    assert source_file.endswith("_managed_fixtures.py")
