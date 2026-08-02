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
    """JSON-файл владельца не найден."""
    JSON_PARSE_ERROR = "json_parse_error"
    """JSON-файл не удалось прочитать/разобрать."""
    HEADER_MISSING = "header_missing"
    """JSON-файл не содержит секции ``header``."""
    VERSION_UNSUPPORTED = "version_unsupported"
    """Raw-формат header не распознан."""


@dataclass
class DecodeResult:
    """Cтруктурированный результат декодирования."""
    ok: bool
    """``True`` — декодирование успешно или частично; ``False`` — ошибка."""
    data: dict
    """Cтруктура ``{"Properties": [...], "TabularSections": [...]}``."""
    error: DecodeError | None = None
    """Cтатус ошибки, если ``ok=False``."""
    warnings: list[str] = field(default_factory=list)
    """Cписок некритичных предупреждений (partial decode)."""


_EMPTY_DATA: dict = {"Properties": [], "TabularSections": []}


def _fail(error: DecodeError, msg: str) -> DecodeResult:
    return DecodeResult(ok=False, data=_EMPTY_DATA.copy(), error=error, warnings=[msg])


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def decode_object_attributes(object_json: Path) -> DecodeResult:
    """Cчитать raw-``header`` из файла объекта метаданных и вернуть
    нормализованную структуру Properties + TabularSections.

    Parameters
    ----------
    object_json:
        Путь к JSON-файлу объекта, например
        ``cf_export/Catalog/Банки/Catalog.json``.

    Returns
    -------
    :class:`DecodeResult` с заполненными полями при успехе или
    ``ok=False`` при критической ошибке.
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

    _walk_header(header, properties, tabular_sections, warnings)

    return DecodeResult(
        ok=True,
        data={"Properties": properties, "TabularSections": tabular_sections},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal raw-header walker
# ---------------------------------------------------------------------------

_NULL_UUID = "00000000-0000-0000-0000-000000000000"


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    parts = value.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


def _unquote(value: object) -> str | None:
    """Cнять окружающие кавычки 1С-строки, например '"City"' → 'City'."""
    if not isinstance(value, str) or len(value) < 2:
        return None
    if value[0] == '"' and value[-1] == '"':
        return value[1:-1].strip() or None
    return value.strip() or None


def _extract_synonym_from_node(node: Any) -> str | None:
    """Извлечь синоним из узла вида ``[0, 0, [0, '"ru"', '"..."\']]``.

    Узел синонима реализует структуру: list[цифра, цифра, list[цифра, язык, строка]]
    или скалярную строку.
    """
    if node is None:
        return None
    if isinstance(node, str):
        return _unquote(node)
    if not isinstance(node, list):
        return None
    # [0, 0, [0, '"ru"', '"..."\']] — синоним лежит в node[2][2]
    if len(node) >= 3 and isinstance(node[2], list) and len(node[2]) >= 3:
        return _unquote(node[2][2])
    # Фоллбэк: ищем первую цитируемую строку
    for item in node:
        if isinstance(item, str) and item.startswith('"') and item.endswith('"'):
            return _unquote(item)
    return None


def _extract_type_from_node(node: Any) -> str | None:
    """Извлечь тип из узла вида ``[0, '"String"']``.

    Узел типа: list[цифра, строка] или простая строка.
    """
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
    """Попытка декодировать одну запись реквизита raw-header.

    Ожидаемая структура (0-индексация):
        entry[0]  — тег (0)
        entry[1]  — UUID-блок: [0, 0, "<uuid>"]
        entry[2]  — имя: '"<Имя>"'
        entry[3]  — тип: [0, '"<Тип>"'] (None если неизвестен)
        entry[4]  — синоним: [0, 0, [0, '"ru"', '"<Синоним>"']]
    """
    if not isinstance(entry, list) or len(entry) < 3:
        warnings.append(
            f"object_decoder: повреждённый узел реквизита: {type(entry).__name__}, len={getattr(entry, '__len__', lambda: '?')()}"
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
    """Декодировать список реквизитов из prop-блока.

    prop-блок: [0, [entry1, entry2, ...]] — индекс-0 аналогичен в header.
    """
    result: list[dict] = []
    if not isinstance(prop_list_node, list):
        return result
    # Список записей начинается с индекса 1 (index 0 — тег)
    for i, entry in enumerate(prop_list_node):
        if i == 0:
            continue  # тег
        if entry is None:
            continue
        decoded = _try_decode_prop_entry(entry, warnings)
        if decoded is not None:
            result.append(decoded)
    return result


def _try_decode_ts_entry(
    entry: Any, warnings: list[str]
) -> dict | None:
    """Попытка декодировать запись Табличной части.

    Ожидаемая структура:
        entry[0]  — тег (0)
        entry[1]  — UUID-блок ТЧ: [0, 0, "<uuid>"]
        entry[2]  — имя ТЧ: '"<Имя>"'
        entry[3]  — синоним ТЧ: [0, 0, [0, '"ru"', '"<Синоним>"']]
        entry[4]  — prop-блок реквизитов ТЧ: [0, [entry1, ...]]
    """
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


def _walk_header(
    header: list,
    properties: list[dict],
    tabular_sections: list[dict],
    warnings: list[str],
) -> None:
    """Обход raw-header с поиском блоков Properties и TabularSections.

    Паттерн подтверждён на header[0][6][i] в Catalog.json.
    Функция работает best-effort: проходит все списки рекурсивно
    и определяет блок-тип по структуре элементов.
    """
    _walk_node(header, properties, tabular_sections, warnings, depth=0)


_MAX_DEPTH = 20


def _looks_like_prop_list(node: list) -> bool:
    """Узел похож на список реквизитов: [0, [entry...], [entry...]...]

    Признаки:
    - первый элемент == 0 (тег)
    - хотя бы один дочерний список с длиной ≥ 3
    - второй дочерний элемент содержит UUID-блок в [1][2]
    """
    if not isinstance(node, list) or len(node) < 2 or node[0] != 0:
        return False
    for item in node[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        uuid_block = item[1] if len(item) > 1 else None
        if isinstance(uuid_block, list) and len(uuid_block) >= 3:
            if _is_uuid(uuid_block[2]) and uuid_block[2] != _NULL_UUID:
                return True
    return False


def _looks_like_ts_list(node: list) -> bool:
    """Узел похож на список ТЧ: как prop-список, но внутренний элемент
    имеет дополнительный признак: entry[4] является списком
    (список реквизитов ТЧ).
    """
    if not _looks_like_prop_list(node):
        return False
    for item in node[1:]:
        if not isinstance(item, list) or len(item) < 5:
            continue
        if isinstance(item[4], list):
            return True
    return False


_props_seen: set[int] = set()
_ts_seen: set[int] = set()


def _walk_node(
    node: Any,
    properties: list[dict],
    tabular_sections: list[dict],
    warnings: list[str],
    depth: int,
) -> None:
    if depth > _MAX_DEPTH or not isinstance(node, list):
        return
    node_id = id(node)

    if _looks_like_ts_list(node) and node_id not in _ts_seen:
        _ts_seen.add(node_id)
        for i, entry in enumerate(node):
            if i == 0:
                continue
            decoded = _try_decode_ts_entry(entry, warnings)
            if decoded is not None:
                tabular_sections.append(decoded)
        return

    if _looks_like_prop_list(node) and node_id not in _props_seen:
        _props_seen.add(node_id)
        for i, entry in enumerate(node):
            if i == 0:
                continue
            decoded = _try_decode_prop_entry(entry, warnings)
            if decoded is not None:
                properties.append(decoded)
        return

    for child in node:
        _walk_node(child, properties, tabular_sections, warnings, depth + 1)
