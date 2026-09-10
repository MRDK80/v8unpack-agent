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

Обезличенный детерминированный агрегат и проверка стабильности (#229):

    python examples/unindexed_forms_report.py --json --runs 2 /path/to/cf_export

Режим --json печатает только агрегированные счётчики: путей, имён форм,
UUID и содержимого файлов в нём нет. Cohort строится по кандидатам
*.elem.json, как и в историческом screening.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from v8unpack_agent.coverage_metric import calc_data_path_coverage
from v8unpack_agent.elem_parser import (
    PLATFORM_DYNAMIC_SOURCE_MARKER,
    UnindexedReason,
    classify_unindexed_form,
    parse_elem_json,
)
from v8unpack_agent.form_classifier import FormClass, classify_no_widgets_form

AGGREGATE_SCHEMA_VERSION = 1

TABULAR_FIELD_UUID = "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1"

UUID_OWN_1 = "3d446926-2fb8-11d7-85a2-0050bae0a772"
UUID_OWN_2 = "3d446928-2fb8-11d7-85a2-0050bae0a772"
UUID_ALIEN_1 = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_ALIEN_2 = "aaaaaaaa-0000-0000-0000-000000000002"



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
    PLATFORM_DYNAMIC_SOURCE_MARKER используется как маркер платформенного источника.
    """
    return {
        "form": [
            [
                [
                    [
                        TABULAR_FIELD_UUID,
                        "4",
                        [],
                        ["8", "0", "0", "100", "100", "1"],
                        ["14", f'"{PLATFORM_DYNAMIC_SOURCE_MARKER}"'],
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
        root, "Catalog/ДинамическийИсточник/CatalogForm/ФормаСписка", "CatalogForm",
        form_json=_form_json_skd_like(),
        catalog=_catalog_json((UUID_OWN_1, "Показатель")),
    ))

    return forms


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------
def _form_class_for_indexed(form_dir: Path, elements: list) -> str:
    """FormClass проиндексированной формы через канонический coverage-API."""
    try:
        params = inspect.signature(calc_data_path_coverage).parameters
        if "form_name" in params:
            report = calc_data_path_coverage(elements, form_name=form_dir.name)
        else:
            report = calc_data_path_coverage(elements)
        return str(report.form_class)
    except Exception:  # noqa: BLE001
        return str(FormClass.UNKNOWN)


def _form_class_for_unindexed(form_dir: Path, reason: UnindexedReason) -> str:
    """FormClass непроиндексированной формы: канон #98/#109/#112 без эвристик."""
    if reason is UnindexedReason.NO_TABULAR_NO_WIDGETS:
        try:
            return str(classify_no_widgets_form(form_dir.name, reason))
        except Exception:  # noqa: BLE001
            return str(FormClass.UNKNOWN)
    return str(FormClass.UNKNOWN)


def report_for_form(form_dir: Path) -> dict:
    result = parse_elem_json(form_dir)
    if result.elem_index_ok:
        return {
            "form": str(form_dir),
            "indexed": True,
            "form_class": _form_class_for_indexed(form_dir, result.elements),
        }

    info = classify_unindexed_form(form_dir, result)
    return {
        "form": str(form_dir),
        "indexed": False,
        "reason": info.reason.value,
        "detail": info.detail,
        "form_class": _form_class_for_unindexed(form_dir, info.reason),
    }


def canonical_json(payload: dict) -> str:
    """Детерминированное представление агрегата для подписи и сравнения."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def aggregate_signature(payload: dict) -> str:
    """sha256 канонического JSON, первые 16 hex."""
    body = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def build_aggregate(rows: list[dict], *, mode: str) -> dict:
    """Обезличенный агрегат: только счётчики, без путей, имён и UUID."""
    forms_total = len(rows)
    ok = sum(1 for row in rows if row["indexed"])
    failed = forms_total - ok
    excluded = 0

    reasons: Counter = Counter(
        row["reason"] for row in rows if not row["indexed"]
    )
    classes: Counter = Counter(row["form_class"] for row in rows)

    matrix: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["indexed"]:
            continue
        by_class = matrix.setdefault(row["reason"], {})
        by_class[row["form_class"]] = by_class.get(row["form_class"], 0) + 1

    if forms_total != ok + failed + excluded:
        raise SystemExit(
            "нарушен инвариант баланса: "
            f"{forms_total} != {ok} + {failed} + {excluded}"
        )

    failed_pct = round(100.0 * failed / forms_total, 4) if forms_total else 0.0

    payload: dict = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "cohort": {
            "mode": mode,
            "unit": "elem_json_candidate",
            "forms_total": forms_total,
            "elem_candidates": forms_total,
        },
        "elem_index": {
            "ok": ok,
            "failed": failed,
            "failed_pct": failed_pct,
            "excluded": excluded,
        },
        "form_class": dict(sorted(classes.items())),
        "unindexed_reason": {
            reason.value: reasons.get(reason.value, 0)
            for reason in UnindexedReason
        },
        "form_class_by_reason": {
            reason: dict(sorted(by_class.items()))
            for reason, by_class in sorted(matrix.items())
        },
    }
    payload["aggregate_signature"] = aggregate_signature(payload)
    return payload


def run_measurement(root: Path, *, mode: str, as_json: bool, runs: int) -> int:
    """Выполнить runs замеров на одном и том же входе и сверить подписи."""
    payloads: list[dict] = []
    for index in range(runs):
        counter, rows = report_for_export(root)
        payloads.append(build_aggregate(rows, mode=mode))
        if index == 0 and not as_json:
            print_report(counter, rows)

    if as_json:
        print(json.dumps(payloads[0], ensure_ascii=False, indent=2, sort_keys=True))

    signatures = sorted({payload["aggregate_signature"] for payload in payloads})
    if runs > 1:
        stream = sys.stderr if as_json else sys.stdout
        if len(signatures) == 1:
            print(f"детерминированность: OK, подпись {signatures[0]}", file=stream)
        else:
            print(
                "детерминированность: РАСХОЖДЕНИЕ, подписи "
                + ", ".join(signatures),
                file=stream,
            )
            return 1
    return 0


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
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="печатать только обезличенный детерминированный агрегат (#229)",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="сколько замеров выполнить на одном входе для проверки подписи (#229)",
    )
    args = parser.parse_args()

    if args.runs < 1:
        raise SystemExit("--runs должен быть не меньше 1")

    # Проверка, что все причины покрыты примером
    assert {r.value for r in UnindexedReason} >= {"unknown"}

    if args.export_root is not None:
        root = args.export_root.expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"Директория не найдена: {root}")
        return run_measurement(
            root, mode="prepared_cf_dump", as_json=args.as_json, runs=args.runs
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_demo_export(root)
        code = run_measurement(
            root, mode="synthetic_demo", as_json=args.as_json, runs=args.runs
        )

    if not args.as_json:
        print(
            "\nНи одна форма не получила data_path: classify_unindexed_form() "
            "только объясняет причину."
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
