"""object_decoder — нормализация реквизитов из raw-header объектных JSON-файлов 1С.

Цель модуля — читать raw-секцию ``header`` из ``Catalog.json``, ``Document.json``
и аналогичных файлов выгрузки v8unpack и возвращать
нормализованную структуру совместимую с ``catalog_resolver``.

UUID-карта для ``elem_parser`` строится из возвращаемого ``DecodeResult``
без дублирующего парсинга header.

Best-effort: любая ошибка не пробрасывается, повреждённый узел
пропускается с записью в ``warnings``.

Начиная с #88 ссылочный тип может быть приведён к имени объекта метаданных
через опциональный ``type_resolver``. Без резолвера поведение не меняется:
ссылка остаётся в виде ``Ref#<uuid>``.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from v8unpack_agent._safe_paths import safe_error_text, safe_path_ref

# ---------------------------------------------------------------------------
# Public API types
# ---------------------------------------------------------------------------

class DecodeError(enum.Enum):
    """Cтатус ошибки декодирования."""
    JSON_NOT_FOUND = "json_not_found"
    JSON_PARSE_ERROR = "json_parse_error"
    HEADER_MISSING = "header_missing"
    VERSION_UNSUPPORTED = "version_unsupported"


@dataclass
class DecodeResult:
    """Cтруктурированный результат декодирования."""
    ok: bool
    data: dict
    error: DecodeError | None = None
    warnings: list[str] = field(default_factory=list)


def _empty_data() -> dict:
    return {"Properties": [], "TabularSections": []}


def _fail(error: DecodeError, msg: str) -> DecodeResult:
    return DecodeResult(ok=False, data=_empty_data(), error=error, warnings=[msg])


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def decode_object_attributes(
    object_json: Path,
    type_resolver: Callable[[str], str | None] | None = None,
) -> DecodeResult:
    """Cчитать raw-``header`` из файла объекта метаданных и вернуть
    нормализованную структуру Properties + TabularSections.

    ``type_resolver`` (#88) — опциональная функция ``uuid -> имя типа``.
    Она получает UUID без префикса ``Ref#``. Если резолвер не передан либо
    вернул ``None``, значение остаётся ``Ref#<uuid>``.
    """
    object_json = Path(object_json)

    if not object_json.exists():
        return _fail(DecodeError.JSON_NOT_FOUND,
                     f"object_decoder: файл не найден: {safe_path_ref(object_json, tail=3)}")

    try:
        raw = json.loads(object_json.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return _fail(DecodeError.JSON_PARSE_ERROR,
                     f"object_decoder: ошибка чтения {safe_path_ref(object_json, tail=3)}: "
                     f"{safe_error_text(exc, object_json, tail=3)}")

    if not isinstance(raw, dict) or "header" not in raw:
        return _fail(DecodeError.HEADER_MISSING,
                     f"object_decoder: отсутствует 'header' в {object_json.name}")

    header = raw["header"]
    if not isinstance(header, list):
        return _fail(DecodeError.VERSION_UNSUPPORTED,
                     f"object_decoder: 'header' не является списком в {object_json.name}")

    warnings: list[str] = []

    # Реальные выгрузки v8unpack используют строковые теги и раздельные
    # контейнеры реквизитов/табличных частей. Синтетический walker ниже
    # остаётся fallback для старого и нормализованного формата.
    real_data = _decode_real_header(header, warnings)
    if real_data is not None:
        _apply_type_resolver(real_data, type_resolver, warnings)
        return DecodeResult(ok=True, data=real_data, warnings=warnings)

    properties: list[dict] = []
    tabular_sections: list[dict] = []

    # seen-множества создаются пер вызов, не на уровне модуля
    props_seen: set[int] = set()
    ts_seen: set[int] = set()

    _walk_node(header, properties, tabular_sections, warnings,
               depth=0, props_seen=props_seen, ts_seen=ts_seen)

    data = {"Properties": properties, "TabularSections": tabular_sections}
    _apply_type_resolver(data, type_resolver, warnings)

    return DecodeResult(ok=True, data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# Reference type resolution (#88)
# ---------------------------------------------------------------------------

def _iter_decoded_properties(data: dict):
    """Все записи реквизитов результата, включая колонки табличных частей."""
    for prop in data.get("Properties", []):
        if isinstance(prop, dict):
            yield prop
    for section in data.get("TabularSections", []):
        if not isinstance(section, dict):
            continue
        for prop in section.get("Properties", []):
            if isinstance(prop, dict):
                yield prop


def _apply_type_resolver(
    data: dict,
    type_resolver: Callable[[str], str | None] | None,
    warnings: list[str],
) -> None:
    """Заменить ``Ref#<uuid>`` читаемым именем типа, если резолвер его знает.

    Примитивные и уже распознанные значения не изменяются. Неизвестный UUID
    остаётся безопасным ``Ref#<uuid>`` — тип не угадывается.
    """
    if type_resolver is None:
        return

    for prop in _iter_decoded_properties(data):
        value_type = prop.get("Type")
        if not isinstance(value_type, str):
            continue
        if not value_type.startswith(_REF_TYPE_PREFIX):
            continue

        ref_uuid = value_type[len(_REF_TYPE_PREFIX):]
        if not ref_uuid:
            continue

        try:
            resolved = type_resolver(ref_uuid)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"object_decoder: REF_RESOLVER_FAILED - {ref_uuid}: {exc}"
            )
            continue

        if isinstance(resolved, str) and resolved:
            prop["Type"] = resolved


# ---------------------------------------------------------------------------
# Internal raw-header walker
# ---------------------------------------------------------------------------

_NULL_UUID = "00000000-0000-0000-0000-000000000000"
_MAX_DEPTH = 20


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    parts = value.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


def _unquote(value: object) -> str | None:
    if not isinstance(value, str) or len(value) < 2:
        return None
    if value[0] == '"' and value[-1] == '"':
        return value[1:-1].strip() or None
    return value.strip() or None


def _extract_synonym_from_node(node: Any) -> str | None:
    if node is None:
        return None
    if isinstance(node, str):
        return _unquote(node)
    if not isinstance(node, list):
        return None
    if len(node) >= 3 and isinstance(node[2], list) and len(node[2]) >= 3:
        return _unquote(node[2][2])
    for item in node:
        if isinstance(item, str) and item.startswith('"') and item.endswith('"'):
            return _unquote(item)
    return None


def _extract_type_from_node(node: Any) -> str | None:
    if node is None:
        return None
    if isinstance(node, str):
        return _unquote(node)
    if not isinstance(node, list):
        return None
    for item in node:
        if isinstance(item, str):
            val = _unquote(item)
            if val:
                return val
    return None


def _tag_eq(value: object, expected: int | str) -> bool:
    """Сравнить числовой тег независимо от JSON-представления."""
    return str(value) == str(expected)


def _at(node: object, *indexes: int) -> object | None:
    """Безопасно получить элемент глубоко вложенного списка."""
    current = node
    for index in indexes:
        if not isinstance(current, list) or index >= len(current):
            return None
        current = current[index]
    return current


def _container_records(node: object) -> list:
    """Записи контейнера ``[service-id, count, record, ...]``."""
    if not isinstance(node, list) or len(node) < 2:
        return []
    try:
        expected = int(node[1])
    except (TypeError, ValueError):
        return []
    if expected < 0:
        return []
    return node[2:2 + expected]


# ---------------------------------------------------------------------------
# Type resolution (production layout)
# ---------------------------------------------------------------------------

_PRIMITIVE_TYPE_CODES: dict[str, str] = {
    "S": "String",
    "N": "Number",
    "B": "Boolean",
    "D": "Date",
    "U": "Undefined",
    "T": "Null",
}

_REF_TYPE_PREFIX = "Ref#"


def _resolve_type_from_descriptor(
    descriptor: object,
    warnings: list[str] | None = None,
    prop_name: str = "",
) -> str | None:
    # descriptor = [tag, name_entry, type_block, ...]
    # type_block = [Pattern, type_node]; type_node = [code, ...]
    # primitive -> name; ref -> Ref#<uuid> (see #88); unknown -> None + warning
    if warnings is None:
        warnings = []
    if not isinstance(descriptor, list) or len(descriptor) < 3:
        return None

    type_block = descriptor[2]
    if not isinstance(type_block, list) or len(type_block) < 2:
        return None
    if _unquote(type_block[0]) != "Pattern":
        return None

    type_node = type_block[1]
    if not isinstance(type_node, list) or not type_node:
        return None

    code = _unquote(type_node[0])
    if code is None:
        return None

    if code in _PRIMITIVE_TYPE_CODES:
        return _PRIMITIVE_TYPE_CODES[code]

    if code == "#":
        if len(type_node) >= 2 and isinstance(type_node[1], str):
            ref_uuid = type_node[1].strip(chr(34)).strip()
            if ref_uuid:
                return _REF_TYPE_PREFIX + ref_uuid
        return None

    context = f" rekvizit={prop_name!r}" if prop_name else ""
    warnings.append(
        f"object_decoder: TYPE_UNKNOWN - neizvestnyy kod tipa {code!r}{context}"
    )
    return None


def _decode_real_name_entry(
    entry: object,
    warnings: list[str] | None = None,
    descriptor: object = None,
) -> dict | None:
    """Декодировать name-entry реального raw-header v8unpack."""
    if not isinstance(entry, list) or len(entry) < 4:
        return None
    # Код варианта name-entry зависит от типа/версии метаданных. В реальных
    # выгрузках подтверждены 0, 1, 2 и 3; структура UUID/name/synonym при этом
    # одинакова. Остальные коды не принимаем, чтобы не собирать служебные узлы.
    # Строковый тип тега также отличает raw production-layout от старого
    # нормализованного формата с целочисленными тегами: последний должен
    # обрабатываться legacy walker, сохраняющим Type, ТЧ и warnings.
    if not isinstance(entry[0], str) or entry[0] not in {"0", "1", "2", "3"}:
        return None

    uuid = _at(entry, 1, 2)
    name = _unquote(entry[2])
    synonym = _unquote(_at(entry, 3, 2))
    if not _is_uuid(uuid) or uuid == _NULL_UUID or not name:
        return None

    return {
        "UUID": uuid,
        "Name": name,
        # В реальном формате тип находится в соседнем descriptor. Пока UUID
        # типа не разрешён в имя метаданных, нельзя подставлять "Pattern"/"ru".
        "Type": _resolve_type_from_descriptor(descriptor, warnings, name),
        "Synonym": synonym,
    }


def _decode_real_attribute_wrapper(
    wrapper: object,
    warnings: list[str] | None = None,
) -> dict | None:
    """Декодировать реквизит из wrapper реального v8unpack-контейнера."""
    descriptor = _at(wrapper, 0, 1, 1)
    return _decode_real_name_entry(_at(descriptor, 1), warnings, descriptor)


def _decode_compact_attribute_wrapper(wrapper: object) -> dict | None:
    """Декодировать реквизит compact owner-metadata: [1, 1, node]."""
    if not isinstance(wrapper, list) or len(wrapper) < 3:
        return None

    node = wrapper[2]
    if not isinstance(node, list) or len(node) < 7:
        return None

    uuid = node[3]
    name = _unquote(node[4])
    locale = node[5]
    synonym = _unquote(node[6])

    # Проверка locale отделяет compact-layout от production name-entry.
    if not isinstance(locale, str):
        return None
    if not _is_uuid(uuid) or uuid == _NULL_UUID or not name:
        return None

    return {
        "UUID": uuid,
        "Name": name,
        "Type": None,
        "Synonym": synonym,
    }


def _decode_real_tabular_section(
    node: object,
    warnings: list[str] | None = None,
) -> dict | None:
    """Декодировать ТЧ и её реквизиты из реального raw-header."""
    if not isinstance(node, list) or len(node) < 3:
        return None

    ts_descriptor = _at(node, 0, 1, 5)
    decoded = _decode_real_name_entry(_at(ts_descriptor, 1), warnings, ts_descriptor)
    if decoded is None:
        return None

    properties = []
    for wrapper in _container_records(node[2]):
        prop = _decode_real_attribute_wrapper(wrapper, warnings)
        if prop is not None:
            properties.append(prop)

    return {
        "UUID": decoded["UUID"],
        "Name": decoded["Name"],
        "Synonym": decoded["Synonym"],
        "Properties": properties,
    }


def _decode_real_header(
    header: object,
    warnings: list[str],
) -> dict | None:
    """Декодировать production-layout v8unpack со строковыми тегами.

    Позиции контейнеров зависят от типа и версии метаданных, поэтому поиск
    выполняется по устойчивой структуре wrapper/descriptor, а не по индексам
    ``root[3]`` и ``root[5]``.
    """
    if not isinstance(header, list):
        return None

    def iter_lists(node: object):
        if not isinstance(node, list):
            return
        yield node
        for child in node:
            yield from iter_lists(child)

    tabular_sections = []
    section_uuids: set[str] = set()
    nested_uuids: set[str] = set()

    # ТЧ распознаётся по связке: header-wrapper, флаг и counted-контейнер
    # колонок. Такой узел одинаков для разных позиций в Document/Catalog.
    for node in iter_lists(header):
        section = _decode_real_tabular_section(node, warnings)
        if section is None or section["UUID"] in section_uuids:
            continue
        section_uuids.add(section["UUID"])
        nested_uuids.update(
            prop["UUID"] for prop in section["Properties"] if prop["UUID"]
        )
        tabular_sections.append(section)

    properties = []
    property_uuids: set[str] = set()
    blocked_uuids = section_uuids | nested_uuids

    # В owner metadata верхнеуровневые реквизиты лежат в header[0][6].
    for wrapper in _container_records(_at(header, 0, 6)):
        prop = _decode_compact_attribute_wrapper(wrapper)
        if prop is None:
            continue

        uuid = prop["UUID"]
        if uuid in blocked_uuids or uuid in property_uuids:
            continue

        property_uuids.add(uuid)
        properties.append(prop)

    # После выделения ТЧ остальные attribute-wrapper являются реквизитами
    # объекта. UUID предотвращает повторный сбор одного узла на разных уровнях.
    for node in iter_lists(header):
        prop = _decode_real_attribute_wrapper(node, warnings)
        if prop is None:
            continue
        uuid = prop["UUID"]
        if uuid in blocked_uuids or uuid in property_uuids:
            continue
        property_uuids.add(uuid)
        properties.append(prop)

    # Последний best-effort слой повторяет доказавший покрытие permissive
    # подход: собирает непосредственные name-entry независимо от окружающей
    # версии wrapper. Уже распознанные ТЧ и их колонки исключаются, поэтому
    # структурный результат имеет приоритет. Для неизвестных layout запись
    # остаётся верхнеуровневым Property с Type=None, но UUID-карта не теряется.
    for node in iter_lists(header):
        prop = _decode_real_name_entry(node, warnings)
        if prop is None:
            continue
        uuid = prop["UUID"]
        if uuid in blocked_uuids or uuid in property_uuids:
            continue
        property_uuids.add(uuid)
        properties.append(prop)

    if not properties and not tabular_sections:
        return None
    return {
        "Properties": properties,
        "TabularSections": tabular_sections,
    }


def _has_valid_uuid_block(entry: Any) -> bool:
    # Uzel raspoznan kak rekvizit: entry[1] == [_, _, <valid UUID>]
    if not isinstance(entry, list) or len(entry) < 2:
        return False
    block = entry[1]
    if not isinstance(block, list) or len(block) < 3:
        return False
    return _is_uuid(block[2]) and block[2] != _NULL_UUID


def _try_decode_prop_entry(entry: Any, warnings: list[str]) -> dict | None:
    """entry[0]=тег, entry[1]=UUID-блок, entry[2]=имя, entry[3]=тип, entry[4]=синоним."""
    # Warning tolko dlya RASPOZNANNOGO uzla rekvizita (validnyy UUID-blok).
    if not isinstance(entry, list) or len(entry) < 3:
        if _has_valid_uuid_block(entry):
            warnings.append(
                f"object_decoder: povrezhdyonnyy uzel rekvizita: UUID={entry[1][2]}, len={len(entry)}"
            )
        return None

    uuid_block = entry[1] if len(entry) > 1 else None
    uuid: str | None = None
    if isinstance(uuid_block, list) and len(uuid_block) >= 3:
        candidate = uuid_block[2]
        if _is_uuid(candidate) and candidate != _NULL_UUID:
            uuid = candidate

    name = _unquote(entry[2]) if len(entry) > 2 else None
    if not name:
        if uuid:
            warnings.append(
                f"object_decoder: povrezhdyonnyy uzel rekvizita: UUID={uuid}, imya otsutstvuet"
            )
        return None

    type_val = _extract_type_from_node(entry[3] if len(entry) > 3 else None)
    synonym = _extract_synonym_from_node(entry[4] if len(entry) > 4 else None)

    return {
        "UUID": uuid or "",
        "Name": name,
        "Type": type_val,
        "Synonym": synonym,
    }


def _collect_prop_list(prop_list_node: Any, warnings: list[str]) -> list[dict]:
    """prop-блок: [0, entry1, entry2, ...] — индекс 0 является тегом."""
    result: list[dict] = []
    if not isinstance(prop_list_node, list):
        return result
    for i, entry in enumerate(prop_list_node):
        if i == 0:
            continue
        if entry is None:
            continue
        decoded = _try_decode_prop_entry(entry, warnings)
        if decoded is not None:
            result.append(decoded)
    return result


def _try_decode_ts_entry(entry: Any, warnings: list[str]) -> dict | None:
    """entry[0]=тег, [1]=UUID-блок ТЧ, [2]=имя, [3]=синоним, [4]=prop-блок."""
    if not isinstance(entry, list) or len(entry) < 3:
        return None

    uuid_block = entry[1] if len(entry) > 1 else None
    uuid: str | None = None
    if isinstance(uuid_block, list) and len(uuid_block) >= 3:
        candidate = uuid_block[2]
        if _is_uuid(candidate) and candidate != _NULL_UUID:
            uuid = candidate

    name = _unquote(entry[2]) if len(entry) > 2 else None
    if not name:
        return None

    synonym = _extract_synonym_from_node(entry[3] if len(entry) > 3 else None)
    props_node = entry[4] if len(entry) > 4 else None
    props = _collect_prop_list(props_node, warnings) if props_node is not None else []

    return {
        "UUID": uuid or "",
        "Name": name,
        "Synonym": synonym,
        "Properties": props,
    }


def _looks_like_prop_entry(item: Any) -> bool:
    """item похож на запись реквизита: [0, [0,0,uuid], '"Name"', ...]."""
    if not isinstance(item, list) or len(item) < 3:
        return False
    uuid_block = item[1] if len(item) > 1 else None
    if not isinstance(uuid_block, list) or len(uuid_block) < 3:
        return False
    return _is_uuid(uuid_block[2]) and uuid_block[2] != _NULL_UUID


def _looks_like_prop_list(node: list) -> bool:
    """[0, entry, entry, ...] — хотя бы один элемент с UUID-блоком."""
    if not isinstance(node, list) or len(node) < 2 or node[0] != 0:
        return False
    return any(_looks_like_prop_entry(item) for item in node[1:])


def _looks_like_ts_list(node: list) -> bool:
    """[0, ts_entry, ...] — ts_entry имеет UUID и entry[4] является списком (prop-блок)."""
    if not _looks_like_prop_list(node):
        return False
    for item in node[1:]:
        if (
            _looks_like_prop_entry(item)
            and len(item) > 4
            and _looks_like_prop_list(item[4])
        ):
            return True
    return False


def _walk_node(
    node: Any,
    properties: list[dict],
    tabular_sections: list[dict],
    warnings: list[str],
    depth: int,
    props_seen: set[int],
    ts_seen: set[int],
) -> None:
    """Best-effort рекурсивный обход header.

    seen-множества передаются сверху вниз и создаются заново при каждом
    вызове decode_object_attributes, исключая загрязнение между тестами.
    """
    if depth > _MAX_DEPTH or not isinstance(node, list):
        return
    node_id = id(node)

    if _looks_like_ts_list(node) and node_id not in ts_seen:
        ts_seen.add(node_id)
        for i, entry in enumerate(node):
            if i == 0:
                continue
            decoded = _try_decode_ts_entry(entry, warnings)
            if decoded is not None:
                tabular_sections.append(decoded)
        return

    if _looks_like_prop_list(node) and node_id not in props_seen:
        props_seen.add(node_id)
        for i, entry in enumerate(node):
            if i == 0:
                continue
            decoded = _try_decode_prop_entry(entry, warnings)
            if decoded is not None:
                properties.append(decoded)
        return

    for child in node:
        _walk_node(child, properties, tabular_sections, warnings,
                   depth + 1, props_seen=props_seen, ts_seen=ts_seen)
