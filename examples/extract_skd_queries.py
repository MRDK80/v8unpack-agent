"""Извлечение запросов СКД из распакованного внешнего отчёта (.erf).

Второй шаг двухэтапной схемы для .erf:

    my_report.erf
      -> v8unpack -E: текстовый слой (BSL виден)
         -> extract_skd_queries.py: skd_queries.json (запросы СКД в тексте)

Пример демонстрирует публичный API пакета (#253): разбор выполняет
``v8unpack_agent.skd_extractor.extract_skd_queries()``, собственного парсера
в примере больше нет.

Ранее файл разбирал сериализованный файл ``metadata`` собственными
регулярными выражениями. Проверка на реальной выгрузке ``.erf`` показала, что
файла ``metadata`` в ней нет вовсе: схема компоновки данных лежит в
v8-контейнере ``Template/<ИмяСхемы>/Template.bin``, который и читает
публичная функция.

Побочный эффект публичной функции: помимо ``--output`` она всегда пишет
``skd_queries.json`` в корень переданной выгрузки.

Запуск (аргументы совпадают с вызовом из обработки 1С):

    python examples/extract_skd_queries.py --unpack-dir DIR --output DIR/skd_queries.json

Скрипт некритичен для пайплайна: если контейнер схемы найден, но запросы не
извлечены, он пишет пустой JSON и завершается с кодом 0; при отсутствии
каталога выгрузки или контейнера схемы — с кодом 1 и сообщением в stderr, не
прерывая основной цикл выгрузки.

Все примеры синтетические. Реальные данные, базы 1С и внутренняя
инфраструктура не используются.

Требуемые данные: скрипт работает только на реальной распакованной выгрузке
``.erf`` и требует обязательных ``--unpack-dir`` и ``--output``. Без выгрузки
запуск невозможен: ``python examples/extract_skd_queries.py`` без аргументов
штатно завершается ошибкой argparse. Это ожидаемое поведение, а не дефект.

Категория: пример на реальной выгрузке.
Входные данные: --unpack-dir, --output — распакованный внешний
отчёт .erf с контейнером Template/<ИмяСхемы>/Template.bin.
Ожидаемый результат: обезличенный агрегат в stdout, RC=0;
локальные имена и CSV не публикуются и не коммитятся.
Поведение без данных: штатная ошибка argparse (RC=2) —
это ожидаемое поведение, а не дефект; в автоматический
прогон файл не входит.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from v8unpack_agent.skd_extractor import extract_skd_queries

_TEMPLATE_BIN = "Template.bin"


def has_skd_container(unpack_dir: Path) -> bool:
    """Проверить наличие контейнера схемы компоновки данных в выгрузке.

    Разбор контейнера выполняет публичная функция. Здесь проверяется только
    наличие носителя, чтобы отличить «выгрузка без СКД» (RC=1) от «схема
    есть, запросов нет» (RC=0 и пустой JSON).
    """
    return any(unpack_dir.rglob(_TEMPLATE_BIN))


def build_output(report_name: str, datasets: list[dict]) -> dict:
    """Собрать итоговую структуру skd_queries.json."""
    return {
        "report": report_name,
        "datasets": datasets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Извлечь запросы СКД из распакованного .erf в JSON."
    )
    parser.add_argument(
        "--unpack-dir",
        required=True,
        type=Path,
        help="Директория с результатом v8unpack -E для .erf файла",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Путь к выходному skd_queries.json",
    )
    parser.add_argument(
        "--report-name",
        default=None,
        help="Имя отчёта для поля 'report' в JSON (по умолчанию — имя директории)",
    )
    args = parser.parse_args(argv)

    unpack_dir: Path = args.unpack_dir
    if not unpack_dir.exists():
        print(f"Ошибка: директория не найдена: {unpack_dir}", file=sys.stderr)
        return 1

    if not has_skd_container(unpack_dir):
        print(
            f"Ошибка: {_TEMPLATE_BIN} не найден в {unpack_dir}. "
            "Убедитесь, что v8unpack -E выполнен и отчёт содержит схему "
            "компоновки данных.",
            file=sys.stderr,
        )
        return 1

    result = extract_skd_queries(unpack_dir)
    for warning in result.warnings:
        print(f"Предупреждение: {warning}", file=sys.stderr)

    datasets: list[dict] = list(result.datasets)
    if not datasets:
        print(
            "Предупреждение: запросы СКД не найдены. "
            "Нестандартная сериализация или отчёт не содержит запросов.",
            file=sys.stderr,
        )

    report_name: str = args.report_name or unpack_dir.name
    output_data = build_output(report_name, datasets)

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"skd_queries.json записан: {output_path} ({len(datasets)} наборов данных)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
