"""Синтетические фикстуры управляемой формы для конвейера №2c.

Хелперы автономны (только stdlib), не зависят от продакшн-модулей.
Генерируют *.elem.json совместимый с parse_elem_json из elem_parser.py:
секции data (ключи-пути вида «Страница1/Панель/Кнопка», список -pages-),
tree, props, commands, params.
Пути строятся через pathlib — без литеральных \\ и абсолютных путей в коде.
"""

from __future__ import annotations

import json
from pathlib import Path

_PAGE_LIST_KEY = "-pages-"

# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def make_managed_form_elem_json(
    pages: list[str] | None = None,
    props: list[dict] | None = None,
    commands: list[dict] | None = None,
    params: list[dict] | None = None,
    *,
    with_noise: bool = True,
) -> dict:
    """Возвращает dict, сериализуемый в JSON (совместим с parse_elem_json).

    Детерминизм: одинаковые аргументы → побайтово одинаковый json.dumps.

    Args:
        pages: имена страниц формы. По умолчанию ["Страница1"].
        props: список свойств (секция props). Если None — синтетический.
        commands: список команд (секция commands). Если None — синтетический.
        params: список параметров (секция params). Если None — синтетический.
        with_noise: добавить GUID-подобные id/ref, координаты,
            оформительские атрибуты — реалистичный шум.
    """
    if pages is None:
        pages = ["Страница1"]

    data: dict = {}

    # -pages- на корневом уровне
    data[_PAGE_LIST_KEY] = list(pages)

    # для каждой страницы — несколько дочерних элементов
    for i, page in enumerate(pages):
        data[page] = _make_element_entry(page, "Page", i, with_noise)
        panel_name = f"Панель{i + 1}"
        data[f"{page}/{panel_name}"] = _make_element_entry(panel_name, "Group", i * 10, with_noise)
        button_name = f"Кнопка{i + 1}"
        data[f"{page}/{panel_name}/{button_name}"] = _make_element_entry(
            button_name, "Button", i * 10 + 1, with_noise
        )
        # -pages- для страницы (суб-страницы не нужны, но ключ должен присутствовать)
        data[f"{page}/{_PAGE_LIST_KEY}"] = []

    tree: list[dict] = []
    for i, page in enumerate(pages):
        panel_name = f"Панель{i + 1}"
        button_name = f"Кнопка{i + 1}"
        node: dict = {
            "name": page,
            "type": "Page",
            "items": [
                {
                    "name": panel_name,
                    "type": "Group",
                    "items": [
                        {
                            "name": button_name,
                            "type": "Button",
                            "handler": f"{button_name}Нажатие",
                        }
                    ],
                }
            ],
        }
        if with_noise:
            node["id"] = _guid(i)
            node["ref"] = _guid(i + 100)
        tree.append(node)

    resolved_props = props if props is not None else _default_props(pages, with_noise)
    resolved_commands = commands if commands is not None else _default_commands(pages, with_noise)
    resolved_params = params if params is not None else _default_params(with_noise)

    return {
        "commands": resolved_commands,
        "data": data,
        "params": resolved_params,
        "props": resolved_props,
        "tree": tree,
    }


def write_managed_form_elem(
    root: Path,
    object_type: str,
    object_name: str,
    form_name: str,
    payload: dict,
    *,
    write_aux: bool = True,
) -> Path:
    """Раскладывает файлы формы по layout и возвращает Path к *.elem.json.

    Layout: <root>/<object_type>/<object_name>/<form_suffix>/<form_name>/
    Поддерживаемые суффиксы каталога форм: CatalogForm, Form, ReportForm.
    Имя *.elem.json: <form_name>.elem.json

    Args:
        root: корневой временный каталог.
        object_type: тип объекта, например "Catalog", "external_managed".
        object_name: имя объекта, например "Банки".
        form_name: имя формы, например "ФормаЭлементаУправляемая".
        payload: dict, возвращённый make_managed_form_elem_json().
        write_aux: если True, создаёт также *.10.json и *.obj.10.bsl.

    Returns:
        Path к созданному *.elem.json.
    """
    form_suffix = _detect_form_suffix(form_name)
    form_dir = root / object_type / object_name / form_suffix / form_name
    form_dir.mkdir(parents=True, exist_ok=True)

    elem_path = form_dir / f"{form_name}.elem.json"
    elem_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if write_aux:
        aux_json_path = form_dir / f"{form_name}.10.json"
        aux_json_path.write_text(
            json.dumps({"form": form_name, "version": 10}, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        bsl_path = form_dir / f"{form_name}.obj.10.bsl"
        lines = []
        pages = payload.get("data", {}).get("-pages-", [])
        for page in pages:
            for i in range(1, len(pages) + 1):
                lines.append(f"Процедура Кнопка{i}Нажатие(Элемент)")
                lines.append("    // синтетический обработчик")
                lines.append("КонецПроцедуры")
                lines.append("")
            break  # по одному набору процедур
        bsl_path.write_text("\n".join(lines), encoding="utf-8")

    return elem_path


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------


def _guid(seed: int) -> str:
    """Детерминированный GUID-подобный идентификатор на основе seed."""
    h = format(seed * 0x9E3779B9 & 0xFFFFFFFFFFFFFFFF, "016x")
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[0:4]}-{h[4:16]}"


def _make_element_entry(name: str, etype: str, seed: int, with_noise: bool) -> dict:
    entry: dict = {"name": name, "type": etype}
    if with_noise:
        entry["id"] = _guid(seed)
        entry["ref"] = _guid(seed + 200)
        entry["top"] = seed * 5
        entry["left"] = seed * 3
        entry["width"] = 100 + seed
        entry["height"] = 20 + seed
        entry["color"] = "#FFFFFF"
        entry["font"] = "Arial"
    return entry


def _default_props(pages: list[str], with_noise: bool) -> list[dict]:
    result = []
    for i, page in enumerate(pages):
        prop: dict = {"name": f"ЗаголовокФормы{i + 1}", "type": "String", "value": page}
        if with_noise:
            prop["id"] = _guid(i + 500)
        result.append(prop)
    return result


def _default_commands(pages: list[str], with_noise: bool) -> list[dict]:
    result = []
    for i, _ in enumerate(pages):
        cmd: dict = {
            "name": f"Команда{i + 1}",
            "type": "Command",
            "handler": f"Команда{i + 1}Действие",
        }
        if with_noise:
            cmd["id"] = _guid(i + 600)
        result.append(cmd)
    return result


def _default_params(with_noise: bool) -> list[dict]:
    param: dict = {"name": "ПараметрОтчета", "type": "AnyRef"}
    if with_noise:
        param["id"] = _guid(700)
        param["ref"] = _guid(701)
    return [param]


_FORM_SUFFIX_MAP = {
    "catalog": "CatalogForm",
    "catalogform": "CatalogForm",
    "report": "ReportForm",
    "reportform": "ReportForm",
    "form": "Form",
}


def _detect_form_suffix(form_name: str) -> str:
    """Выбирает суффикс каталога формы по имени формы (эвристика)."""
    lower = form_name.lower()
    if "catalog" in lower or "каталог" in lower or "элемент" in lower or "список" in lower:
        return "CatalogForm"
    if "report" in lower or "отчет" in lower or "отчёт" in lower:
        return "ReportForm"
    return "Form"
