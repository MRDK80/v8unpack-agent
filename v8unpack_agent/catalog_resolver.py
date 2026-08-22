"""catalog_resolver — резолюция data_path-привязок через файл объекта.

Резолвит строки вида ``"Объект.Реквизит"`` или ``"Объект.ТЧ.Реквизит"``
по файлу ``<Тип>/<Имя>/<Тип|Имя>.json`` в выгрузке конфигурации.

Поддерживаемые форматы data_path
---------------------------------
``Объект.Реквизит``
    Реквизит верхнего уровня объекта.

``Объект.ТабличнаяЧасть.Реквизит``
    Реквизит внутри табличной части объекта.

Источник данных (issue #148)
----------------------------
Модуль не разбирает файл объекта самостоятельно. Единственная точка
декодирования — :func:`v8unpack_agent.object_decoder.decode_object_attributes`.
Причина: production-выгрузка v8unpack содержит верхнеуровневый ключ ``header``
(raw-header), а не готовые списки ``Properties`` / ``Attributes``, и разбирать
его умеет только ``object_decoder``. Второго парсера raw-header в проекте нет.

Best-effort: если файл отсутствует, JSON повреждён, ``DecodeResult.ok`` равен
``False``, путь нераспознан или структура неполная — возвращается
:class:`ResolvedBinding` с ``resolved=False``. Исключения не пробрасываются,
тип, имя и синоним не достраиваются по догадке.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from v8unpack_agent.object_decoder import decode_object_attributes

if TYPE_CHECKING:
    from v8unpack_agent.scan_forms import FormEntry

__all__ = [
    "ResolvedBinding",
    "clear_object_cache",
    "object_json_path",
    "resolve_data_path",
]


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
    """Строковое представление типа реквизита, или ``None``."""

    synonym: str | None
    """Синоним реквизита, или ``None``."""

    resolved: bool
    """``True`` — резолюция успешна; ``False`` — partial/failed (best-effort)."""


# ---------------------------------------------------------------------------
# Кэш декодированных файлов объектов
# ---------------------------------------------------------------------------
# ``form_context._resolve_relations()`` вызывает ``resolve_data_path`` отдельно
# для каждой data-связи, то есть один и тот же файл объекта декодируется
# многократно. Безусловный ``lru_cache`` только по ``Path`` здесь недопустим:
# файл может измениться в долгоживущем процессе или внутри теста. Поэтому ключ
# включает ``stat``: (path, mtime_ns, size). Кэш ограничен по размеру и не
# влияет на публичный контракт — его можно удалить без изменения поведения.

_DECODE_CACHE_MAXSIZE = 32
_DECODE_CACHE: dict[tuple[str, int, int], dict[str, Any] | None] = {}
_MISSING: Any = object()


def clear_object_cache() -> None:
    """Сбросить кэш декодированных файлов объектов.

    Нужен долгоживущим процессам и тестам, которые перезаписывают файл
    объекта; на результат резолюции не влияет.
    """
    _DECODE_CACHE.clear()


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

    Notes
    -----
    Файл объекта декодируется через
    ``object_decoder.decode_object_attributes`` (issue #148): верхнеуровневые
    реквизиты берутся из ``DecodeResult.data["Properties"]``, реквизиты
    табличных частей — из ``DecodeResult.data["TabularSections"]`` и
    ``Properties`` найденной секции.
    """
    parts = data_path.split(".") if isinstance(data_path, str) else []

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
    if len(parts) < 2:
        return _unresolved

    data = _decoded_object_data(object_json)
    if data is None:
        return _unresolved

    try:
        # Формат: Объект.Реквизит        → parts = [obj, attr]
        # Формат: Объект.ТЧ.Реквизит    → parts = [obj, tab_part, attr]
        if len(parts) == 2:
            attr_key = parts[1]
            section = _top_level_properties(data)
        elif len(parts) == 3:
            attr_key = parts[2]
            section = _tabular_properties(data, parts[1])
        else:
            # Более глубокие пути не входят в подтверждённые правила
            # интерпретации data_path и не резолвятся.
            return _unresolved

        if section is None:
            return _unresolved

        attr_record = _find_attribute(section, attr_key)
        if attr_record is None:
            return _unresolved

        return replace(
            _unresolved,
            attribute_name=attr_key,
            value_type=_extract_value_type(attr_record),
            synonym=_extract_synonym(attr_record),
            resolved=True,
        )
    except Exception:  # noqa: BLE001
        return _unresolved


