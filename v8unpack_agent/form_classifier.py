"""form_classifier — классификатор форм 1С: объектные vs. сервисные.

Решает issue #98: формы-мастера, помощники и диалоги привязывают поля
не к реквизитам объекта метаданных (``Объект.Реквизит``), а к временным
реквизитам формы. Это не баг парсера — архитектурный паттерн платформы 1С.

Классы форм
------------------
:data:`FormClass.OBJECT`
    Объектная форма: поля привязаны к реквизитам объекта (``Объект.*``).
    Включается в агрегированное покрытие data_path.

:data:`FormClass.SERVICE`
    Сервисная форма (мастер, помощник, диалог): поля привязаны к
    временным реквизитам формы, а не к ``Объект.*``.
    Исключается из агрегированного покрытия или считается отдельно.

:data:`FormClass.UNKNOWN`
    Класс не определён: нет data-элементов или имя не попало под паттерн.

Критерий классификации
------------------------
Критерий двойной, объединяется через OR:

1. **По имени**: имя формы начинается с одного из паттернов:
   ``Помощник*``, ``Мастер*``, ``Черновик*``, ``Диалог*``, ``Добавление*``
   (регистронезависимо).

2. **По структуре**: есть data-элементы (> 0) И ни одного
   ``data_path`` не начинается с ``Объект.`` (регистронезависимо).
Форма считается SERVICE если выполняется хотя бы одно условие.

OS-нейтральность, кодировка UTF-8.
"""
from __future__ import annotations

from typing import Iterable

from v8unpack_agent.coverage_metric import DATA_ELEMENT_TYPES


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Паттерны начала имени сервисных форм (проверяется после lower()).
SERVICE_FORM_NAME_PATTERNS: tuple[str, ...] = (
    "помощник",    # ПомощникПодключенияЭДО, ПомощникНастройки и т.п.
    "мастер",       # МастерЗаполнения, МастерНастройки и т.п.
    "черновик",    # ЧерновикМЧД, ЧерновикПередоверияМЧД и т.п.
    "диалог",      # ДиалогВыбораПериода и т.п.
    "добавление",  # ДобавлениеПредставителя и т.п.
)

# Префикс объектной привязки (после lower()).
_OBJECT_BINDING_PREFIX: str = "объект."


# ---------------------------------------------------------------------------
# FormClass — возвращаемые значения
# ---------------------------------------------------------------------------

class FormClass(str):
    """String-совместимые константы класса формы.

    Используется как str (сериализуется напрямую), но позволяет сравнение
    через FormClass.OBJECT, FormClass.SERVICE и FormClass.UNKNOWN.
    """

    OBJECT: "FormClass"
    SERVICE: "FormClass"
    UNKNOWN: "FormClass"

    def __new__(cls, value: str) -> "FormClass":
        return super().__new__(cls, value)


FormClass.OBJECT = FormClass("object")
FormClass.SERVICE = FormClass("service")
FormClass.UNKNOWN = FormClass("unknown")


# ---------------------------------------------------------------------------
# Классификация по имени
# ---------------------------------------------------------------------------

def classify_form_by_name(form_name: str) -> FormClass:
    """Cклассифицировать форму по имени.

    Возвращает :data:`FormClass.SERVICE` если имя начинается
    (регистронезависимо) с одного из :data:`SERVICE_FORM_NAME_PATTERNS`.
    Иначе — :data:`FormClass.UNKNOWN` (не хватает данных для определения).

    Параметры
    ----------
    form_name:
        Короткое имя формы (leaf-часть пути, например ``ПомощникПодключенияЭДО``).
    """
    if not form_name:
        return FormClass.UNKNOWN
    lower_name = form_name.lower()
    for pattern in SERVICE_FORM_NAME_PATTERNS:
        if lower_name.startswith(pattern):
            return FormClass.SERVICE
    return FormClass.UNKNOWN


# ---------------------------------------------------------------------------
# Классификация по структуре привязок
# ---------------------------------------------------------------------------

def classify_form_by_bindings(elements: Iterable[dict]) -> FormClass:
    """Cклассифицировать форму по структуре привязок data_path.

    Алгоритм:
    - если data-элементов нет — :data:`FormClass.UNKNOWN`;
    - если хотя бы один ``data_path`` начинается
      с ``Объект.`` (регистронезависимо) — :data:`FormClass.OBJECT`;
    - если data-элементы есть, но ни одного объектного пути — :data:`FormClass.SERVICE`.

    Параметры
    ----------
    elements:
        Итерируемая коллекция dict-элементов формы с полями
        ``"type"`` и ``"data_path"``.
    """
    data_count = 0
    for elem in elements:
        elem_type = elem.get("type") or "Unknown"
        if elem_type not in DATA_ELEMENT_TYPES:
            continue
        data_count += 1
        dp = elem.get("data_path") or ""
        if dp.lower().startswith(_OBJECT_BINDING_PREFIX):
            return FormClass.OBJECT
    if data_count == 0:
        return FormClass.UNKNOWN
    return FormClass.SERVICE


# ---------------------------------------------------------------------------
# Итоговая классификация
# ---------------------------------------------------------------------------

def classify_form(
    form_name: str,
    elements: Iterable[dict],
) -> FormClass:
    """Cклассифицировать форму, объединяя два критерия (OR-логика).

    Форма считается SERVICE если хотя бы один критерий даёт SERVICE.
    Форма считается OBJECT если ни один критерий не дал SERVICE и хотя бы
    один дал OBJECT.
    Иначе — UNKNOWN.

    Параметры
    ----------
    form_name:
        Короткое имя формы (лист-часть пути).
    elements:
        Итерируемая коллекция dict-элементов формы.
    """
    elements = list(elements)
    by_name = classify_form_by_name(form_name)
    if by_name == FormClass.SERVICE:
        return FormClass.SERVICE
    by_bindings = classify_form_by_bindings(elements)
    if by_bindings == FormClass.SERVICE:
        return FormClass.SERVICE
    if by_bindings == FormClass.OBJECT:
        return FormClass.OBJECT
    if by_name == FormClass.UNKNOWN and by_bindings == FormClass.UNKNOWN:
        return FormClass.UNKNOWN
    return FormClass.UNKNOWN
