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

_PAGE_LIST_KEY = "-pages-"


def parse_elem_json(form_root: Path) -> ElemIndexResult:
    warnings: list[str] = []

    elem_path = _find_elem_json(form_root)
    if elem_path is None:
        return ElemIndexResult(False, [], [f"elem.json не найден в {form_root}"])

    try:
        data = json.loads(elem_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return ElemIndexResult(False, [], [f"Не удалось прочитать {elem_path}: {exc}"])

    try:
        elements = _extract_elements(data, warnings)
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


def _extract_elements(data: Any, warnings: list[str]) -> list[dict]:
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        tree_meta = _types_from_tree(data.get("tree", []))
        elements = _extract_from_data_paths(data["data"], tree_meta)
        elements.extend(_extract_props(data.get("props")))
        if elements:
            return _deduplicate_elements(elements)

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


def _extract_from_data_paths(data_section: dict, tree_meta: dict[str, dict]) -> list[dict]:
    elements: list[dict] = []
    seen_paths: set[str] = set()

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
        if meta.get("data_path"):
            el["data_path"] = meta["data_path"]
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
        if not isinstance(value, dict) or "id" not in value:
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
