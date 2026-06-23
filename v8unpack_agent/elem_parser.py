"""Парсер elem.json обычной формы 1С.

Цель модуля — получить структурную выжимку для агентного индекса:
элементы формы, типы, родительские связи, page, обработчики и привязки данных.

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
_CHILD_KEYS = ("items", "Items", "children", "Children", "elements", "Elements")
_HANDLER_KEYS = ("handler", "Обработчик", "event_handler", "Action", "Действие")
_DATA_PATH_KEYS = ("data_path", "ПутьКДанным", "dataPath", "binding", "Привязка")


def parse_elem_json(form_root: Path) -> ElemIndexResult:
    warnings: list[str] = []

    elem_path = _find_elem_json(form_root)
    if elem_path is None:
        return ElemIndexResult(
            elem_index_ok=False,
            elements=[],
            warnings=[f"elem.json не найден в {form_root}"],
        )

    try:
        data = json.loads(elem_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return ElemIndexResult(
            elem_index_ok=False,
            elements=[],
            warnings=[f"Не удалось прочитать {elem_path}: {exc}"],
        )

    try:
        elements = _extract_elements(data, warnings)
    except Exception as exc:
        return ElemIndexResult(
            elem_index_ok=False,
            elements=[],
            warnings=[f"Не удалось разобрать {elem_path}: {exc}"],
        )

    if not elements:
        warnings.append(f"Элементы формы не найдены в {elem_path}")
        return ElemIndexResult(elem_index_ok=False, elements=[], warnings=warnings)

    _normalize_parents(elements, warnings)
    _attach_handlers_from_bsl(form_root, elements, warnings)

    index_path = form_root / "form_elements_index.json"
    try:
        index_path.write_text(
            json.dumps({"form": form_root.name, "elements": elements}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        warnings.append(f"Не удалось записать {index_path}: {exc}")

    return ElemIndexResult(elem_index_ok=True, elements=elements, warnings=warnings)


def _find_elem_json(form_root: Path) -> Path | None:
    direct = sorted(form_root.glob("*.elem.json"))
    if direct:
        return direct[0]

    recursive = sorted(form_root.rglob("*.elem.json"))
    if recursive:
        return recursive[0]

    return None


def _extract_elements(data: Any, warnings: list[str]) -> list[dict]:
    raw_nodes: list[tuple[dict, str | None]] = []
    _walk_json(data, parent_name=None, out=raw_nodes)

    elements: list[dict] = []
    for node, fallback_parent in raw_nodes:
        element_type = _first_value(node, _ELEMENT_TYPE_KEYS)
        name = _first_value(node, _NAME_KEYS)
        page = _first_value(node, _PAGE_KEYS)
        handler = _first_value(node, _HANDLER_KEYS)
        data_path = _first_value(node, _DATA_PATH_KEYS)

        if not element_type and not name:
            continue

        element = {
            "name": str(name) if name is not None else "",
            "type": str(element_type) if element_type is not None else "Unknown",
            "parent": _detect_parent(node, fallback_parent),
            "page": page,
        }

        if handler:
            element["handler"] = str(handler)

        if data_path:
            element["data_path"] = str(data_path)

        elements.append(element)

    return _deduplicate_elements(elements)


def _walk_json(value: Any, parent_name: str | None, out: list[tuple[dict, str | None]]) -> None:
    if isinstance(value, dict):
        name = _first_value(value, _NAME_KEYS)
        element_type = _first_value(value, _ELEMENT_TYPE_KEYS)

        current_parent = parent_name
        if name or element_type:
            out.append((value, parent_name))
            if name:
                current_parent = str(name)

        for key, child in value.items():
            if key in _CHILD_KEYS:
                _walk_json(child, current_parent, out)
            elif isinstance(child, (dict, list)):
                _walk_json(child, current_parent, out)

    elif isinstance(value, list):
        for item in value:
            _walk_json(item, parent_name, out)


def _first_value(node: dict, keys: tuple[str, ...]) -> Any:
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
    seen: set[tuple[str, str, str | None, Any]] = set()

    for element in elements:
        key = (
            element.get("name", ""),
            element.get("type", ""),
            element.get("parent"),
            element.get("page"),
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

        candidates = [
            f"{name}Нажатие",
            f"{name}ПриИзменении",
            f"{name}ПриАктивизации",
            f"{name}Выбор",
        ]

        for candidate in candidates:
            if candidate in procedures:
                element["handler"] = candidate
                break


def _find_form_bsl(form_root: Path) -> Path | None:
    candidates = [
        form_root / "Form.obj.bsl",
        form_root / "ReportForm.obj.bsl",
        form_root / "Ext" / "ObjectModule.bsl",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    found = sorted(form_root.rglob("*.bsl"))
    return found[0] if found else None