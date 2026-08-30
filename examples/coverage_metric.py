"""Пример: метрика покрытия data_path по элементам данных (issue #90).

Пример полностью синтетический и самодостаточный. Реальные данные,
контейнеры 1С и внутренняя инфраструктура не используются.

Показано:
* старая метрика (все элементы в знаменателе) vs новая (только данные);
* форма с преобладанием служебных элементов;
* использование CoverageReport.to_dict() для JSON-отчёта;
* обёртка calc_coverage_from_elem_index() под ElemIndexResult;
* form_class в CoverageReport: объектная vs. сервисная форма (issue #98).

Запуск:

python examples/coverage_metric.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent.coverage_metric import (
    DATA_ELEMENT_TYPES,
    PLATFORM_STANDARD_ATTRIBUTES,
    SERVICE_ELEMENT_TYPES,
    calc_coverage_from_elem_index,
    calc_data_path_coverage,
)
from v8unpack_agent.elem_parser import parse_elem_json

# ---------------------------------------------------------------------------
# Синтетические элементы формы
# ---------------------------------------------------------------------------

# Форма Catalog/Банки/ФормаЭлементаУправляемая — в синтетике упрощена до 19
# элементов: 11 полей данных (все привязаны) + 8 служебных.
#
# Реальная форма УТ 10.3 содержит 14 DATA-элементов, из которых 11 привязаны
# (78.6%) — три платформенных реквизита (Код, Наименование, Родитель) не
# разрешены до issue #88. Синтетика намеренно опускает эти три элемента,
# чтобы показать 11/11 = 100% без шума от нерешённого #88.
BANKS_FORM_ELEMENTS = [
    # Данные — привязаны
    {"type": "Field", "name": "КоррСчет",    "data_path": "Объект.КоррСчет"},
    {"type": "Field", "name": "БИК",          "data_path": "Объект.БИК"},
    {"type": "Field", "name": "НомерКор",     "data_path": "Объект.НомерКор"},
    {"type": "Field", "name": "ТелефонФакс",  "data_path": "Объект.ТелефонФакс"},
    {"type": "Field", "name": "Индекс",       "data_path": "Объект.Индекс"},
    {"type": "Field", "name": "Город",        "data_path": "Объект.Город"},
    {"type": "Field", "name": "Адрес",        "data_path": "Объект.Адрес"},
    {"type": "Field", "name": "РКЦ",          "data_path": "Объект.РКЦ"},
    {"type": "Field", "name": "Участник",     "data_path": "Объект.Участник"},
    {"type": "Field", "name": "ОКПО",         "data_path": "Объект.ОКПО"},
    {"type": "Field", "name": "ОКОНХ",        "data_path": "Объект.ОКОНХ"},
    # Служебные — без привязки (штатно)
    {"type": "Group",        "name": "Группа1",        "data_path": None},
    {"type": "Group",        "name": "Группа2",        "data_path": None},
    {"type": "Group",        "name": "Группа3",        "data_path": None},
    {"type": "Button",       "name": "Команда1",       "data_path": None},
    {"type": "Label",        "name": "Надпись1",       "data_path": None},
    {"type": "CommandPanel", "name": "ПанельКоманд",   "data_path": None},
    {"type": "Panel",        "name": "Панель1",        "data_path": None},
    {"type": "Page",         "name": "СтраницаОсновная", "data_path": None},
]


def demo_constants() -> None:
    """Вывести состав констант модуля."""
    print("=== Константы coverage_metric ===")
    print(f"DATA_ELEMENT_TYPES ({len(DATA_ELEMENT_TYPES)}): {sorted(DATA_ELEMENT_TYPES)}")
    print(f"SERVICE_ELEMENT_TYPES ({len(SERVICE_ELEMENT_TYPES)}): {sorted(SERVICE_ELEMENT_TYPES)}")
    print(
        f"PLATFORM_STANDARD_ATTRIBUTES ({len(PLATFORM_STANDARD_ATTRIBUTES)}): "
        f"{sorted(PLATFORM_STANDARD_ATTRIBUTES)}"
    )
    print()


def demo_old_vs_new() -> None:
    """Сравнение старой метрики (все элементы) с новой (только данные)."""
    print("=== Старая метрика vs новая (синтетика Банки, 19 элементов) ===")

    total = len(BANKS_FORM_ELEMENTS)
    bound_all = sum(1 for e in BANKS_FORM_ELEMENTS if e.get("data_path"))
    old_pct = bound_all / total * 100

    report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)

    print(
        f"Старая: {bound_all}/{total} = {old_pct:.1f}% "
        f"(все элементы в знаменателе, включая Group/Label/Panel...)"
    )
    print(f"Новая: {report}")
    print()


def demo_partial_coverage() -> None:
    """Форма с частичной привязкой: часть полей не заполнена."""
    print("=== Частичная привязка ===")
    elements = [
        {"type": "Field",  "name": "Наименование", "data_path": "Объект.Наименование"},
        {"type": "Field",  "name": "ИНН",           "data_path": None},  # не привязан
        {"type": "Field",  "name": "КПП",           "data_path": None},  # не привязан
        {"type": "Table",  "name": "Контакты",      "data_path": "Объект.Контакты"},
        {"type": "Label",  "name": "НадписьИНН",    "data_path": None},  # служебный
        {"type": "Group",  "name": "Группа1",       "data_path": None},  # служебный
    ]

    report = calc_data_path_coverage(elements)
    print(report)
    print(f"  to_dict() → {json.dumps(report.to_dict(), ensure_ascii=False)}")
    print()


def demo_elem_index_wrapper() -> None:
    """calc_coverage_from_elem_index: обёртка под parse_elem_json."""
    print("=== Обёртка под ElemIndexResult ===")

    with tempfile.TemporaryDirectory() as tmp:
        form_dir = Path(tmp) / "Catalog" / "Товары" / "CatalogForm" / "ФормаЭлемента"
        form_dir.mkdir(parents=True)

        payload = {
            "params": [], "props": [], "commands": [],
            "tree": [
                {"name": "Наименование", "type": "InputField"},
                {"name": "Цена",         "type": "Field"},
                {"name": "Группа1",      "type": "Group"},
                {"name": "Надпись1",     "type": "Label"},
            ],
            "data": {
                "Наименование": {
                    "ver": "1", "page": None,
                    "raw": [], "data_path": "Объект.Наименование",
                },
                "Цена": {
                    "ver": "1", "page": None,
                    "raw": [], "data_path": "Объект.Цена",
                },
                "Группа1":   {"ver": "1", "page": None, "raw": []},
                "Надпись1":  {"ver": "1", "page": None, "raw": []},
            },
        }

        (form_dir / "CatalogForm.elem.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        result = parse_elem_json(form_dir)
        report = calc_coverage_from_elem_index(result)
        print(f"elem_index_ok = {result.elem_index_ok}")
        print(report)
        print()


def demo_json_report() -> None:
    """Пример сериализации CoverageReport в JSON для внешних систем."""
    print("=== JSON-отчёт ===")
    report = calc_data_path_coverage(BANKS_FORM_ELEMENTS)
    output = {
        "form": "Catalog/Банки/CatalogForm/ФормаЭлементаУправляемая",
        "coverage": report.to_dict(),
        "note": (
            "Синтетика: 11/11 = 100%. На реальной выгрузке УТ 10.3 — 11/14 = 78.6%: "
            "три поля (Код, Наименование, Родитель) не разрешены до issue #88."
        ),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    print()


def demo_form_class() -> None:
    """form_class в CoverageReport: объектная vs. сервисная форма (issue #98).

    Объектная форма имеет привязки Объект.*; агрегат считается по ней штатно.
    Сервисная форма (мастер, помощник, диалог) использует временные реквизиты
    формы вместо Объект.* — нулевое покрытие не является признаком проблемы.
    form_class позволяет агрегатору исключать сервисные формы из общего знаменателя.
    """
    print("=== form_class: объектная vs. сервисная форма (issue #98) ===")

    # Объектная форма — все поля данных привязаны к Объект.*
    report_obj = calc_data_path_coverage(
        BANKS_FORM_ELEMENTS,
        form_name="ФормаЭлемента",
    )
    print(f"Объектная (form_name='ФормаЭлемента'): {report_obj}")
    print(f"  form_class = {report_obj.form_class}")

    # Сервисная форма (мастер МЧД) — поля привязаны к временным реквизитам,
    # Объект.* отсутствует — это архитектурный паттерн платформы 1С, не баг.
    service_elements = [
        {"type": "Field",  "name": "ПолеМастера",    "data_path": None},
        {"type": "Field",  "name": "ДатаОформления", "data_path": None},
        {"type": "Field",  "name": "ФИОПодписанта",  "data_path": None},
        {"type": "Button", "name": "Далее",          "data_path": None},
        {"type": "Button", "name": "Назад",          "data_path": None},
        {"type": "Group",  "name": "Группа1",        "data_path": None},
    ]

    report_svc = calc_data_path_coverage(
        service_elements,
        form_name="ЧерновикМЧД",
    )
    print(f"Сервисная (form_name='ЧерновикМЧД'): {report_svc}")
    print(f"  form_class = {report_svc.form_class}")
    print(
        "\n  Нулевое покрытие сервисной формы — штатный результат.\n"
        "  Агрегатор исключает её из общего знаменателя."
    )
    print()


def demo_empty_tree_class() -> None:
    """form_class при пустом tree (issue #98, #100).

    parse_elem_json с пустым tree.json возвращает elem_index_ok=False или True
    (через extract_legacy_form_elements, PR #102). calc_coverage_from_elem_index
    для форм с elem_index_ok=False возвращает form_class="unknown" — такие формы
    исключаются из знаменателя агрегата.

    После PR #102 из 80 форм с пустым tree 49 переходят в elem_index_ok=True
    (ФормаЗаписи, ФормаЭлемента, формы отчётов/обработок).
    Оставшиеся 225 — ФормаСписка/ФормаВыбора с TabularField (#103).
    """
    print("=== form_class при пустом tree (issue #98, #100) ===")

    with tempfile.TemporaryDirectory() as tmp:
        form_dir = Path(tmp) / "InformationRegister" / "АдресныйКлассификатор"                    / "InformationRegisterForm" / "ФормаЗаписи"
        form_dir.mkdir(parents=True)

        # Минимальный elem.json с пустым tree — имитируем форму с нераспознанной
        # разметкой (бинарный формат, v8unpack не извлёк элементы).
        empty_payload = {"params": [], "props": [], "commands": [], "tree": [], "data": {}}
        (form_dir / "InformationRegisterForm.elem.json").write_text(
            json.dumps(empty_payload, ensure_ascii=False), encoding="utf-8"
        )

        result = parse_elem_json(form_dir)
        report = calc_coverage_from_elem_index(result, form_name="ФормаЗаписи")
        print(f"elem_index_ok = {result.elem_index_ok}")
        print(f"form_class    = {report.form_class}  (ожидается \'unknown\')")
        print(
            "  → форма исключена из знаменателя агрегата; "
            "причина: platform_object_name_unparsed"
        )
    print()


def main() -> None:
    demo_constants()
    demo_old_vs_new()
    demo_partial_coverage()
    demo_elem_index_wrapper()
    demo_json_report()
    demo_form_class()
    demo_empty_tree_class()


if __name__ == "__main__":
    main()
