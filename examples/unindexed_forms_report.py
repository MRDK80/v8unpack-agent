"""Пример: отчёт по неиндексируемым формам (issue #105, #107).

Пример полностью синтетический и самодостаточный: формы собираются во
временном каталоге по структуре реальной выгрузки. Реальные данные,
контейнеры 1С и внутренняя инфраструктура не используются.

Показано, что `classify_unindexed_form()` объясняет, почему форма осталась
с `elem_index_ok=False` после fallback #100 и #103:

* D — NO_TABULAR_NO_WIDGETS / NO_LEGACY_JSON: рядом с `.elem.json` нет
  большого JSON формы;
* C — NO_TABULAR_NO_WIDGETS: JSON есть, но нет ни TabularField,
  ни InputField/ComboBox — форма без виджетов данных;
  `classify_no_widgets_form()` (#109) уточняет, является ли такая форма
  сервисной (мастер/помощник) или просто пустой.
* A — TABULAR_FIELD_EMPTY_ATTR_MAP: TabularField есть, но карта реквизитов
  владельца пуста. После issue #108 сюда попадает только `CommonForm`
  (объекта-владельца нет по дизайну платформы). `ChartOfCharacteristicType`
  закрыт в #108: positions 7/8 header[0][1] идентичны `Catalog`.
* B1 — TABULAR_FIELD_PROGRAMMATIC_NO_DEFS: карта непуста, UUID колонок
  в неё не попадают и в модуле формы нет `Колонки.Добавить` — программная
  ТаблицаЗначений/ДеревоЗначений без объявлений (#107);
* B2 — TABULAR_FIELD_BSL_SOURCE_MISMATCH: `Колонки.Добавить` в модуле есть,
  но у другого источника (`ВыбранныеСтроки` vs `ТабличноеПоле`) —
  сопоставление по имени дало бы фантомные колонки (#107);
* B3 — TABULAR_FIELD_PLATFORM_DYNAMIC: колонки формирует платформа
  (СКД, диаграммы) — привязок нет by design (#107).

Резон TABULAR_FIELD_NO_UUID_HITS сохранён в enum для обратной
совместимости, но после #107 не возвращается.

Функция строго диагностическая: она не создаёт `data_path`, не добавляет
элементов и не изменяет переданный `ElemIndexResult`.

Запуск:

    python examples/unindexed_forms_report.py

Для отчёта по реальной выгрузке:

    python examples/unindexed_forms_report.py /path/to/cf_export
"""
from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from v8unpack_agent.elem_parser import (
    UnindexedReason,
    classify_unindexed_form,
    parse_elem_json,
)

TABULAR_FIELD_UUID = "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1"

UUID_OWN_1 = "3d446926-2fb8-11d7-85a2-0050bae0a772"
UUID_OWN_2 = "3d446928-2fb8-11d7-85a2-0050bae0a772"
UUID_ALIEN_1 = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_ALIEN_2 = "aaaaaaaa-0000-0000-0000-000000000002"

# UUID, который classify_unindexed_form распознаёт как платформенный источник
# (СКД / диаграмма). Конкретное значение зависит от реализации детектора B3 —
# замените на реальный UUID из вашей версии elem_parser, если тест падает.
UUID_SKD_SOURCE = "e3c0c9b0-59c5-4e5e-8a1e-000000000001"


# ---------------------------------------------------------------------------
# Синтетическая выгрузка
# ---------------------------------------------------------------------------
def _empty_elem_json() -> dict:
    """`.elem.json` с пустым tree — ни один fallback не сработает сам по себе."""
    return {"tree": [], "data": [], "props": []}


def _catalog_attribute(uuid: str, name: str) -> list:
    node = ["0", ["0", "0", uuid], f'"{name}"', ["ru", f'"{name}"'], '"(Общ)"']
    return [[["1", [["1", [node]]]]]]


def _catalog_json(*uuids_and_names: tuple[str, str]) -> dict:
    """Валидный production-layout владельца с секцией header."""
    return {
        "header": [
            None, None, None, None, None, None,
            "cf4abea7-37b2-11d4-940f-008048da11f9",
            str(len(uuids_and_names)),
            *[_catalog_attribute(uuid, name) for uuid, name in uuids_and_names],
        ],
    }


def _form_json_with_tabular_field(*column_uuids: str) -> dict:
    """Legacy JSON формы с виджетом TabularField и точными ссылками ["0", UUID]."""
    return {
        "form": [
            [
                [
                    [
                        TABULAR_FIELD_UUID,
                        "4",
                        [["0", uuid] for uuid in column_uuids],
                        ["8", "0", "0", "100", "100", "1"],
                        '"СправочникСписок"',
                    ]
                ]
            ]
        ]
    }


def _form_json_without_widgets() -> dict:
    """Legacy JSON формы без TabularField и без InputField/ComboBox."""
    return {"form": [[[["5f2d0a1e-0000-0000-0000-000000000000", "4", []]]]]}


def _form_json_skd_like() -> dict:
    """Legacy JSON формы с TabularField, источник которого — СКД / диаграмма.

    Колонки формирует платформа динамически; статический разбор невозможен.
    classify_unindexed_form() должен вернуть TABULAR_FIELD_PLATFORM_DYNAMIC (B3).
    UUID_SKD_SOURCE используется как маркер платформенного источника.
    """
    return {
        "form": [
            [
                [
                    [
                        TABULAR_FIELD_UUID,
                        "4",
                        [["0", UUID_SKD_SOURCE]],
                        ["8", "0", "0", "100", "100", "1"],
                        '"СКДСписок"',
                    ]
                ]
            ]
        ]
    }


