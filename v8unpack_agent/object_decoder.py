"""object_decoder — нормализация реквизитов из raw-header объектных JSON-файлов 1С.

Цель модуля — читать raw-секцию ``header`` из ``Catalog.json``, ``Document.json``
и аналогичных файлов выгрузки v8unpack и возвращать
нормализованную структуру совместимую с ``catalog_resolver``:

.. code-block:: json

    {
      "Properties": [
        {"UUID": "...", "Name": "...", "Type": "...", "Synonym": "..."}
      ],
      "TabularSections": [
        {
          "UUID": "...", "Name": "...", "Synonym": "...",
          "Properties": [
            {"UUID": "...", "Name": "...", "Type": "...", "Synonym": "..."}
          ]
        }
      ]
    }

UUID-карта для ``elem_parser`` строится из возвращаемого ``DecodeResult``
без дублирующего парсинга header.

Best-effort: любая ошибка не пробрасывается, повреждённый узел
пропускается с записью в ``warnings``.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

def decode_object_attributes(object_json: Path) -> DecodeResult:
    """Cчитать raw-``header`` из файла объекта метаданных и вернуть
    нормализованную структуру Properties + TabularSections.
    """
    object_json = Path(object_json)

    if not object_json.exists():
        return _fail(DecodeError.JSON_NOT_FOUND,
                     f"object_decoder: файл не найден: {object_json}")

    try:
        raw = json.loads(object_json.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return _fail(DecodeError.JSON_PARSE_ERROR,
                     f"object_decoder: ошибка чтения {object_json}: {exc}")

    if not isinstance(raw, dict) or "header" not in raw:
        return _fail(DecodeError.HEADER_MISSING,
                     f"object_decoder: отсутствует 'header' в {object_json.name}")

    header = raw["header"]
    if not isinstance(header, list):
        return _fail(DecodeError.VERSION_UNSUPPORTED,
                     f"object_decoder: 'header' не является списком в {object_json.name}")

    warnings: list[str] = []
    properties: list[dict] = []
    tabular_sections: list[dict] = []

    # seen-множества создаются пер вызов, не на уровне модуля
    props_seen: set[int] = set()
    ts_seen: set[int] = set()

    _walk_node(header, properties, tabular_sections, warnings,
               depth=0, props_seen=props_seen, ts_seen=ts_seen)

    return DecodeResult(
        ok=True,
        data={"Properties": properties, "TabularSections": tabular_sections},
        warnings=warnings,
    )


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


def _try_decode_prop_entry(entry: Any, warnings: list[str]) -> dict | None:
    """entry[0]=тег, entry[1]=UUID-блок, entry[2]=имя, entry[3]=тип, entry[4]=синоним."""
    if not isinstance(entry, list) or len(entry) < 3:
        warnings.append(
            f"object_decoder: повреждённый узел реквизита: "
            f"type={type(entry).__name__}, len={len(entry) if isinstance(entry, list) else '?'}"
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
