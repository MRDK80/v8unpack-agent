"""catalog_resolver — резолюция data_path-привязок через Catalog.json.

Резолвит строки вида ``"Объект.Реквизит"`` или ``"Объект.ТЧ.Реквизит"``
по файлу ``<ObjectType>/<ObjectName>/<ObjectName>.json`` в выгрузке конфигурации.

Поддерживаемые форматы data_path
---------------------------------
``Объект.Реквизит``
    Реквизит верхнего уровня объекта.

``Объект.ТабличнаяЧасть.Реквизит``
    Реквизит внутри табличной части объекта.

Best-effort: если файл отсутствует, путь нераспознан или произошла любая
ошибка — возвращается :class:`ResolvedBinding` с ``resolved=False``.
Исключения не пробрасываются.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v8unpack_agent.scan_forms import FormEntry


@dataclass(frozen=True)
class ResolvedBinding:
    """Результат резолюции одного data_path."""

    data_path: str
    """Исходная строка привязки, например ``"Объект.Реквизит"``."""

    object_type: str
    """Тип объекта метаданных, например ``"Catalog"``."""

    attribute_name: str
    """Имя реквизита (последний сегмент пути)."""

    value_type: str | None
    """Строковое представление типа реквизита из ``Catalog.json``, или ``None``."""

    synonym: str | None
    """Синоним реквизита из ``Catalog.json``, или ``None``."""

    resolved: bool
    """``True`` — резолюция успешна; ``False`` — partial/failed (best-effort)."""


def resolve_data_path(
    data_path: str,
    object_json: Path,
) -> ResolvedBinding:
    """Резолвировать одну data_path-строку по файлу объекта метаданных.

    Parameters
    ----------
    data_path:
        Строка привязки вида ``"Объект.Реквизит"`` или
        ``"Объект.ТЧ.Реквизит"``.
    object_json:
        Путь к JSON-файлу объекта (например ``Catalog/Банки/Catalog.json``).

    Returns
    -------
    :class:`ResolvedBinding` с заполненными полями при успехе или
    ``resolved=False`` при любой ошибке.
    """
    parts = data_path.split(".")

    # Определяем attribute_name и object_type по лучшей догадке
    # до чтения файла, чтобы вернуть максимум информации при ошибке.
    attribute_name = parts[-1] if parts else data_path
    object_type = _infer_object_type(object_json)

    _unresolved = ResolvedBinding(
        data_path=data_path,
        object_type=object_type,
        attribute_name=attribute_name,
        value_type=None,
        synonym=None,
        resolved=False,
    )

    # Нужно минимум 2 сегмента: Объект.Реквизит
    if len(parts) < 2:  # noqa: PLR2004
        return _unresolved

    try:
        if not object_json.exists():
            return _unresolved

        raw = json.loads(object_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _unresolved

    try:
        # Формат: Объект.Реквизит  → parts = [obj, attr]
        # Формат: Объект.ТЧ.Реквизит → parts = [obj, tab_part, attr]
        if len(parts) == 2:  # noqa: PLR2004
            attr_key = parts[1]
            section = _get_attributes_section(raw)
        else:
            # Вложенный путь: ищем табличную часть, затем её реквизиты
            tab_part_name = parts[1]
            attr_key = parts[2]
            section = _get_tabular_attributes_section(raw, tab_part_name)

        if section is None:
            return _unresolved

        attr_record = _find_attribute(section, attr_key)
        if attr_record is None:
            return _unresolved

        value_type = _extract_value_type(attr_record)
        synonym = _extract_synonym(attr_record)

        return ResolvedBinding(
            data_path=data_path,
            object_type=object_type,
            attribute_name=attr_key,
            value_type=value_type,
            synonym=synonym,
            resolved=True,
        )
    except Exception:  # noqa: BLE001
        return _unresolved


def object_json_path(form_entry: "FormEntry") -> Path | None:
    """Найти JSON-файл объекта по ``FormEntry``.

    Поднимается на 2 уровня вверх от ``form_entry.form_path``
    (``<ObjectType>/<ObjectName>/<ContainerName>/<FormName>`` → верхушка
    ``<ObjectType>/<ObjectName>``) и ищет ``.json`` файл с именем, совпадающим
    с именем директории объекта (например ``Catalog.json`` или ``Банки.json``).

    Returns
    -------
    :class:`~pathlib.Path` к JSON-файлу объекта или ``None``.
    """
    try:
        # form_path: .../cf_export/<ObjectType>/<ObjectName>/<ContainerName>/<FormName>
        # -2 уровня вверх → .../cf_export/<ObjectType>/<ObjectName>
        object_dir = Path(form_entry.form_path).parents[1]
        if not object_dir.is_dir():
            return None

        object_name = object_dir.name
        candidate = object_dir / f"{object_name}.json"
        if candidate.exists():
            return candidate

        # Некоторые выгрузки именуют файл по типу объекта (Catalog.json),
        # а не по имени объекта. Пробуем тип (родительская директория).
        object_type_dir = object_dir.parent
        type_candidate = object_dir / f"{object_type_dir.name}.json"
        if type_candidate.exists():
            return type_candidate

        # Fallback: первый .json в директории объекта
        json_files = sorted(object_dir.glob("*.json"))
        return json_files[0] if json_files else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _infer_object_type(object_json: Path) -> str:
    """Вывести тип объекта из пути JSON-файла.

    Для пути ``cf_export/Catalog/Банки/Catalog.json`` вернёт ``"Catalog"``.
    """
    try:
        # Имя файла без расширения — либо имя объекта, либо тип.
        # Тип — это имя родителя родителя (два уровня выше файла).
        return object_json.parents[1].name
    except (IndexError, AttributeError):
        return ""


def _get_attributes_section(raw: dict) -> list | None:
    """Вернуть список реквизитов верхнего уровня из ``Catalog.json``.

    v8unpack выгружает реквизиты под ключом ``"Properties"`` или ``"Attributes"``
    (вариативность по версиям конфигурации).
    """
    for key in ("Properties", "Attributes", "props", "attributes"):
        section = raw.get(key)
        if isinstance(section, list):
            return section
    return None


def _get_tabular_attributes_section(raw: dict, tab_part_name: str) -> list | None:
    """Вернуть список реквизитов табличной части ``tab_part_name``.

    Ищет табличные части по ключу ``"TabularSections"`` или ``"TableParts"``.
    """
    for key in ("TabularSections", "TableParts", "tabularSections"):
        sections = raw.get(key)
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            # Имя секции может быть в "Name" или "name"
            name = section.get("Name") or section.get("name") or ""
            if name == tab_part_name:
                for attr_key in ("Properties", "Attributes", "props", "attributes"):
                    attrs = section.get(attr_key)
                    if isinstance(attrs, list):
                        return attrs
    return None


def _find_attribute(section: list, attr_name: str) -> dict | None:
    """Найти запись реквизита по имени (key ``"Name"`` или ``"name"``).""" 
    for record in section:
        if not isinstance(record, dict):
            continue
        name = record.get("Name") or record.get("name") or ""
        if name == attr_name:
            return record
    return None


def _extract_value_type(record: dict) -> str | None:
    """Извлечь строковое представление типа из записи реквизита."""
    for key in ("Type", "type", "ValueType", "valueType"):
        val = record.get(key)
        if val is not None:
            return str(val)
    return None


def _extract_synonym(record: dict) -> str | None:
    """Извлечь синоним из записи реквизита."""
    for key in ("Synonym", "synonym", "Title", "title"):
        val = record.get(key)
        if val is not None:
            return str(val)
    return None