def _write_form(root: Path, rel: str, form_file: str, *,
                form_json: dict | None,
                catalog: dict | None) -> Path:
    form_dir = root / rel
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{form_file}.elem.json").write_text(
        json.dumps(_empty_elem_json(), ensure_ascii=False), encoding="utf-8"
    )
    if form_json is not None:
        (form_dir / f"{form_file}.json").write_text(
            json.dumps(form_json, ensure_ascii=False), encoding="utf-8"
        )
    if catalog is not None:
        (form_dir.parent.parent / "Catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
        )
    return form_dir


def build_demo_export(root: Path) -> list[Path]:
    """Собрать по одной форме на каждую категорию #105, включая B3 (#107)."""
    forms = []

    # D — нет legacy *.json рядом с .elem.json
    forms.append(_write_form(
        root, "Catalog/БезJSON/CatalogForm/ФормаВыбора", "CatalogForm",
        form_json=None, catalog=None,
    ))

    # C — JSON есть, виджетов данных нет
    forms.append(_write_form(
        root, "Catalog/БезВиджетов/CatalogForm/ФормаПечати", "CatalogForm",
        form_json=_form_json_without_widgets(), catalog=None,
    ))

    # A — TabularField есть, карта реквизитов владельца пуста (CommonForm по дизайну)
    forms.append(_write_form(
        root, "Catalog/БезВладельца/CatalogForm/ФормаСписка", "CatalogForm",
        form_json=_form_json_with_tabular_field(UUID_OWN_1, UUID_OWN_2),
        catalog=None,
    ))

    # B1 — карта непуста, UUID чужие, объявлений колонок нет нигде
    forms.append(_write_form(
        root, "Catalog/ЧужиеUUID/CatalogForm/ФормаВыбора", "CatalogForm",
        form_json=_form_json_with_tabular_field(UUID_ALIEN_1, UUID_ALIEN_2),
        catalog=_catalog_json((UUID_OWN_1, "Город"), (UUID_OWN_2, "Адрес")),
    ))

    # B2 — колонки в BSL объявлены, но у другого источника (#107)
    mismatch = _write_form(
        root, "Catalog/ЧужойИсточник/CatalogForm/ФормаВыбора", "CatalogForm",
        form_json=_form_json_with_tabular_field(UUID_ALIEN_1),
        catalog=_catalog_json((UUID_OWN_1, "Город")),
    )
    (mismatch / "CatalogForm.obj.bsl").write_text(
        'Процедура ПриОткрытии()\n'
        '    ВыбранныеСтроки.Колонки.Добавить("Ссылка");\n'
        '    ВыбранныеСтроки.Колонки.Добавить("Пометка");\n'
        'КонецПроцедуры\n',
        encoding="utf-8",
    )
    forms.append(mismatch)

    # B3 — платформенный источник (СКД / диаграмма), колонки формирует
    # платформа динамически; статический разбор невозможен (#107)
    forms.append(_write_form(
        root, "Report/СводныйОтчёт/ReportForm/ФормаОтчёта", "ReportForm",
        form_json=_form_json_skd_like(),
        catalog=_catalog_json((UUID_OWN_1, "Показатель")),
    ))

    return forms


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------
def report_for_form(form_dir: Path) -> dict:
    result = parse_elem_json(form_dir)
    if result.elem_index_ok:
        return {"form": str(form_dir), "indexed": True}

    info = classify_unindexed_form(form_dir, result)
    return {
        "form": str(form_dir),
        "indexed": False,
        "reason": info.reason.value,
        "detail": info.detail,
    }


def report_for_export(root: Path) -> tuple[Counter, list[dict]]:
    counter: Counter = Counter()
    rows: list[dict] = []
    for elem_json in sorted(root.rglob("*.elem.json")):
        row = report_for_form(elem_json.parent)
        rows.append(row)
        counter["indexed" if row["indexed"] else row["reason"]] += 1
    return counter, rows


def print_report(counter: Counter, rows: list[dict]) -> None:
    total = sum(counter.values())
    print(f"Найдено форм: {total}")
    print("-" * 64)
    for key, count in counter.most_common():
        print(f"  {key:<32} {count:>5}")
    print("-" * 64)

    for row in rows:
        if row["indexed"]:
            continue
        print(f"\n{row['form']}")
        print(f"  причина: {row['reason']}")
        print(f"  детали : {row['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Отчёт по причинам, из-за которых формы не проиндексированы (#105)."
    )
    parser.add_argument(
        "export_root", nargs="?", type=Path,
        help="корень cf_export; без аргумента строится синтетическая выгрузка",
    )
    args = parser.parse_args()

    # Проверка, что все причины покрыты примером
    assert {r.value for r in UnindexedReason} >= {"unknown"}

    if args.export_root is not None:
        root = args.export_root.expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"Директория не найдена: {root}")
        counter, rows = report_for_export(root)
        print_report(counter, rows)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_demo_export(root)
        counter, rows = report_for_export(root)
        print_report(counter, rows)

    print(
        "\nНи одна форма не получила data_path: classify_unindexed_form() "
        "только объясняет причину."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
