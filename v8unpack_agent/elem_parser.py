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

Фолбэк на большой *.json (issue #100)
--------------------------------------
Для обычных форм, у которых elem.json содержит пустые ``tree`` и ``data``,
парсер пытается извлечь элементы из большого ``<Name>.json`` в той же
директории. Этот файл — объект формы в бинарном JSON-кодировании платформы
1С. Реквизиты находятся в виде узлов ["14", '"ИмяРеквизита"', ...] внутри
родительского вектора с UUID известного виджета-данных (InputField, ComboBox).
Функция :func:`extract_legacy_form_elements` реализует этот разбор.

Фолбэк на TabularField-формы (issue #103)
------------------------------------------
Для форм-списков (ФормаСписка, ФормаВыбора), у которых elem.json пуст,
а большой *.json содержит виджет TabularField
(UUID ``ea83fe3a-ac3c-4cce-8045-3dddf35b28b1``), реквизиты закодированы
UUID-ами внутри паттерн-блока.

Реальная структура блока TabularField в живых данных:
  tf[0] = UUID TabularField
  tf[2] = ['5', ['"Pattern"', pattern_block], [columns], ...]
  tf[4] = ['14', '"ИмяВиджета"', ...]   — имя самого виджета списка

Все UUID реквизитов объекта лежат в ``pattern_block`` (``tf[2][1][1]``).
Они разрешаются через карту ``uuid -> имя``, построенную из метаданных
объекта-владельца функцией :func:`load_owner_attribute_map`.
Функция :func:`extract_legacy_list_form_elements` реализует кросс-чтение.

Модуль работает best-effort: ошибка парсинга elem.json не должна ломать
основной пайплайн распаковки.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Any

from v8unpack_agent.object_decoder import decode_object_attributes


@dataclass
class ElemIndexResult:
    elem_index_ok: bool
    elements: list[dict]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Классификация неиндексируемых форм (issue #105)
# ---------------------------------------------------------------------------

class UnindexedReason(enum.Enum):
    """Причина, по которой форма не была проиндексирована.

    Приоритеты (от высшего к низшему):
      D — нет большого *.json (нечего разбирать)
      A — TabularField есть, но attr_map пуста (object_decoder не распознал layout)
      B — TabularField есть, attr_map непустая, но UUID колонок не совпадают
      C — нет ни TabularField, ни InputField/ComboBox (норма: регистры, сервисные формы)
      PLATFORM_DYNAMIC — все TF-источники платформенные (wontfix by design)

    Распределение по live-базе после патча #107 (2216 форм, 2026-08-04):
      OK: 2169, B: 19, A: 4, PLATFORM_DYNAMIC: 7, C: 17, D: 0
      Покрытие 97.9%. Скачок +26 OK дало распознавание
      Pattern-ссылок ["#", UUID] в walk_refs.
    """
    TABULAR_FIELD_EMPTY_ATTR_MAP    = "tabular_field_empty_attr_map"    # A
    TABULAR_FIELD_NO_UUID_HITS      = "tabular_field_no_uuid_hits"      # B
    TABULAR_FIELD_PLATFORM_DYNAMIC  = "tabular_field_platform_dynamic"  # issue #107
    NO_TABULAR_NO_WIDGETS           = "no_tabular_no_widgets"           # C
    NO_LEGACY_JSON                  = "no_legacy_json"                  # D
    UNKNOWN                         = "unknown"


@dataclass
class UnindexedResult:
    """Результат классификации неиндексируемой формы."""
    reason: UnindexedReason
    detail: str = ""  # человекочитаемое объяснение для логов и метрик


# ---------------------------------------------------------------------------
# Константы для стандартных реквизитов и служебных UUID (issue #107)
# ---------------------------------------------------------------------------

# Позиции стандартных реквизитов в header[0][1] для объектов Catalog
# header[7] = UUID «Наименование», header[8] = UUID «Код»
# Проверено на 5 справочниках: Валюты, ЛичныеКонтакты, Номенклатура,
# Контрагенты, Подразделения.
_STD_ATTR_HEADER_POSITIONS: dict[int, str] = {7: "Наименование", 8: "Код"}

# Типы объектов, для которых применяется _standard_attribute_map
_STD_ATTR_OWNER_TYPES: frozenset[str] = frozenset({"Catalog"})

# Служебные UUID платформы 1С.
# Смысловые подписи подтверждены только частотой встречаемости и косвенно;
# точная семантика не доказана — в коде они помечены нейтрально.
_PLATFORM_SERVICE_UUIDS: frozenset[str] = frozenset({
    "e1692cc2-605b-4535-84dd-28440238746c",  # служебный, описание колонки
    "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26",  # служебный, описание колонки
    "d2314b5d-8da4-4e0f-822b-45e7500eae09",  # служебный, динамический список
    "48a6ebc3-fcc8-4f8f-b399-459fb32aa46b",  # служебный узел СправочникСписок
    "99f52caa-7b96-4bd4-a649-aedbd230a555",  # служебный (ПометкаУдаления)
    "e603c0f2-19d7-4f5d-9374-e61a0ee5c5a6",  # ДеревоЗначений
    "737535a4-4743-4d3e-98f7-0b40d6ab0c02",  # ТаблицаЗначений
    "acf6192e-8d63-4a2f-977a-3d88d786b0ac",  # строка отбора СКД
    "4652c4ec-6a7a-43ab-9a7e-7c39ea42e870",  # строка отбора СКД
    "381ed624-9217-4e63-85db-c4c3cb87daae",  # виджет InputField
    "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1",  # виджет TabularField
})

# Имена платформенных источников TF — колонки генерируются динамически,
# привязка к реквизитам объекта отсутствует (wontfix by design).
_PLATFORM_DYNAMIC_SOURCE_NAMES: frozenset[str] = frozenset({
    "Отбор",
    "ПостроительОтбор",
    "ИзмеренияСтроки",
    "ИзмеренияКолонки",
    "Порядок",
    "ПоляДляПорядка",
    "ПоляДляВыбранных",
    "ВыбранныеПоля",
    "ПоказателиДиаграммы",
    "СерииИТочкиДиаграммы",
    "ТабличноеПолеДеревоПоказателей",
    "ТабличноеПолеПараметров",
})


def _owner_type_name(owner_json_path: Path) -> str | None:
    """Тип объекта-владельца из имени его JSON-файла (например 'Catalog')."""
    stem = owner_json_path.stem  # 'Catalog'
    return stem if stem else None


def _standard_attribute_map(owner_json_path: Path) -> dict[str, str]:
    """Стандартные реквизиты объекта из фиксированных позиций header.

    Для Catalog.json: data["header"][0][1][7] = UUID «Наименование»,
    data["header"][0][1][8] = UUID «Код».

    Catalog.json имеет структуру словаря с ключом "header", а НЕ список:
      {"name": ..., "header": [[uuid, [uuid, ...]], ...], ...}
    Поэтому используется data["header"][0][1], а НЕ data[0][1].

    Возвращает пустой dict если тип не поддерживается или структура
    не соответствует ожидаемой.
    """
    owner_type = _owner_type_name(owner_json_path)
    if owner_type not in _STD_ATTR_OWNER_TYPES:
        return {}
    try:
        data = json.loads(owner_json_path.read_text(encoding="utf-8-sig"))
        header_row = data["header"][0][1]  # dict → ключ "header", затем [0][1]
        result: dict[str, str] = {}
        for pos, attr_name in _STD_ATTR_HEADER_POSITIONS.items():
            uuid = header_row[pos]
            if _is_uuid(uuid) and uuid != _NULL_UUID:
                result[uuid] = attr_name
        return result
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# BSL-парсер колонок программных ТЗ/ДЗ (issue #107)
# ---------------------------------------------------------------------------

# Regex на Источник.Колонки.Добавить("ИмяКолонки"
# Поддержаны:
#   - русский API: Колонки.Добавить
#   - английский API: Columns.Add
#   - префикс «ЭтаФорма.»
#   - регистронезависимо
_BSL_COLUMN_ADD_RE = re.compile(
    r'(?i)(?:ЭтаФорма\.)?([А-Яа-яA-Za-z0-9_]+)'
    r'\.(?:Колонки|Columns)\.(?:Добавить|Add)'
    r'\s*\(\s*"([^"]+)"',
)


def _extract_bsl_column_names(
    form_root: Path,
    source_name: str,
) -> list[str]:
    """Извлечь имена колонок из BSL-модуля формы для заданного источника данных.

    Ищет паттерн ``<source_name>.Колонки.Добавить("ИмяКолонки"``.
    Порядок сохраняется, дубли удаляются.
    Возвращает [] если BSL-модуль не найден или колонки не обнаружены.
    """
    bsl_path = _find_form_bsl(form_root)
    if bsl_path is None:
        return []
    try:
        text = bsl_path.read_text(encoding="utf-8-sig")
    except Exception:  # noqa: BLE001
        return []

    columns: list[str] = []
    seen: set[str] = set()
    for match in _BSL_COLUMN_ADD_RE.finditer(text):
        src, col_name = match.group(1), match.group(2)
        if src.lower() == source_name.lower() and col_name not in seen:
            seen.add(col_name)
            columns.append(col_name)
    return columns


# ---------------------------------------------------------------------------
# Вспомогательные функции для _find_all_tabular_field_nodes
# ---------------------------------------------------------------------------

def _find_all_tabular_field_nodes(node: Any, out: list) -> None:  # issue #107
    """Рекурсивно собрать все TF-узлы формы в список out."""
    if isinstance(node, list):
        if node and isinstance(node[0], str) and node[0] == _TABULAR_FIELD_UUID:
            out.append(node)
            return  # не углубляемся внутрь найденного TF
        for child in node:
            _find_all_tabular_field_nodes(child, out)
    elif isinstance(node, dict):
        for value in node.values():
            _find_all_tabular_field_nodes(value, out)


def classify_unindexed_form(
    form_root: Path,
    elem_result: ElemIndexResult,
) -> UnindexedResult:
    """Классифицировать форму, которую parse_elem_json не смог проиндексировать.

    Не мутирует ``elem_result``. Не бросает исключений (best-effort).
    Вызывать только когда ``elem_result.elem_index_ok is False``
    или ``elem_result.elements == []``.
    """
    try:
        return _classify_unindexed_form_impl(form_root)
    except Exception as exc:  # noqa: BLE001
        return UnindexedResult(
            reason=UnindexedReason.UNKNOWN,
            detail=f"classify_unindexed_form: неожиданное исключение: {exc}",
        )


def _classify_unindexed_form_impl(form_root: Path) -> UnindexedResult:
    """Внутренняя реализация без перехвата исключений."""
    # --- шаг 1: есть ли вообще большой *.json? ---
    legacy_path = _find_legacy_form_json(form_root)
    if legacy_path is None:
        return UnindexedResult(
            reason=UnindexedReason.NO_LEGACY_JSON,
            detail=f"Большой *.json не найден в {form_root}",
        )

    # --- шаг 2: читаем и ищем TabularField ---
    try:
        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return UnindexedResult(
            reason=UnindexedReason.UNKNOWN,
            detail=f"Не удалось прочитать {legacy_path}: {exc}",
        )

    if not _has_tabular_field(legacy_data):
        return UnindexedResult(
            reason=UnindexedReason.NO_TABULAR_NO_WIDGETS,
            detail=(
                f"TabularField и InputField/ComboBox не найдены "
                f"в {legacy_path.name} — форма без виджетов данных"
            ),
        )

    # --- шаг 3: TabularField найден — проверяем attr_map ---
    dummy_warnings: list[str] = []
    attr_map = load_owner_attribute_map(form_root, dummy_warnings)

    if not attr_map:
        detail_parts = [f"TabularField найден в {legacy_path.name}"]
        if dummy_warnings:
            detail_parts.append("; ".join(dummy_warnings))
        else:
            detail_parts.append("attr_map пуста — object_decoder не распознал layout владельца")
        return UnindexedResult(
            reason=UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP,
            detail=" | ".join(detail_parts),
        )

    # --- шаг 4: attr_map непустая — собираем все TF-узлы ---
    tf_nodes: list = []
    _find_all_tabular_field_nodes(legacy_data, tf_nodes)

    if not tf_nodes:
        tf_node = _find_tabular_field_node(legacy_data)
        if tf_node is None:
            return UnindexedResult(
                reason=UnindexedReason.TABULAR_FIELD_NO_UUID_HITS,
                detail=(
                    f"TabularField найден в {legacy_path.name}, "
                    f"но извлечь узел не удалось; attr_map содержит "
                    f"{len(attr_map)} реквизитов"
                ),
            )
        tf_nodes = [tf_node]

    # --- шаг 5: проверяем источники — все ли платформенные? ---
    source_names = [
        _tabular_field_source_name(tf) for tf in tf_nodes
    ]
    non_platform_sources = [
        s for s in source_names if s not in _PLATFORM_DYNAMIC_SOURCE_NAMES
    ]

    if not non_platform_sources:
        return UnindexedResult(
            reason=UnindexedReason.TABULAR_FIELD_PLATFORM_DYNAMIC,
            detail=(
                f"Все TF-источники платформенные (wontfix): "
                f"{sorted(set(source_names))}"
            ),
        )

    # --- шаг 6: пробуем разрешить UUID хотя бы для одного непл. источника ---
    unresolved_uuids_count = 0
    for tf in tf_nodes:
        src = _tabular_field_source_name(tf)
        if src in _PLATFORM_DYNAMIC_SOURCE_NAMES:
            continue
        slots = _tabular_field_attribute_slots(tf, attr_map)
        if slots:
            return UnindexedResult(
                reason=UnindexedReason.UNKNOWN,
                detail=(
                    f"TabularField разрешён ({len(slots)} слотов), "
                    f"но форма не проиндексирована — неизвестная причина"
                ),
            )
        uuids: list[str] = []
        _collect_uuids(tf, uuids)
        unresolved = [
            u for u in uuids
            if u not in attr_map and u not in _PLATFORM_SERVICE_UUIDS
        ]
        unresolved_uuids_count += len(set(unresolved))

    src_label = (
        "(динамический список B1a)"
        if all(s in {"СправочникСписок", "СправочникДерево", "ДеревоГрупп",
                     "Список", "СписокХарактеристикиНоменклатуры"}
               for s in non_platform_sources)
        else "(программные ТЗ/ДЗ B2)"
    )
    return UnindexedResult(
        reason=UnindexedReason.TABULAR_FIELD_NO_UUID_HITS,
        detail=(
            f"TabularField в {legacy_path.name}: источники {non_platform_sources} "
            f"{src_label}; "
            f"{unresolved_uuids_count} UUID вне attr_map и вне служебных; "
            f"attr_map содержит {len(attr_map)} реквизитов"
        ),
    )


def _find_tabular_field_node(node: Any) -> list | None:
    """Найти первый полноценный узел TabularField (list с tf[0] == UUID)."""
    if isinstance(node, list):
        if node and isinstance(node[0], str) and node[0] == _TABULAR_FIELD_UUID:
            return node
        for child in node:
            result = _find_tabular_field_node(child)
            if result is not None:
                return result
    elif isinstance(node, dict):
        for value in node.values():
            result = _find_tabular_field_node(value)
            if result is not None:
                return result
    return None


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
_LIST_PREFIX = "Список"


# ---------------------------------------------------------------------------
# Константы для разбора большого *.json обычной формы (issue #100)
# ---------------------------------------------------------------------------

_LEGACY_FORM_DATA_WIDGET_UUIDS: frozenset[str] = frozenset({
    "381ed624-9217-4e63-85db-c4c3cb87daae",  # InputField
    "64483e7f-3833-48e2-8c75-2c31aac49f6e",  # ComboBox / ListBox
})

_LEGACY_WIDGET_TYPE_NAMES: dict[str, str] = {
    "381ed624-9217-4e63-85db-c4c3cb87daae": "InputField",
    "64483e7f-3833-48e2-8c75-2c31aac49f6e": "ComboBox",
}

# ---------------------------------------------------------------------------
# Константы для разбора форм-списков с TabularField (issue #103)
# ---------------------------------------------------------------------------

_TABULAR_FIELD_UUID = "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1"
_TABULAR_PATTERN_TAG = '"Pattern"'


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
    """Устаревший вспомогательный обходчик header.

    Оставлен для совместимости. UUID-карта теперь строится через
    :func:`load_owner_attribute_map`.
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
    return "CommonForm" in form_root.parts


def _find_owner_metadata_json(form_root: Path) -> Path | None:
    """Найти ``<Type>.json`` владельца без фиксированного списка типов."""
    for parent in form_root.parents:
        if parent.parent == parent:
            break
        candidate = parent / f"{parent.parent.name}.json"
        if candidate.is_file():
            return candidate
    return None


def load_owner_attribute_map(form_root: Path, warnings: list[str]) -> dict[str, str]:
    """Карта ``uuid реквизита -> имя`` из метаданных объекта-владельца формы.

    Помимо пользовательских реквизитов (Properties, TabularSections)
    дописывает стандартные реквизиты из фиксированных позиций header
    (issue #107): для Catalog — «Наименование» (pos 7) и «Код» (pos 8).
    """
    path = _find_owner_metadata_json(form_root)
    if path is None:
        if not _is_common_form(form_root):
            warnings.append(f"Метаданные владельца не найдены для {form_root}")
        return {}

    result = decode_object_attributes(path)
    if not result.ok:
        warnings.append(
            f"object_decoder: не удалось декодировать {path}: "
            f"{result.error} — {result.warnings}"
        )
        return {}

    mapping: dict[str, str] = {}
    for prop in result.data.get("Properties", []):
        uuid = prop.get("UUID") or ""
        name = prop.get("Name") or ""
        if uuid and name:
            mapping.setdefault(uuid, name)
    for ts in result.data.get("TabularSections", []):
        for prop in ts.get("Properties", []):
            uuid = prop.get("UUID") or ""
            name = prop.get("Name") or ""
            if uuid and name:
                mapping.setdefault(uuid, name)

    # issue #107: дописываем стандартные реквизиты из data["header"][0][1][7/8]
    # через setdefault — не затираем данные декодера
    for uuid, attr_name in _standard_attribute_map(path).items():
        mapping.setdefault(uuid, attr_name)

    if not mapping:
        warnings.append(
            f"object_decoder: karta rekvizitov pusta dlya {path.name} - "
            f"layout ne raspoznan dekoderom"
        )

    return mapping


def _tabular_field_source_name(tf_node: list) -> str:
    """Имя источника данных TabularField из узла привязки tf[4]."""
    if len(tf_node) > 4:
        binding = tf_node[4]
        if isinstance(binding, list) and len(binding) >= 2 and binding[0] == _LEGACY_BINDING_TAG:
            name = _unquote_1c(binding[1])
            if name:
                return name
    return _LIST_PREFIX


def _tabular_field_attribute_slots(
    tf_node: list,
    attr_map: dict[str, str],
) -> list[tuple[str, str]]:
    """Упорядоченные привязки реквизитов внутри TabularField.

    При наличии блока ``20`` используются только его однозначные слоты.
    Ранний выход применяется только если candidates непуст — иначе
    переходим к fallback по ссылкам ``["0", UUID]``. (issue #107)
    """
    candidates: list[list[tuple[str, str]]] = []
    has_node20 = False

    def walk_node20(node: Any) -> None:
        nonlocal has_node20

        if not isinstance(node, list):
            return

        if node and node[0] == "20":
            has_node20 = True

            if len(node) > 6:
                resolved: list[tuple[str, str]] = []

                for slot in node[6:]:
                    uuids: list[str] = []
                    _collect_uuids(slot, uuids)

                    hits: list[tuple[str, str]] = []
                    seen: set[str] = set()

                    for uuid in uuids:
                        name = attr_map.get(uuid)
                        if name and uuid not in seen:
                            seen.add(uuid)
                            hits.append((uuid, name))

                    if len(hits) == 1:
                        resolved.append(hits[0])

                if resolved:
                    candidates.append(resolved)

        for child in node:
            walk_node20(child)

    walk_node20(tf_node)

    # issue #107: ранний выход ТОЛЬКО если candidates непуст.
    # Если блок 20 найден, но все кандидаты пусты — переходим к fallback:
    # нужный UUID может лежать в TF в другом месте (напр. через ["0", UUID]).
    if has_node20 and candidates:
        return max(candidates, key=len, default=[])

    result: list[tuple[str, str]] = []
    seen_uuids: set[str] = set()

    def walk_refs(node: Any) -> None:
        if not isinstance(node, list):
            return

        if (
            len(node) == 2
            and node[0] in ("0", '"#"')
            and isinstance(node[1], str)
            and node[1] in attr_map
        ):
            uuid = node[1]
            if uuid not in seen_uuids:
                seen_uuids.add(uuid)
                result.append((uuid, attr_map[uuid]))
            return

        for child in node:
            walk_refs(child)

    walk_refs(tf_node)
    return result

_LEGACY_BINDING_TAG = "14"
_LEGACY_NAME_POS = 4


def _legacy_attribute_name(raw: object) -> str | None:
    if not isinstance(raw, list) or len(raw) <= _LEGACY_NAME_POS:
        return None
    node = raw[_LEGACY_NAME_POS]
    if not isinstance(node, list) or len(node) < 2:
        return None
    if node[0] != _LEGACY_BINDING_TAG:
        return None
    return _unquote_1c(node[1])


def decode_legacy_data_path(record: object) -> tuple[str | None, list[str]]:
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


# ---------------------------------------------------------------------------
# Разбор большого *.json обычной формы (issue #100)
# ---------------------------------------------------------------------------

def extract_legacy_form_elements(form_json: Any) -> list[dict]:
    results: list[dict] = []
    seen_names: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, list):
            return
        if (
            len(node) >= 5
            and isinstance(node[0], str)
            and node[0] in _LEGACY_FORM_DATA_WIDGET_UUIDS
            and isinstance(node[4], list)
            and len(node[4]) >= 2
            and node[4][0] == _LEGACY_BINDING_TAG
        ):
            name = _unquote_1c(node[4][1])
            if name and name not in seen_names:
                seen_names.add(name)
                widget_type = _LEGACY_WIDGET_TYPE_NAMES.get(node[0], "InputField")
                results.append({
                    "name": name,
                    "type": widget_type,
                    "data_path": f"{_OBJECT_PREFIX}.{name}",
                    "source": "legacy_form_json",
                })
        for child in node:
            walk(child)

    if isinstance(form_json, dict):
        for value in form_json.values():
            walk(value)
    elif isinstance(form_json, list):
        walk(form_json)

    return results


# ---------------------------------------------------------------------------
# Разбор форм-списков с TabularField (issue #103, #107)
# ---------------------------------------------------------------------------

def _has_tabular_field(node: Any) -> bool:
    if isinstance(node, list):
        if node and isinstance(node[0], str) and node[0] == _TABULAR_FIELD_UUID:
            return True
        return any(_has_tabular_field(child) for child in node)
    if isinstance(node, dict):
        return any(_has_tabular_field(v) for v in node.values())
    return False


def extract_legacy_list_form_elements(
    form_json: Any,
    obj_json: Any,
    form_root: Path | None = None,  # issue #107: опциональный BSL-путь
) -> list[dict]:
    """Извлечь колонки TabularField из подтвержденного блока 20.

    Если UUID-слоты пусты и источник не платформенный — пытается
    извлечь имена колонок из BSL-модуля формы (issue #107).
    Старая двухаргументная сигнатура работает как раньше.
    """
    if form_json is None or not isinstance(obj_json, dict):
        return []
    attr_map = obj_json.get("attr_map")
    if not isinstance(attr_map, dict) or not attr_map:
        return []

    results: list[dict] = []
    seen_names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
            return
        if not isinstance(node, list):
            return
        if node and node[0] == _TABULAR_FIELD_UUID:
            source_name = _tabular_field_source_name(node)
            slots = _tabular_field_attribute_slots(node, attr_map)
            if slots:
                for _uuid, name in slots:
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    results.append({
                        "name": name,
                        "type": "TabularFieldColumn",
                        "data_path": f"{source_name}.{name}",
                        "source": "legacy_list_form_json",
                    })
            elif form_root is not None and source_name not in _PLATFORM_DYNAMIC_SOURCE_NAMES:
                # issue #107: fallback на BSL-парсер колонок
                bsl_columns = _extract_bsl_column_names(form_root, source_name)
                for col_name in bsl_columns:
                    if col_name in seen_names:
                        continue
                    seen_names.add(col_name)
                    results.append({
                        "name": col_name,
                        "type": "TabularFieldColumn",
                        "data_path": f"{source_name}.{col_name}",
                        "source": "legacy_list_form_bsl",
                    })
            return
        for child in node:
            walk(child)

    walk(form_json)
    return results


def _find_legacy_form_json(form_root: Path) -> Path | None:
    candidates = [
        p for p in form_root.glob("*.json")
        if not p.name.endswith(".elem.json")
        and ".id" not in p.stem
        and p.name != "form_elements_index.json"
    ]
    return candidates[0] if len(candidates) == 1 else (
        max(candidates, key=lambda p: p.stat().st_size) if candidates else None
    )


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
        legacy_json_path = _find_legacy_form_json(form_root)
        if legacy_json_path is not None:
            try:
                legacy_data = json.loads(
                    legacy_json_path.read_text(encoding="utf-8-sig")
                )

                # --- Фолбэк #103/#107: TabularField ---
                if _has_tabular_field(legacy_data):
                    if attribute_map:
                        # issue #107: передаём form_root для BSL-fallback
                        list_elements = extract_legacy_list_form_elements(
                            legacy_data, {"attr_map": attribute_map}, form_root
                        )
                        if list_elements:
                            warnings.append(
                                f"elem.json пуст, колонки TabularField извлечены из "
                                f"{legacy_json_path.name} (issue #103/#107): "
                                f"{len(list_elements)} эл."
                            )
                            elements = list_elements

                # --- Фолбэк #100: InputField / ComboBox ---
                if not elements:
                    legacy_elements = extract_legacy_form_elements(legacy_data)
                    if legacy_elements:
                        warnings.append(
                            f"elem.json пуст, элементы извлечены из {legacy_json_path.name} "
                            f"(legacy form JSON, issue #100): {len(legacy_elements)} эл."
                        )
                        elements = legacy_elements
            except Exception as exc:
                warnings.append(
                    f"Не удалось разобрать legacy form JSON {legacy_json_path}: {exc}"
                )

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
