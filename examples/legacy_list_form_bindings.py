"""Пример: привязки колонок legacy ФормаСписка/ФормаВыбора (#103).

Скрипт разбирает существующую директорию формы из выгрузки v8unpack и
показывает колонки TabularField, извлечённые безопасным fallback #103.
Исходные файлы не изменяются.

Запуск:

    python examples/legacy_list_form_bindings.py \
      /path/to/Object/ObjectForm/ФормаСписка

Для JSON-вывода:

    python examples/legacy_list_form_bindings.py FORM_DIR --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from v8unpack_agent.elem_parser import parse_elem_json

EXPECTED_SOURCE = "legacy_list_form_json"
EXPECTED_TYPE = "TabularFieldColumn"


def _legacy_list_columns(elements: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Вернуть только колонки, подтверждённые fallback #103."""
    return [
        element
        for element in elements
        if element.get("type") == EXPECTED_TYPE
        or element.get("source") == EXPECTED_SOURCE
    ]


def _build_report(form_dir: Path) -> dict[str, Any]:
    result = parse_elem_json(form_dir)
    columns = _legacy_list_columns(result.elements)
    extraction_source = getattr(result, "extraction_source", None)

    return {
        "form_dir": str(form_dir),
        "elem_index_ok": result.elem_index_ok,
        "extraction_source": extraction_source,
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