def object_json_path(form_entry: "FormEntry") -> Path | None:
    """Найти JSON-файл объекта по ``FormEntry``.

    Поднимается на 2 уровня вверх от ``form_entry.form_path``
    (``<Тип>/<Имя>/<Контейнер>/<Форма>`` → ``<Тип>/<Имя>``) и ищет ``.json``
    файл с именем, совпадающим с именем директории объекта (например
    ``Catalog.json`` или ``Банки.json``).

    Returns
    -------
    :class:`~pathlib.Path` к JSON-файлу объекта или ``None``.
    """
    try:
        # form_path: .../cf_export/<Тип>/<Имя>/<Контейнер>/<Форма>
        # -2 уровня вверх → .../cf_export/<Тип>/<Имя>
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


def _decode(object_json: Path) -> dict[str, Any] | None:
    """Декодировать файл объекта через ``object_decoder``.

    Возвращает ``DecodeResult.data`` при ``ok=True``; во всех остальных
    случаях (``ok=False``, отсутствующий файл, повреждённый JSON,
    неожиданная форма результата) — ``None``. Исключения не пробрасываются.
    """
    try:
        result = decode_object_attributes(object_json)
    except Exception:  # noqa: BLE001
        return None

    if result is None or not getattr(result, "ok", False):
        return None

    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    return data


def _cache_key(object_json: Path) -> tuple[str, int, int] | None:
    """Ключ кэша по пути и метаданным файла. ``None`` — файл недоступен."""
    try:
        stat = object_json.stat()
    except OSError:
        return None
    return (os.fspath(object_json), stat.st_mtime_ns, stat.st_size)


def _decoded_object_data(object_json: Path) -> dict[str, Any] | None:
    """``_decode`` с ограниченным кэшем по ``(path, mtime_ns, size)``."""
    key = _cache_key(object_json)
    if key is None:
        # Файла нет или он недоступен: результат не кэшируем.
        return _decode(object_json)

    cached = _DECODE_CACHE.get(key, _MISSING)
    if cached is not _MISSING:
        return cached  # type: ignore[return-value]

    data = _decode(object_json)
    if len(_DECODE_CACHE) >= _DECODE_CACHE_MAXSIZE:
        _DECODE_CACHE.clear()
    _DECODE_CACHE[key] = data
    return data


def _top_level_properties(data: dict[str, Any]) -> list | None:
    """Реквизиты верхнего уровня из ``DecodeResult.data["Properties"]``."""
    section = data.get("Properties")
    return section if isinstance(section, list) else None


def _tabular_properties(data: dict[str, Any], tab_part_name: str) -> list | None:
    """Реквизиты табличной части ``tab_part_name``.

    Табличные части приходят в ``DecodeResult.data["TabularSections"]``,
    их реквизиты — в ``Properties`` соответствующей секции.
    """
    sections = data.get("TabularSections")
    if not isinstance(sections, list):
        return None

    for section in sections:
        if not isinstance(section, dict):
            continue
        if not _same_name(section.get("Name"), tab_part_name):
            continue
        attrs = section.get("Properties")
        return attrs if isinstance(attrs, list) else None
    return None


def _same_name(left: Any, right: Any) -> bool:
    """Сравнить имена метаданных: имена 1С регистронезависимы."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return left.strip().casefold() == right.strip().casefold()


def _find_attribute(section: list, attr_name: str) -> dict | None:
    """Найти запись реквизита по имени в нормализованном списке реквизитов."""
    for record in section:
        if isinstance(record, dict) and _same_name(record.get("Name"), attr_name):
            return record
    return None


def _extract_value_type(record: dict) -> str | None:
    """Извлечь строковое представление типа из записи реквизита."""
    val = record.get("Type")
    return str(val) if val is not None else None


def _extract_synonym(record: dict) -> str | None:
    """Извлечь синоним из записи реквизита."""
    val = record.get("Synonym")
    return str(val) if val is not None else None
