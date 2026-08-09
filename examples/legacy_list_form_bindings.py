"""Пример: привязки колонок legacy ФормаСписка/ФормаВыбора (#103, #105, #107).

Скрипт разбирает существующую директорию формы из выгрузки v8unpack и
показывает колонки TabularField, извлечённые безопасным fallback #103.
Если форма не проиндексирована, печатается причина из
`classify_unindexed_form()` (#105). Исходные файлы не изменяются.

Источник колонки виден в поле `source`:

* `legacy_list_form_json` — UUID-привязка из блока 20 либо Pattern-ссылка
  `["#", UUID]` (#107);
* `legacy_list_form_bsl` — имя взято из `Колонки.Добавить` в модуле формы.

Запуск:

    python examples/legacy_list_form_bindings.py \
      /path/to/Object/ObjectForm/ФормаСписка

Для JSON-вывода:

    python examples/legacy_list_form_bindings.py FORM_DIR --json

Требуемые данные: скрипт работает только на реальной выгрузке v8unpack и
требует обязательного позиционного ``FORM_DIR``. Без выгрузки запуск
невозможен: вызов без аргументов штатно завершается ошибкой argparse.
Это ожидаемое поведение, а не дефект.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from v8unpack_agent.elem_parser import classify_unindexed_form, parse_elem_json

EXPECTED_SOURCES = {"legacy_list_form_json", "legacy_list_form_bsl"}
EXPECTED_TYPE = "TabularFieldColumn"


def _legacy_list_columns(elements: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Вернуть только колонки, подтверждённые fallback #103."""
    return [
        element
        for element in elements
        if element.get("type") == EXPECTED_TYPE
        or element.get("source") in EXPECTED_SOURCES
    ]


def _build_report(form_dir: Path) -> dict[str, Any]:
    result = parse_elem_json(form_dir)
    columns = _legacy_list_columns(result.elements)
    extraction_source = getattr(result, "extraction_source", None)

    unindexed_reason = None
    unindexed_detail = None
    if not result.elem_index_ok:
        # Диагностика #105: причина, по которой форма осталась без разметки.
        # Вызов не создаёт data_path и не изменяет result.
        info = classify_unindexed_form(form_dir, result)
        unindexed_reason = info.reason.value
        unindexed_detail = info.detail

    return {
        "form_dir": str(form_dir),
        "elem_index_ok": result.elem_index_ok,
        "extraction_source": extraction_source,
        "unindexed_reason": unindexed_reason,
        "unindexed_detail": unindexed_detail,
        "elements": len(result.elements),
        "legacy_list_columns": len(columns),
        "columns": [
            {
                "name": column.get("name"),
                "type": column.get("type"),
                "data_path": column.get("data_path"),
                "source": column.get("source"),
            }
            for column in columns
        ],
        "warnings": list(result.warnings),
    }


def _print_text(report: dict[str, Any]) -> None:
    print(f"Форма: {report['form_dir']}")
    print(f"elem_index_ok: {report['elem_index_ok']}")
    print(f"extraction_source: {report['extraction_source']!r}")
    print(f"Всего элементов: {report['elements']}")
    print(f"Колонок TabularField: {report['legacy_list_columns']}")

    columns = report["columns"]
    if columns:
        print("\nПодтверждённые привязки:")
        for column in columns:
            name = column["name"] or "<без имени>"
            data_path = column["data_path"] or "— (привязка не подтверждена)"
            print(f"  {name:<32} {data_path}")
    else:
        print("\nFallback #103 не вернул колонок для этой формы.")

    if report["unindexed_reason"]:
        print(f"\nПричина (#105): {report['unindexed_reason']}")
        print(f"  {report['unindexed_detail']}")

    if report["warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Показать привязки колонок legacy ФормаСписка/ФормаВыбора, "
            "извлечённые fallback #103."
        )
    )
    parser.add_argument(
        "form_dir",
        type=Path,
        help="директория формы, содержащая *.elem.json и legacy *.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="вывести машиночитаемый JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    form_dir = args.form_dir.expanduser().resolve()

    if not form_dir.is_dir():
        raise SystemExit(f"Директория формы не найдена: {form_dir}")

    report = _build_report(form_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)

    return 0 if report["elem_index_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
