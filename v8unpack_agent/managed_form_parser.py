"""managed_form_parser — чистый парсер Form.xml управляемой формы.

Реализует issue #53.

Архитектурный принцип: **ни одного обращения к файловой системе**. Парсер
принимает XML-содержимое или поток (``str | bytes | IO``) и возвращает
типизированную структуру :class:`ParsedManagedForm`. Обнаружение файлов в
выгрузке — задача отдельного модуля (issue #55).

Design notes
------------
- ``xml.etree.ElementTree`` — без внешних зависимостей (см. pyproject.toml).
- Namespace нормализуется через ``tag.split('}')[-1]``: URI платформы
  (``http://v8.1c.ru/8.2/managed-application/core`` и любые другие) не
  хардкодятся. Парсер устойчив к любым namespace-префиксам.
- Best-effort: непарсящийся или пустой XML возвращает ``ok=False`` +
  непустые ``warnings`` без исключения наружу (дисциплина ``SkdResult``
  и ``ElemIndexResult``).
- Приём ``str`` / ``bytes`` / файлоподобных объектов; UTF-8; BOM
  игнорируется (``ET.fromstring`` / ``ET.parse`` сами обрабатывают BOM).
- OS-нейтральность: нет импортов ``pathlib``, ``os``, ``os.path``.

Иерархия элементов:
    Вложенность элементов формы передаётся через атрибуты XML (``parent``
    / ``parentName`` / ``group`` и т.п.). Модуль сохраняет плоский список
    :attr:`ParsedManagedForm.elements` с сырыми атрибутами — нормализация
    иерархии и отсечение шума вынесена в issue #54.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import IO, Union


# ---------------------------------------------------------------------------
# Публичные типы
# ---------------------------------------------------------------------------

@dataclass
class ParsedManagedForm:
    """Результат разбора Form.xml управляемой формы.

    Все поля-списки содержат словари с сырыми атрибутами XML-узлов
    (ключ — локальное имя атрибута без namespace-префикса, значение — строка).
    Нормализация, отсечение шума и преобразование типов — задача issue #54.

    Attributes
    ----------
    ok:
        ``True`` — XML разобран успешно; ``False`` — возникли ошибки
        (подробности в ``warnings``).
    attributes:
        Список реквизитов формы (``<Attribute …/>`` узлы).
    commands:
        Список команд формы (``<Command …/>`` узлы).
    elements:
        Плоский список элементов формы (``<Element …/>`` узлы).
        Вложенность сохраняется как сырые атрибуты ``parent`` / ``parentName``.
    events:
        Список событий формы (``<Event …/>`` узлы).
    relations:
        Список связей «элемент → реквизит/команда» (``<Relation …/>`` узлы).
    warnings:
        Предупреждения, накопленные при разборе. Непустой список при ``ok=False``.
    """

    ok: bool = True
    attributes: list[dict[str, str]] = field(default_factory=list)
    commands: list[dict[str, str]] = field(default_factory=list)
    elements: list[dict[str, str]] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    relations: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Вернуть локальное имя тега, отбросив namespace URI.

    ``'{http://v8.1c.ru/8.2/managed-application/core}Form'`` → ``'Form'``.
    Тег без namespace-URI возвращается без изменений.
    """
    return tag.split("}")[-1] if "}" in tag else tag


def _attrs(element: ET.Element) -> dict[str, str]:
    """Вернуть словарь атрибутов XML-элемента с нормализованными ключами.

    Ключи нормализуются через :func:`_local` — namespace-URI из имён
    атрибутов отбрасывается. Значения остаются строками.
    """
    return {_local(k): v for k, v in element.attrib.items()}


