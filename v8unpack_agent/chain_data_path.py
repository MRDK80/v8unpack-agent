"""Сегментные цепочки ``data_path`` управляемых форм (issue #89).

Часть управляемых форм при непустой карте реквизитов объекта не получала
ни одной привязки. Причина не в карте: элементы таких форм вообще не
ссылаются на реквизиты объекта по UUID — они адресуют вложенное поле
реквизита формы цепочкой сегментов.

Контракт блока привязки (``raw[11]``)::

    [<счётчик>, <сегмент>, ["0", <uuid типа>], <сегмент>, ...]

``<счётчик>`` равен числу элементов после него. Записи ``["0", <uuid>]``
описывают тип следующего звена и в пути не участвуют. Остальные
записи — сегменты пути двух видов:

* ``[<id>]`` — узел дерева реквизитов формы (секция ``props``, включая
  вложенные ``child``);
* ``[<id>, <uuid таблицы>]`` — поле таблицы определений из большого
  JSON формы (``$.form[0][0][3]``). Узел таблицы имеет вид
  ``["0", <адрес>, <кол-во>, <поле>, ...]``, а поле —
  ``["5", <id>, "0", "\"Имя\"", ...]``.

Адрес таблицы — это префикс цепочки, поэтому резолюция идёт строго
последовательно: имя i-го сегмента ищется в таблице, адрес которой
равен первым i сегментам.

Защита от ложных привязок — два независимых условия:

1. UUID таблицы из сегмента обязан встречаться в адресах известных
   определений этой формы;
2. склейка имён всех сегментов обязана посимвольно совпасть с именем
   элемента (платформа именует такие элементы склейкой пути).

Любое расхождение оставляет элемент без ``data_path``: выдуманные пути хуже
отсутствующих. Цепочки короче двух сегментов не обрабатываются: это
старый формат со ссылкой на реквизит объекта, его разбирает
``elem_parser.decode_element_data_path``.

Машиночитаемые причины нулевой привязки (issue #116)
---------------------------------------------------------------

Отсутствие привязки раньше либо молчало, либо выражалось текстовым
предупреждением, пригодным для чтения человеком, но не для отчётов.
:class:`ZeroBindingReason` возвращает ту же структурную диагностику
стабильным литералом. Нового разбора не появляется: классификаторы
опираются на те же вспомогательные функции, что и декодер.

Модуль работает best-effort: входные структуры не мутируются, исключения
наружу не пробрасываются.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "BIND_SLOT",
    "ZeroBindingReason",
    "aggregate_form_zero_binding",
    "build_form_attribute_ids",
    "build_form_segment_tables",
    "classify_element_zero_binding",
    "classify_raw_zero_binding",
    "decode_chain_data_path",
    "enrich_elements_with_chain_paths",
    "load_form_segment_tables",
]

# Позиция блока привязки в записи элемента управляемой формы.
# Подтверждена на двух независимых layout: ссылка на реквизит объекта
# и цепочка сегментов лежат в одной и той же позиции.
BIND_SLOT = 11

# Секция таблиц определений внутри большого JSON формы.
_SEGMENT_SECTION_PATH = (0, 0, 3)

_DEFINITION_TAG = "0"
_FIELD_TAG = "5"
_FIELD_NAME_POS = 3
_MIN_CHAIN_SEGMENTS = 2
_CHILD_KEYS = ("child", "Child", "children", "Children", "items", "Items")

# Блок ["1", "0"] встречается у заведомо непривязанных элементов рабочих
# форм, поэтому это отдельная причина, а не повреждённая структура.
_UNBOUND_MARKER_COUNTER = "1"
_UNBOUND_MARKER_VALUE = "0"


class ZeroBindingReason(enum.Enum):
    """Причина отсутствия ``data_path`` у элемента или формы (issue #116).

    Каждое значение следует из наблюдаемой структуры записи элемента
    и имеет синтетическую тестовую фикстуру. Общего статуса вроде
    ``unknown`` здесь нет намеренно: отдельный диагностический код
    полезнее молчания и полезнее частичного «успеха».

    ``NO_BIND_SLOT``
        В записи элемента нет слота :data:`BIND_SLOT`.
    ``BIND_SLOT_UNBOUND_MARKER``
        Слот равен ``["1", "0"]`` — маркер непривязанного элемента.
    ``CHAIN_MALFORMED``
        Счётчик не согласован с составом блока.
    ``CHAIN_TOO_SHORT``
        Сегментов меньше двух: цепочкой это не является.
    ``CHAIN_TABLE_NOT_DECLARED``
        UUID таблицы сегмента не объявлен в определениях формы.
    ``CHAIN_SEGMENT_UNRESOLVED``
        Сегмент не найден ни в таблицах, ни среди реквизитов формы.
    ``CHAIN_NAME_MISMATCH``
        Склейка имён сегментов не совпала с именем элемента.
    ``MIXED``
        Агрегат уровня формы: у элементов разные причины.
    """

    NO_BIND_SLOT = "no_bind_slot"
    BIND_SLOT_UNBOUND_MARKER = "bind_slot_unbound_marker"
    CHAIN_MALFORMED = "chain_malformed"
    CHAIN_TOO_SHORT = "chain_too_short"
    CHAIN_TABLE_NOT_DECLARED = "chain_table_not_declared"
    CHAIN_SEGMENT_UNRESOLVED = "chain_segment_unresolved"
    CHAIN_NAME_MISMATCH = "chain_name_mismatch"
    MIXED = "mixed"


def _is_uuid(value: object) -> bool:
    """Проверка формата UUID без зависимости от elem_parser."""
    if not isinstance(value, str) or len(value) != 36:
        return False
    parts = value.split("-")
    if [len(part) for part in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(char in "0123456789abcdefABCDEF" for part in parts for char in part)


def _unquote(value: object) -> str | None:
    """Снять кавычки строкового литерала 1С."""
    if not isinstance(value, str) or len(value) < 3:
        return None
    if value[0] == '"' and value[-1] == '"':
        return value[1:-1].strip() or None
    return None


def _address_key(segments: list) -> tuple[str, ...]:
    """Ключ адреса: детерминированная сериализация сегментов."""
    return tuple(json.dumps(segment, ensure_ascii=False) for segment in segments)


def build_form_attribute_ids(
    props_section: Any,
    parent: str | None = None,
    out: dict[tuple[str | None, int], str] | None = None,
) -> dict[tuple[str | None, int], str]:
    """Дерево реквизитов формы: ``(имя родителя, id) -> имя``.

    Вложенные узлы лежат в ``child``; у корневых реквизитов родитель ``None``.
    Идентификаторы уникальны только в пределах уровня, поэтому ключ
    составной.
    """
    if out is None:
        out = {}
    if isinstance(props_section, list):
        for item in props_section:
            build_form_attribute_ids(item, parent, out)
        return out
    if not isinstance(props_section, dict):
        return out

    name = props_section.get("name")
    ident = props_section.get("id")
    current = parent
    if isinstance(name, str) and name.strip():
        current = name.strip()
        if isinstance(ident, str) and ident.isdigit():
            out.setdefault((parent, int(ident)), current)
        elif isinstance(ident, int):
            out.setdefault((parent, ident), current)

    for key in _CHILD_KEYS:
        if key in props_section:
            build_form_attribute_ids(props_section[key], current, out)
    return out


def build_form_segment_tables(form_json: Any) -> dict[tuple[str, ...], dict[int, str]]:
    """Таблицы определений из большого JSON формы.

    Возвращает ``адрес -> {id поля: имя}``. При любом несоответствии
    структуры возвращается пустой словарь, а не частичный результат.
    """
    tables: dict[tuple[str, ...], dict[int, str]] = {}
    if not isinstance(form_json, dict):
        return tables
    node: Any = form_json.get("form")
    try:
        for index in _SEGMENT_SECTION_PATH:
            node = node[index]
    except (TypeError, IndexError, KeyError):
        return tables
    if not isinstance(node, list):
        return tables

    for entry in node:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        if entry[0] != _DEFINITION_TAG:
            continue
        address = entry[1]
        if not isinstance(address, list) or not address:
            continue
        head = address[0]
        if not (isinstance(head, str) and head.isdigit()):
            continue
        address_segments = [item for item in address[1:] if isinstance(item, list)]
        if not address_segments:
            continue

        fields: dict[int, str] = {}
        for field in entry[3:]:
            if not isinstance(field, list) or len(field) <= _FIELD_NAME_POS:
                continue
            if field[0] != _FIELD_TAG:
                continue
            ident = field[1]
            if not (isinstance(ident, str) and ident.isdigit()):
                continue
            name = _unquote(field[_FIELD_NAME_POS])
            if name:
                fields.setdefault(int(ident), name)

        if fields:
            tables.setdefault(_address_key(address_segments), fields)
    return tables


def load_form_segment_tables(form_root: Path) -> dict[tuple[str, ...], dict[int, str]]:
    """Прочитать таблицы определений из большого JSON формы."""
    candidates = [
        path
        for path in form_root.glob("*.json")
        if not path.name.endswith(".elem.json")
        and ".id" not in path.stem
        and path.name != "form_elements_index.json"
    ]
    if not candidates:
        return {}
    target = max(candidates, key=lambda path: path.stat().st_size)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    return build_form_segment_tables(payload)


def _known_table_uuids(tables: dict[tuple[str, ...], dict[int, str]]) -> set[str]:
    """UUID таблиц, реально объявленных в адресах определений."""
    known: set[str] = set()
    for key in tables:
        for part in key:
            try:
                segment = json.loads(part)
            except ValueError:
                continue
            if isinstance(segment, list) and len(segment) == 2 and _is_uuid(segment[1]):
                known.add(segment[1])
    return known


def _is_type_separator(item: object) -> bool:
    """Запись ``["0", <uuid>]`` описывает тип, а не сегмент пути."""
    return (
        isinstance(item, list)
        and len(item) == 2
        and item[0] == "0"
        and _is_uuid(item[1])
    )


def _is_unbound_marker(block: object) -> bool:
    """Блок ``["1", "0"]``: элемент заведомо не привязан к данным."""
    return (
        isinstance(block, list)
        and len(block) == 2
        and block[0] == _UNBOUND_MARKER_COUNTER
        and block[1] == _UNBOUND_MARKER_VALUE
    )


def _bind_block_items(block: object) -> list | None:
    """Записи блока привязки, если счётчик и состав согласованы.

    Счётчик обязан совпадать с числом элементов после него.
    Несовпадение означает повреждённую или чужую структуру.
    """
    if not isinstance(block, list) or len(block) < 2:
        return None
    head = block[0]
    if not (isinstance(head, str) and head.isdigit()):
        return None
    items = block[1:]
    if int(head) != len(items):
        return None
    if not all(isinstance(item, list) for item in items):
        return None
    return items


def _chain_segments(block: object) -> list | None:
    """Сегменты пути из блока привязки или ``None``."""
    items = _bind_block_items(block)
    if items is None:
        return None
    segments = [item for item in items if not _is_type_separator(item)]
    return segments or None


def _segment_parts(segment: list) -> tuple[int | None, str | None]:
    """Идентификатор сегмента и UUID его таблицы, если есть."""
    if not isinstance(segment, list) or not segment:
        return None, None
    head = segment[0]
    ident = int(head) if isinstance(head, str) and head.isdigit() else None
    table_uuid = None
    if len(segment) >= 2 and _is_uuid(segment[1]):
        table_uuid = segment[1]
    return ident, table_uuid


def decode_chain_data_path(
    block: object,
    segment_tables: dict[tuple[str, ...], dict[int, str]],
    form_attribute_ids: dict[tuple[str | None, int], str],
    element_name: str | None = None,
) -> tuple[str | None, list[str]]:
    """``data_path`` из цепочки сегментов или ``None`` с диагностикой.

    Входные структуры не изменяются. При любом сомнении возвращается
    ``None``: ложная привязка хуже отсутствующей.
    """
    segments = _chain_segments(block)
    if segments is None or len(segments) < _MIN_CHAIN_SEGMENTS:
        return None, []

    known_uuids = _known_table_uuids(segment_tables)
    names: list[str] = []

    for position, segment in enumerate(segments):
        ident, table_uuid = _segment_parts(segment)
        if ident is None:
            return None, [
                f"decode_chain_data_path: сегмент {position} без числового id"
                f"{_context(element_name)}"
            ]
        if table_uuid is not None and table_uuid not in known_uuids:
            return None, [
                f"decode_chain_data_path: таблица сегмента {position} не объявлена "
                f"в определениях формы{_context(element_name)}"
            ]

        table = segment_tables.get(_address_key(segments[:position]))
        name = table.get(ident) if table else None
        if name is None:
            parent = names[-1] if names else None
            name = form_attribute_ids.get((parent, ident))
        if name is None:
            return None, [
                f"decode_chain_data_path: сегмент {position} (id={ident}) не найден "
                f"ни в таблицах определений, ни среди реквизитов формы"
                f"{_context(element_name)}"
            ]
        names.append(name)

    if element_name is not None and "".join(names) != element_name:
        return None, [
            f"decode_chain_data_path: склейка сегментов не совпала с именем "
            f"элемента{_context(element_name)}"
        ]

    return ".".join(names), []


def _context(element_name: str | None) -> str:
    return f", элемент={element_name!r}" if element_name else ""


def classify_element_zero_binding(
    block: object,
    segment_tables: dict[tuple[str, ...], dict[int, str]],
    form_attribute_ids: dict[tuple[str | None, int], str],
    element_name: str | None = None,
) -> ZeroBindingReason | None:
    """Причина отсутствия привязки по блоку ``raw[BIND_SLOT]`` (issue #116).

    Возвращает ``None``, если цепочка разбирается и даёт подтверждённый
    ``data_path``. Порядок проверок совпадает с порядком в
    :func:`decode_chain_data_path`, поэтому первый структурный отказ
    важнее последующего сравнения имён. Никакая ветка не создаёт
    привязку и не меняет входные структуры.
    """
    if _is_unbound_marker(block):
        return ZeroBindingReason.BIND_SLOT_UNBOUND_MARKER

    if _bind_block_items(block) is None:
        return ZeroBindingReason.CHAIN_MALFORMED

    segments = _chain_segments(block)
    if segments is None or len(segments) < _MIN_CHAIN_SEGMENTS:
        return ZeroBindingReason.CHAIN_TOO_SHORT

    known_uuids = _known_table_uuids(segment_tables)
    names: list[str] = []

    for position, segment in enumerate(segments):
        ident, table_uuid = _segment_parts(segment)
        if ident is None:
            return ZeroBindingReason.CHAIN_SEGMENT_UNRESOLVED
        if table_uuid is not None and table_uuid not in known_uuids:
            return ZeroBindingReason.CHAIN_TABLE_NOT_DECLARED

        table = segment_tables.get(_address_key(segments[:position]))
        name = table.get(ident) if table else None
        if name is None:
            parent = names[-1] if names else None
            name = form_attribute_ids.get((parent, ident))
        if name is None:
            return ZeroBindingReason.CHAIN_SEGMENT_UNRESOLVED
        names.append(name)

    if element_name is not None and "".join(names) != element_name:
        return ZeroBindingReason.CHAIN_NAME_MISMATCH

    return None


def classify_raw_zero_binding(
    raw: object,
    segment_tables: dict[tuple[str, ...], dict[int, str]],
    form_attribute_ids: dict[tuple[str | None, int], str],
    element_name: str | None = None,
) -> ZeroBindingReason | None:
    """Причина отсутствия привязки по целой записи элемента.

    Отсутствие слота :data:`BIND_SLOT` — самостоятельная причина,
    а не повреждённая цепочка.
    """
    if not isinstance(raw, list) or len(raw) <= BIND_SLOT:
        return ZeroBindingReason.NO_BIND_SLOT
    return classify_element_zero_binding(
        raw[BIND_SLOT], segment_tables, form_attribute_ids, element_name
    )


def aggregate_form_zero_binding(
    reasons: Iterable[ZeroBindingReason | None],
) -> ZeroBindingReason | None:
    """Причина уровня формы из причин её элементов.

    ``None`` — непривязанных элементов нет, форма в остаток #116 не
    входит. Единственная причина возвращается как есть; несколько
    разных дают :data:`ZeroBindingReason.MIXED`. Результат не зависит
    от порядка элементов.
    """
    distinct = {reason for reason in reasons if reason is not None}
    if not distinct:
        return None
    if len(distinct) == 1:
        return next(iter(distinct))
    return ZeroBindingReason.MIXED


def enrich_elements_with_chain_paths(
    form_root: Path,
    payload: Any,
    elements: list[dict],
    warnings: list[str],
) -> int:
    """Добавить ``data_path`` элементам с цепочкой сегментов.

    Уже найденные привязки не перебиваются, порядок элементов не меняется.
    Возвращает число добавленных привязок.
    """
    if not isinstance(payload, dict) or not elements:
        return 0
    data_section = payload.get("data")
    if not isinstance(data_section, dict):
        return 0

    attribute_ids = build_form_attribute_ids(payload.get("props"))
    if not attribute_ids:
        return 0

    records: dict[str, dict] = {}
    for key, value in data_section.items():
        if not isinstance(value, dict) or not isinstance(value.get("raw"), list):
            continue
        records.setdefault(key.rstrip("/").split("/")[-1], value)
    if not records:
        return 0

    tables = load_form_segment_tables(form_root)
    added = 0
    for element in elements:
        if element.get("data_path"):
            continue
        name = element.get("name")
        if not isinstance(name, str) or not name:
            continue
        record = records.get(name)
        if record is None:
            continue
        raw = record.get("raw")
        if not isinstance(raw, list) or len(raw) <= BIND_SLOT:
            continue
        path, chain_warnings = decode_chain_data_path(
            raw[BIND_SLOT], tables, attribute_ids, name
        )
        warnings.extend(chain_warnings)
        if path:
            element["data_path"] = path
            added += 1
    return added
