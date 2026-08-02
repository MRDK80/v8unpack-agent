"""Парсер elem.json обычной формы 1С.

Цель модуля — получить структурную выжимку для агентного индекса:
элементы формы, типы, родительские связи, page, обработчики и привязки данных.

Иерархия восстанавливается из секции ``data``: ключи вида
``Страница1/ПанельВерхняя/Страница1/ПанельВложенная/Страница11/Кнопка``
кодируют полную, достоверную цепочку вложенности для панелей и страниц
(включая пустые страницы из ключей ``-pages-``). Для групп распаковщик кладёт
ключи ``data`` плоско — вложенность групп в путях НЕ представлена (см.
``_warn_unresolved_group_hierarchy``). Если секции ``data`` нет — используется
фолбэк-обход по секциям ``tree``/``props``/``commands``/``params``.

Привязка к данным (``data_path``) декодируется тремя способами:

* обычные формы — источник данных назван в поле ``prop`` записи ``data``,
  имя реквизита лежит в ``raw[4][1]``. Если оба совпадают, элемент связан
  с самостоятельным реквизитом формы и путь состоит из одного имени;
  иначе путь имеет вид ``<prop>.<реквизит>`` (например
  ``СправочникОбъект.Город``). Записи без ``prop`` привязки не имеют:
  маркер ``raw[4][0] == "14"`` присутствует и у надписей, и у разделителей,
  поэтому опираться на него нельзя;
* управляемые формы — ни ``prop``, ни строки-пути нет, привязка задана
  UUID реквизита объекта-владельца. UUID разрешается через карту
  ``uuid -> имя реквизита``, построенную из ``Catalog.json``/``Document.json``
  соседнего объекта метаданных. Служебные UUID (самой формы, элементов
  оформления) в карте отсутствуют и отсеиваются автоматически;
* редкий случай — путь лежит готовой строкой с точкой внутри ``raw``.

Разбор по UUID применяется только к формам без ``prop``: в обычных формах
UUID в ``raw`` описывают класс виджета, а не привязку, и попытка их
разрешить давала ложные срабатывания.

Модуль работает best-effort: ошибка парсинга elem.json не должна ломать
основной пайплайн распаковки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Any


@dataclass
class ElemIndexResult:
    elem_index_ok: bool
    elements: list[dict]
    warnings: list[str] = field(default_factory=list)


_ELEMENT_TYPE_KEYS = ("type", "Тип", "item_type", "kind", "Вид")
_NAME_KEYS = ("name", "Имя", "caption", "Заголовок", "identifier", "Идентификатор")
_PAGE_KEYS = ("page", "Страница", "page_id", "Page")
_CHILD_KEYS = ("items", "Items", "children", "Children", "elements", "Elements", "child", "Child", "Дети")
_HANDLER_KEYS = ("handler", "Обработчик", "event_handler", "Action", "Действие")
_DATA_PATH_KEYS = ("data_path", "ПутьКДанным", "dataPath", "binding", "Привязка")
_RAW_DICT_DATA_PATH_KEYS = ("DataPath", "data_path", "ПутьКДанным", "dataPath", "binding", "Привязка")

_PAGE_LIST_KEY = "-pages-"


_NULL_UUID = "00000000-0000-0000-0000-000000000000"
_OBJECT_PREFIX = "Объект"


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    parts = value.split("-")
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


def _unquote_1c(value: object) -> str | None:
    if not isinstance(value, str) or len(value) < 3:
        return None
    if value[0] == '"' and value[-1] == '"':
        return value[1:-1].strip() or None
    return None


def _collect_attribute_uuid_map(node: object, out: dict[str, str]) -> None:
    """UUID реквизита и его имя лежат рядом: node[1][2] и node[2].

    Инвариант подтверждён на header[0][6][i][0][1][1][1] в Catalog.json.
    """
    if isinstance(node, list):
        if len(node) >= 3 and isinstance(node[1], list) and len(node[1]) >= 3:
            uuid = node[1][2]
            name = _unquote_1c(node[2])
            if _is_uuid(uuid) and uuid != _NULL_UUID and name:
                out.setdefault(uuid, name)
        for child in node:
            _collect_attribute_uuid_map(child, out)
    elif isinstance(node, dict):
        for child in node.values():
            _collect_attribute_uuid_map(child, out)


def _collect_uuids(node: object, out: list[str]) -> None:
    if isinstance(node, str):
        if _is_uuid(node) and node != _NULL_UUID:
            out.append(node)
    elif isinstance(node, list):
        for child in node:
            _collect_uuids(child, out)
    elif isinstance(node, dict):
        for child in node.values():
            _collect_uuids(child, out)


def _is_common_form(form_root: Path) -> bool:
    """Общая форма не имеет объекта-владельца и его карты реквизитов."""
    return "CommonForm" in form_root.parts


def _find_owner_metadata_json(form_root: Path) -> Path | None:
    """Найти ``<Type>.json`` владельца без фиксированного списка типов.

    Для пути ``.../<Type>/<Object>/<FormKind>/<Form>`` файл владельца лежит
    в ``.../<Type>/<Object>/<Type>.json``. Это покрывает новые и редкие типы
    метаданных без обновления константы в парсере.
    """
    for parent in form_root.parents:
        if parent.parent == parent:
            break
        candidate = parent / f"{parent.parent.name}.json"
        if candidate.is_file():
            return candidate
    return None


def load_owner_attribute_map(form_root: Path, warnings: list[str]) -> dict[str, str]:
    """Карта ``uuid реквизита -> имя`` из метаданных объекта-владельца формы."""
    path = _find_owner_metadata_json(form_root)
    if path is None:
        if not _is_common_form(form_root):
            warnings.append(f"Метаданные владельца не найдены для {form_root}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        warnings.append(f"Не удалось прочитать {path}: {exc}")
        return {}
    mapping: dict[str, str] = {}
    _collect_attribute_uuid_map(payload.get("header", payload), mapping)
    return mapping


_LEGACY_BINDING_TAG = "14"
_LEGACY_NAME_POS = 4


def _legacy_attribute_name(raw: object) -> str | None:
    """Имя реквизита из ``raw[4]`` записи обычной формы.

    Узел имеет вид ``["14", "\"Город\"", "4294967295", "0", "0", "0"]``.
    Само по себе наличие тега ``"14"`` привязки не доказывает — он есть и у
    надписей; значение используется только вместе с непустым ``prop``.
    """
    if not isinstance(raw, list) or len(raw) <= _LEGACY_NAME_POS:
        return None
    node = raw[_LEGACY_NAME_POS]
    if not isinstance(node, list) or len(node) < 2:
        return None
    if node[0] != _LEGACY_BINDING_TAG:
        return None
    return _unquote_1c(node[1])


def decode_legacy_data_path(record: object) -> tuple[str | None, list[str]]:
    """data_path элемента обычной формы по полям ``prop`` и ``raw``.

    ``prop`` называет источник данных из секции ``props``. Если имя реквизита
    совпадает с ``prop``, элемент связан с самим реквизитом формы и путь
    состоит из одного имени; иначе путь — ``<prop>.<реквизит>``.
    """
    if not isinstance(record, dict):
        return None, []

    prop = record.get("prop")
    if not isinstance(prop, str) or not prop.strip():
        return None, []
    prop = prop.strip()

    attribute = _legacy_attribute_name(record.get("raw"))
    if attribute is None:
        return prop, [
            f"decode_legacy_data_path: не найдено имя реквизита в raw, prop={prop!r}"
        ]
    if attribute == prop:
        return prop, []
    return f"{prop}.{attribute}", []


def is_legacy_form_data(data_section: object) -> bool:
    """Секция ``data`` принадлежит обычной, а не управляемой форме.

    Признак — хотя бы одна запись с ключом ``prop``: в управляемых формах
    такого ключа нет. Нужен, чтобы не применять разбор по UUID там, где
    UUID описывают класс виджета.
    """
    if not isinstance(data_section, dict):
        return False
    return any(
        isinstance(value, dict) and "prop" in value
        for value in data_section.values()
    )


def decode_element_data_path(
    raw: object,
    attribute_map: dict[str, str] | None = None,
    object_prefix: str = _OBJECT_PREFIX,
    element_name: str | None = None,
    warn_empty_map: bool = True,
) -> tuple[str | None, list[str]]:
    """Декодирование data_path из raw секции data.

    Обычные формы: путь лежит строкой внутри raw.
    Управляемые формы: строки-пути нет, привязка задана UUID реквизита
    владельца (например raw[11][2][1]); он разрешается через attribute_map,
    построенную из Catalog.json/Document.json. Служебные UUID (форма,
    оформление) в карте отсутствуют и отсеиваются автоматически.

    Ограничение: префикс «Объект.» верен для форм элемента/группы. Для форм
    списка привязка идёт к реквизиту динамического списка.
    """
    if raw is None:
        return None, []

    if isinstance(raw, str):
        stripped = raw.strip()
        return (stripped, []) if stripped else (None, [])

    if isinstance(raw, dict):
        for key in _RAW_DICT_DATA_PATH_KEYS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), []
        return None, [
            f"decode_element_data_path: raw-словарь без data_path, ключи={list(raw.keys())!r}"
        ]

    if not isinstance(raw, list):
        return None, [f"decode_element_data_path: неизвестный тип raw={type(raw).__name__!r}"]

    for item in raw:
        if isinstance(item, list):
            for sub in item:
                if isinstance(sub, str) and "." in sub and sub.strip():
                    return sub.strip(), []
        elif isinstance(item, str) and "." in item and item.strip():
            return item.strip(), []

    if not attribute_map:
        warnings = ["decode_element_data_path: карта реквизитов владельца пуста"]
        return None, warnings if warn_empty_map else []

    uuids: list[str] = []
    _collect_uuids(raw, uuids)
    matched = sorted({attribute_map[u] for u in uuids if u in attribute_map})

    if len(matched) == 1:
        return f"{object_prefix}.{matched[0]}", []
    if len(matched) > 1:
        exact = [candidate for candidate in matched if candidate == element_name]
        if len(exact) == 1:
            return f"{object_prefix}.{exact[0]}", []
        context = f", элемент={element_name!r}" if element_name else ""
        return None, [
            f"decode_element_data_path: неоднозначная привязка{context}, "
            f"кандидаты={matched!r}"
        ]
    return None, []


def parse_elem_json(form_root: Path) -> ElemIndexResult:
    warnings: list[str] = []

    elem_path = _find_elem_json(form_root)
    if elem_path is None:
        return ElemIndexResult(False, [], [f"elem.json не найден в {form_root}"])

    try:
        data = json.loads(elem_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return ElemIndexResult(False, [], [f"Не удалось прочитать {elem_path}: {exc}"])

    attribute_map = load_owner_attribute_map(form_root, warnings)

    try:
        elements = _extract_elements(
            data, warnings, attribute_map,
            suppress_empty_map_warning=_is_common_form(form_root),
        )
    except Exception as exc:
        return ElemIndexResult(False, [], [f"Не удалось разобрать {elem_path}: {exc}"])

    if not elements:
        warnings.append(f"Элементы формы не найдены в {elem_path}")
        return ElemIndexResult(False, [], warnings)

    _normalize_parents(elements, warnings)
    _warn_unresolved_group_hierarchy(elements, warnings)
    _attach_handlers_from_bsl(form_root, elements, warnings)

    index_path = form_root / "form_elements_index.json"
    try:
        index_path.write_text(
            json.dumps({"form": form_root.name, "elements": elements}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        warnings.append(f"Не удалось записать {index_path}: {exc}")

    return ElemIndexResult(True, elements, warnings)


def _find_elem_json(form_root: Path) -> Path | None:
    direct = sorted(form_root.glob("*.elem.json"))
    if direct:
        return direct[0]
    recursive = sorted(form_root.rglob("*.elem.json"))
    return recursive[0] if recursive else None


def _extract_elements(
    data: Any,
    warnings: list[str],
    attribute_map: dict[str, str] | None = None,
    suppress_empty_map_warning: bool = False,
) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        tree_meta = _types_from_tree(data.get("tree", []))
        props = _extract_props(data.get("props"))
        form_attribute_names = {
            element["name"]
            for element in props
            if element.get("name")
        }
        elements = _extract_from_data_paths(
            data["data"], tree_meta, warnings, attribute_map,
            form_attribute_names=form_attribute_names,
            suppress_empty_map_warning=suppress_empty_map_warning,
        )
        elements.extend(props)
        if elements:
            return _merge_source_duplicates(_deduplicate_elements(elements))

    raw_nodes: list[tuple[dict, str | None, str]] = []
    if isinstance(data, dict):
        for section in ("tree", "props", "commands", "params"):
            if section in data:
                _walk_json(data[section], None, raw_nodes, section)
        if not raw_nodes:
            _walk_json(data, None, raw_nodes, "unknown")
    else:
        _walk_json(data, None, raw_nodes, "unknown")

    elements = []
    for node, fallback_parent, source in raw_nodes:
        element_type = _first_value(node, _ELEMENT_TYPE_KEYS)
        name = _first_value(node, _NAME_KEYS)
        if not element_type and not name:
            continue
        element = {
            "name": str(name) if name is not None else "",
            "type": str(element_type) if element_type is not None else "Unknown",
            "parent": _detect_parent(node, fallback_parent),
            "parent_path": None,
            "path": None,
            "page": _first_value(node, _PAGE_KEYS),
            "source": source,
        }
        handler = _first_value(node, _HANDLER_KEYS)
        if handler:
            element["handler"] = str(handler)
        data_path = _first_value(node, _DATA_PATH_KEYS)
        if data_path:
            element["data_path"] = str(data_path)
        elements.append(element)

    return _deduplicate_elements(elements)


def _types_from_tree(tree_section: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    nodes: list = []
    _walk_json(tree_section, None, nodes, "tree")
    for node, _parent, _src in nodes:
        name = _first_value(node, _NAME_KEYS)
        if not name:
            continue
        info: dict = {"type": _first_value(node, _ELEMENT_TYPE_KEYS)}
        handler = _first_value(node, _HANDLER_KEYS)
        if handler:
            info["handler"] = str(handler)
        data_path = _first_value(node, _DATA_PATH_KEYS)
        if data_path:
            info["data_path"] = str(data_path)
        out[str(name)] = info
    return out



def _managed_structural_data_path(
    full_path: str,
    element_name: str,
    form_attribute_names: set[str],
) -> str | None:
    """Консервативный fallback для привязок управляемой формы.

    Точное совпадение означает самостоятельный реквизит формы.
    Для колонки таблицы непосредственный родитель пути должен быть
    реквизитом формы; имя колонки может включать имя таблицы как префикс.
    """
    if element_name in form_attribute_names:
        return element_name

    parts = [part for part in full_path.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[-2]
    if owner not in form_attribute_names:
        return None

    column = (
        element_name[len(owner):]
        if element_name.startswith(owner)
        else element_name
    )
    if not column:
        return None

    return f"{owner}.{column}"


def _is_element_record(value: object) -> bool:
    """Запись секции ``data`` описывает элемент формы.

    Обычные формы: присутствует ключ ``id``.
    Управляемые формы: ``id`` отсутствует, запись имеет вид
    ``{"raw": [...], "ver": ...}``.
    """
    if not isinstance(value, dict):
        return False
    return "id" in value or "raw" in value


def _extract_from_data_paths(
    data_section: dict,
    tree_meta: dict[str, dict],
    warnings: list[str] | None = None,
    attribute_map: dict[str, str] | None = None,
    form_attribute_names: set[str] | None = None,
    suppress_empty_map_warning: bool = False,
) -> list[dict]:
    """Извлекает элементы из секции data, включая декодирование data_path
    из поля raw каждой записи (issue #85).

    Записи секции ``data`` в обычных формах содержат ключ ``id``; в
    управляемых формах ключа ``id`` нет — есть ``raw``/``ver``. Поэтому
    запись считается элементом при наличии любого из них.
    """
    if warnings is None:
        warnings = []
    if form_attribute_names is None:
        form_attribute_names = set()
    elements: list[dict] = []
    seen_paths: set[str] = set()

    legacy = is_legacy_form_data(data_section)
    if (
        not legacy
        and not attribute_map
        and not suppress_empty_map_warning
        and not any(
            "карта реквизитов владельца пуста" in warning
            for warning in warnings
        )
    ):
        warnings.append("decode_element_data_path: карта реквизитов владельца пуста")

    # Записи по имени элемента для последующего декодирования привязки
    record_by_element_name: dict[str, dict] = {}
    for key, value in data_section.items():
        if key in (_PAGE_LIST_KEY,) or key.endswith("/" + _PAGE_LIST_KEY):
            continue
        if not _is_element_record(value):
            continue
        name = key.rstrip("/").split("/")[-1]
        if "raw" in value:
            record_by_element_name[name] = value

    def make(name: str, full_path: str, etype: str) -> dict:
        parts = full_path.rstrip("/").split("/")
        parent_parts = parts[:-1]
        el = {
            "name": name,
            "type": etype,
            "parent": parent_parts[-1] if parent_parts else None,
            "parent_path": "/".join(parent_parts) if parent_parts else None,
            "path": full_path,
            "page": parent_parts[-1] if parent_parts else None,
            "source": "data",
        }
        meta = tree_meta.get(name, {})
        if meta.get("handler"):
            el["handler"] = meta["handler"]
        # data_path: сначала из tree_meta (если явно прописан в tree),
        # затем best-effort из raw секции data
        if meta.get("data_path"):
            el["data_path"] = meta["data_path"]
        elif name in record_by_element_name:
            record = record_by_element_name[name]
            if legacy:
                decoded, decode_warnings = decode_legacy_data_path(record)
            else:
                decoded, decode_warnings = decode_element_data_path(
                    record.get("raw"), attribute_map,
                    element_name=name, warn_empty_map=False,
                )
            warnings.extend(decode_warnings)
            if decoded is None and not legacy:
                decoded = _managed_structural_data_path(
                    full_path,
                    name,
                    form_attribute_names,
                )
            if decoded is not None:
                el["data_path"] = decoded
        return el

    for key, value in data_section.items():
        if key == _PAGE_LIST_KEY:
            owner_path = ""
        elif key.endswith("/" + _PAGE_LIST_KEY):
            owner_path = key[: -(len(_PAGE_LIST_KEY) + 1)]
        else:
            continue
        if not isinstance(value, list):
            continue
        for page_name in value:
            full = f"{owner_path}/{page_name}" if owner_path else page_name
            if full in seen_paths:
                continue
            seen_paths.add(full)
            elements.append(make(page_name, full, "Page"))

    for key, value in data_section.items():
        if key == _PAGE_LIST_KEY or key.endswith("/" + _PAGE_LIST_KEY):
            continue
        if not _is_element_record(value):
            continue
        if key in seen_paths:
            continue
        seen_paths.add(key)
        name = key.rstrip("/").split("/")[-1]
        etype = str(tree_meta.get(name, {}).get("type") or "Unknown")
        elements.append(make(name, key, etype))

    return elements


def _extract_props(props_section: Any) -> list[dict]:
    result: list[dict] = []
    if not isinstance(props_section, list):
        return result
    nodes: list = []
    _walk_json(props_section, None, nodes, "props")
    for node, _parent, _src in nodes:
        name = _first_value(node, _NAME_KEYS)
        if not name:
            continue
        result.append({
            "name": str(name),
            "type": str(_first_value(node, _ELEMENT_TYPE_KEYS) or "Unknown"),
            "parent": None,
            "parent_path": None,
            "path": None,
            "page": None,
            "source": "props",
        })
    return result


def _walk_json(value: Any, parent_name: str | None, out: list, source: str) -> None:
    if isinstance(value, dict):
        name = _first_value(value, _NAME_KEYS)
        element_type = _first_value(value, _ELEMENT_TYPE_KEYS)
        current_parent = parent_name
        if name or element_type:
            out.append((value, parent_name, source))
            if name:
                current_parent = str(name)
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                _walk_json(child, current_parent, out, source)
    elif isinstance(value, list):
        for item in value:
            _walk_json(item, parent_name, out, source)


def _first_value(node: dict, keys: tuple[str, ...]) -> Any:
    if not isinstance(node, dict):
        return None
    for key in keys:
        if key in node and node[key] not in ("", None):
            return node[key]
    return None


def _detect_parent(node: dict, fallback_parent: str | None) -> str | None:
    for key in ("parent", "Parent", "Родитель", "parent_name", "parentName"):
        if key in node and node[key]:
            return str(node[key])
    return fallback_parent


def _deduplicate_elements(elements: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set = set()
    for element in elements:
        key = (
            element.get("name", ""), element.get("type", ""),
            element.get("path"), element.get("parent_path"), element.get("source"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(element)
    return result


_SOURCE_PRIORITY = {"data": 0, "props": 1, "tree": 2, "commands": 3, "params": 4}


def _merge_source_duplicates(elements: list[dict]) -> list[dict]:
    """Схлопывает записи об одном элементе, пришедшие из разных секций.

    В формах списка имя реквизита формы совпадает с именем элемента, поэтому
    один элемент приходит и из ``data`` (с привязкой), и из ``props`` (без
    неё). Побеждает запись из ``data``: она несёт path, page и data_path.
    Недостающие поля добираются из проигравших записей, чтобы не потерять
    сведения, которых в ``data`` нет.

    Схлопывание идёт только по элементам без ``path`` у проигравшего: записи
    ``props`` позиции в дереве не имеют. Два разных элемента с одинаковым
    именем, но разными путями в ``data``, остаются раздельными.
    """
    best: dict[str, dict] = {}
    order: list[str] = []
    extra: list[dict] = []

    for element in elements:
        name = element.get("name") or ""
        source = element.get("source") or ""
        if not name or (source != "data" and element.get("path")):
            extra.append(element)
            continue

        current = best.get(name)
        if current is None:
            best[name] = element
            order.append(name)
            continue

        if element.get("path") and current.get("path") and (
            element["path"] != current["path"]
        ):
            extra.append(element)
            continue

        rank_new = _SOURCE_PRIORITY.get(source, 99)
        rank_old = _SOURCE_PRIORITY.get(current.get("source") or "", 99)
        winner, loser = (
            (element, current) if rank_new < rank_old else (current, element)
        )
        for key, value in loser.items():
            if winner.get(key) in (None, "", "Unknown") and value not in (None, ""):
                winner[key] = value
        merged = winner.setdefault("merged_sources", [])
        for candidate in (current.get("source"), source):
            if candidate and candidate not in merged:
                merged.append(candidate)
        best[name] = winner

    return [best[name] for name in order] + extra


def _normalize_parents(elements: list[dict], warnings: list[str]) -> None:
    known_names = {e["name"] for e in elements if e.get("name")}
    for element in elements:
        parent = element.get("parent")
        if parent and parent not in known_names:
            warnings.append(
                f"Родитель '{parent}' для элемента '{element.get('name')}' не найден в индексе"
            )


def _warn_unresolved_group_hierarchy(elements: list[dict], warnings: list[str]) -> None:
    """Честный признак неполноты для групп.

    Иерархия панелей/страниц достоверна из путей-ключей data. Для групп же
    распаковщик кодирует дерево в бинарном слое ('Дочерние элементы отдельно'),
    а ключи data остаются плоскими — поэтому parent групп и их детей
    указывает на страницу, а не на группу.

    Зацепки на вложенность групп присутствуют в raw/info-векторах элементов
    data (позиционные списки дочерних числовых id). Однако raw — это
    недокументированный внутренний формат платформы 1С, и его декодирование
    выходит за рамки разбора публичных артефактов распаковки и лицензионно
    некорректно. Поэтому вложенность групп намеренно НЕ реконструируется.

    Срабатывает, когда в форме есть группы, но ни один элемент не ссылается
    parent-ом на группу.
    """
    group_names = {e["name"] for e in elements if e.get("type") == "Group"}
    if not group_names:
        return
    any_child_of_group = any(e.get("parent") in group_names for e in elements)
    if not any_child_of_group:
        warnings.append(
            "вложенность групп не восстановлена: дерево групп хранится в "
            "бинарном слое распаковки ('Дочерние элементы отдельно'), а ключи "
            "data для групп плоские — parent групп указывает на страницу. "
            "Зацепки на связь группа->группа есть в raw/info-векторах "
            "(позиционные списки дочерних id), но raw — недокументированный "
            "внутренний формат 1С; его декодирование не выполняется по "
            "лицензионным соображениям (вне разбора публичных артефактов)."
        )


def _attach_handlers_from_bsl(form_root: Path, elements: list[dict], warnings: list[str]) -> None:
    bsl_path = _find_form_bsl(form_root)
    if bsl_path is None:
        return
    try:
        text = bsl_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        warnings.append(f"Не удалось прочитать BSL-модуль {bsl_path}: {exc}")
        return

    procedures = set(re.findall(r"(?im)^\s*Процедура\s+([А-Яа-яA-Za-z0-9_]+)\s*\(", text))
    for element in elements:
        if element.get("handler"):
            continue
        name = element.get("name") or ""
        if not name:
            continue
        for candidate in (f"{name}Нажатие", f"{name}ПриИзменении", f"{name}ПриАктивизации", f"{name}Выбор"):
            if candidate in procedures:
                element["handler"] = candidate
                break


def _find_form_bsl(form_root: Path) -> Path | None:
    for candidate in (
        form_root / "Form.obj.bsl",
        form_root / "ReportForm.obj.bsl",
        form_root / "Ext" / "ObjectModule.bsl",
    ):
        if candidate.exists():
            return candidate
    found = sorted(form_root.rglob("*.bsl"))
    return found[0] if found else None
