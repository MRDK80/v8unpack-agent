"""Синтетические фикстуры управляемой формы (Form.xml) для конвейера №2c.

Использование в тестах (issue #52):

    from tests._managed_fixtures import make_managed_form_xml, write_managed_form

    xml = make_managed_form_xml(
        attributes=[{"name": "Организация", "type": "CatalogRef.Organizations"}],
        commands=[{"name": "Записать", "action": "Write"}],
        elements=[{"name": "ПолеОрганизация", "type": "InputField", "data_path": "Организация"}],
        events=[{"name": "ПриСозданииНаСервере", "handler": "ПриСозданииНаСервереНаСервере"}],
        relations=[{"attribute": "Организация", "element": "ПолеОрганизация"}],
    )
    form_dir = write_managed_form(tmp_path, "Catalog", "Товары", "ФормаЭлемента", xml)

Автономность:
- Никакой зависимости от production-модулей ``v8unpack_agent/``.
- Стандартная библиотека Python: xml.etree.ElementTree, hashlib, pathlib.

XML-структура:
    <Form xmlns="http://v8.1c.ru/8.2/managed-application/core"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          id="{GUID}">
      <Attributes>  … <Attribute name="…" id="…"/> …  </Attributes>
      <Commands>    … <Command  name="…" id="…"/> …  </Commands>
      <Elements>    … <Element  name="…" id="…"/> …  </Elements>
      <Events>      … <Event    name="…" id="…"/> …  </Events>
      <Relations>   … <Relation …/>              …  </Relations>
    </Form>

OS-нейтральность: пути строятся через :mod:`pathlib`.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Пространства имён (шум — для проверки их наличия в тестах)
# ---------------------------------------------------------------------------
_NS_CORE = "http://v8.1c.ru/8.2/managed-application/core"
_NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Оформительский шум, добавляемый к каждому узлу при with_noise=True.
# Ключи намеренно «косметические», чтобы проверить их присутствие в тестах.
_NOISE_ATTRS: dict[str, str] = {
    "left": "10",
    "top": "10",
    "width": "200",
    "height": "24",
    "color": "#000000",
    "font": "Arial",
}


def _make_guid(seed: str) -> str:
    """Детерминированный GUID-подобный идентификатор на основе SHA-1 seed.

    Формат: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (8-4-4-4-12).
    Детерминизм гарантирует стабильность хэшей (issue #58).
    """
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()  # noqa: S324 — не крипто
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _append_section(
    parent: ET.Element,
    tag: str,
    items: list[dict[str, Any]],
    *,
    with_noise: bool,
    item_tag: str,
) -> ET.Element:
    """Добавить секцию <tag><item_tag …/> … </tag> к parent.

    Порядок дочерних узлов детерминирован: items обрабатываются в том
    порядке, в котором переданы. Атрибуты в каждом узле сортируются по ключу
    (xml.etree выводит их в порядке вставки, но мы используем sorted() для
    явного детерминизма).
    """
    section = ET.SubElement(parent, tag)
    for i, item in enumerate(items):
        name = item.get("name", f"Item{i}")
        child = ET.SubElement(section, item_tag)
        # Сначала — атрибуты из items (детерминированный порядок по ключу).
        for k in sorted(item.keys()):
            child.set(k, str(item[k]))
        # id и ref — «шум» идентификаторов; добавляются безусловно.
        child.set("id", _make_guid(f"{tag}:{name}:id"))
        child.set("ref", _make_guid(f"{tag}:{name}:ref"))
        # Оформительский шум — только при with_noise=True.
        if with_noise:
            for attr, val in sorted(_NOISE_ATTRS.items()):
                child.set(attr, val)
    return section


def make_managed_form_xml(
    attributes: list[dict[str, Any]] | None = None,
    commands: list[dict[str, Any]] | None = None,
    elements: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    *,
    with_noise: bool = True,
) -> str:
    """Собрать синтетический XML управляемой формы и вернуть строку UTF-8.

    Parameters
    ----------
    attributes:
        Список реквизитов формы. Каждый — dict с ключами ``name``, ``type``
        и любыми другими (станут атрибутами XML-узла).
    commands:
        Список команд формы. Поддерживаемые ключи: ``name``, ``action``.
    elements:
        Список элементов формы. Ключи: ``name``, ``type``, ``data_path``.
    events:
        Список событий формы. Ключи: ``name``, ``handler``.
    relations:
        Список связей. Ключи: ``attribute``, ``element``.
    with_noise:
        Если ``True`` (по умолчанию), к каждому дочернему узлу добавляются
        косметические атрибуты (координаты, цвет, шрифт) и объявления
        пространств имён на корневом элементе. Используется для проверки
        их отсечения в последующих шагах конвейера (#54, #57, #58).

    Returns
    -------
    str
        Текст XML-документа с XML-декларацией ``<?xml version='1.0'
        encoding='utf-8'?>``.

    Детерминизм:
        Одинаковые входные параметры гарантируют побайтово одинаковый вывод:
        порядок секций фиксирован, атрибуты сортируются по ключу,
        GUID-идентификаторы строятся как SHA-1 от seed-строки.
    """
    attributes = attributes or []
    commands = commands or []
    elements = elements or []
    events = events or []
    relations = relations or []

    # Регистрируем namespace для чистого вывода (без ns0: префиксов).
    ET.register_namespace("", _NS_CORE)
    ET.register_namespace("xsi", _NS_XSI)

    root = ET.Element(f"{{{_NS_CORE}}}Form")
    # Корневой id — детерминированный GUID.
    root.set("id", _make_guid("Form:root:id"))
    if with_noise:
        # Явное объявление xsi-namespace в корне (шум для проверки).
        root.set(f"xmlns:xsi", _NS_XSI)  # noqa: F541 — намеренно строка
        root.set("xsi:noNamespaceSchemaLocation", "")

    _append_section(root, "Attributes", attributes, with_noise=with_noise, item_tag="Attribute")
    _append_section(root, "Commands", commands, with_noise=with_noise, item_tag="Command")
    _append_section(root, "Elements", elements, with_noise=with_noise, item_tag="Element")
    _append_section(root, "Events", events, with_noise=with_noise, item_tag="Event")
    _append_section(root, "Relations", relations, with_noise=with_noise, item_tag="Relation")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")

    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8")


def write_managed_form(
    root: Path,
    object_type: str,
    object_name: str,
    form_name: str,
    xml: str,
) -> Path:
    """Разложить Form.xml на диск в ожидаемое layout-дерево.

    Layout для объектов конфигурации (ожидается issue #55):

        <root>/<object_type>/<object_name>/<object_type>Form/<form_name>/Form.xml

    Все промежуточные директории создаются автоматически.

    Parameters
    ----------
    root:
        Корень временного дерева (обычно ``tmp_path`` из pytest).
    object_type:
        Тип метаобъекта: ``"Catalog"``, ``"Document"`` и т.п.
    object_name:
        Имя объекта: ``"Товары"``, ``"ПриходТоваров"`` и т.п.
    form_name:
        Имя формы: ``"ФормаЭлемента"``, ``"ФормаСписка"`` и т.п.
    xml:
        Текст XML-документа, возвращённый :func:`make_managed_form_xml`.

    Returns
    -------
    Path
        Путь к директории формы (содержит ``Form.xml``).
    """
    form_dir = root / object_type / object_name / f"{object_type}Form" / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    form_xml_path = form_dir / "Form.xml"
    form_xml_path.write_text(xml, encoding="utf-8")
    return form_dir