def _collect_section(
    root: ET.Element,
    section_tag: str,
    item_tag: str,
) -> list[dict[str, str]]:
    """Собрать список словарей атрибутов из секции XML-дерева.

    Ищет первый дочерний элемент с локальным тегом ``section_tag``,
    затем собирает все дочерние элементы с локальным тегом ``item_tag``.
    Секция или элементы могут отсутствовать — возвращается пустой список.

    Parameters
    ----------
    root:
        Корневой элемент разобранного XML-дерева.
    section_tag:
        Ожидаемый локальный тег секции (``'Attributes'``, ``'Commands'`` …).
    item_tag:
        Ожидаемый локальный тег элемента внутри секции
        (``'Attribute'``, ``'Command'`` …).
    """
    result: list[dict[str, str]] = []
    for child in root:
        if _local(child.tag) == section_tag:
            for item in child:
                if _local(item.tag) == item_tag:
                    result.append(_attrs(item))
            break  # берём только первую секцию с этим именем
    return result


def _source_to_bytes(source: Union[str, bytes, IO]) -> bytes:
    """Привести ``source`` к ``bytes`` для передачи в ``ET.fromstring``.

    - ``str`` → кодируем в UTF-8 (BOM не добавляем).
    - ``bytes`` → возвращаем как есть.
    - Файлоподобный объект → читаем, затем применяем те же правила.
    """
    if isinstance(source, bytes):
        return source
    if isinstance(source, str):
        return source.encode("utf-8")
    # Файлоподобный объект
    raw = source.read()
    if isinstance(raw, bytes):
        return raw
    # io.StringIO.read() возвращает str
    return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def parse_managed_form(
    source: Union[str, bytes, IO],
) -> ParsedManagedForm:
    """Разобрать Form.xml управляемой формы из содержимого или потока.

    Принимает XML как строку (``str``), байты (``bytes``) или файлоподобный
    объект (``io.StringIO``, ``io.BytesIO``, открытый файл). Все три варианта
    дают одинаковый результат для одного содержимого.

    **Нет обращений к файловой системе.** Путь к файлу не принимается;
    если нужно разобрать файл — откройте его снаружи и передайте поток:

    .. code-block:: python

        with open(path, "rb") as fh:
            result = parse_managed_form(fh)

    Parameters
    ----------
    source:
        XML-содержимое: строка, байты или файлоподобный объект.
        UTF-8; BOM игнорируется.

    Returns
    -------
    :class:`ParsedManagedForm`
        Разобранная структура. ``ok=False`` при синтаксической ошибке;
        ``warnings`` содержат описание проблемы.

    Notes
    -----
    Структура секций (ожидаемый XML)::

        <Form xmlns="http://v8.1c.ru/8.2/managed-application/core" id="{GUID}">
          <Attributes>  <Attribute name="…" type="…" id="…"/>  </Attributes>
          <Commands>    <Command  name="…" action="…" id="…"/> </Commands>
          <Elements>    <Element  name="…" type="…" id="…"/>   </Elements>
          <Events>      <Event    name="…" handler="…" id="…"/></Events>
          <Relations>   <Relation attribute="…" element="…"/>  </Relations>
        </Form>

    Любой другой namespace или структура не вызывает исключения: секции,
    которые не нашлись, дают пустые списки в результате.
    """
    warnings: list[str] = []

    try:
        raw = _source_to_bytes(source)
    except Exception as exc:  # noqa: BLE001
        return ParsedManagedForm(
            ok=False,
            warnings=[f"failed to read source: {exc}"],
        )

    try:
        root = ET.fromstring(raw)  # noqa: S314 — синтетические данные, без сети
    except ET.ParseError as exc:
        return ParsedManagedForm(
            ok=False,
            warnings=[f"XML parse error: {exc}"],
        )
    except Exception as exc:  # noqa: BLE001
        return ParsedManagedForm(
            ok=False,
            warnings=[f"unexpected parse error: {exc}"],
        )

    # Проверяем, что корневой элемент похож на Form
    root_local = _local(root.tag)
    if root_local != "Form":
        warnings.append(
            f"unexpected root tag '{root_local}'; expected 'Form'"
        )

    attributes = _collect_section(root, "Attributes", "Attribute")
    commands = _collect_section(root, "Commands", "Command")
    elements = _collect_section(root, "Elements", "Element")
    events = _collect_section(root, "Events", "Event")
    relations = _collect_section(root, "Relations", "Relation")

    return ParsedManagedForm(
        ok=True,
        attributes=attributes,
        commands=commands,
        elements=elements,
        events=events,
        relations=relations,
        warnings=warnings,
    )
