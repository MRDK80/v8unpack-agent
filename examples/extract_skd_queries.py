"""Извлечение запросов СКД из распакованного внешнего отчёта (.erf).

Второй шаг двухэтапной схемы для .erf:

    my_report.erf
      └─► v8unpack -E  →  текстовый слой (BSL виден)
           └─► extract_skd_queries.py  →  skd_queries.json (запросы СКД в тексте)

v8unpack распаковывает контейнер .erf так же, как .epf. Но внутри .erf живёт
Схема Компоновки Данных (СКД): запросы наборов данных, вычисляемые поля,
связи между наборами. Всё это лежит в файле ``metadata`` как сериализованная
строка и v8unpack его не разбирает. Данный скрипт вытаскивает запросы СКД
в читаемый JSON, чтобы агент мог делать семантический поиск по тексту запроса,
а не только по BSL-коду модуля.

Запуск (аргументы совпадают с вызовом из обработки 1С):

    python examples/extract_skd_queries.py \
        --unpack-dir path/to/unpacked/report \
        --output    path/to/unpacked/report/skd_queries.json

Скрипт некритичен для пайплайна: если metadata не содержит ожидаемых маркеров
(нестандартная сериализация, новая версия платформы), он завершается с кодом 1
и сообщением в stderr, не прерывая основной цикл выгрузки.

Все примеры синтетические. Реальные данные, базы 1С и внутренняя
инфраструктура не используются.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Маркеры СКД в файле metadata
# ---------------------------------------------------------------------------
# После v8unpack файл metadata содержит текстовое представление объекта 1С.
# Запросы наборов данных обрамлены специфичными тегами сериализации платформы.
# Паттерн ниже покрывает типовой формат платформ 8.2/8.3; при смене версии
# может потребоваться корректировка.
_QUERY_BLOCK_RE = re.compile(
    r'"DataSource"\s*:\s*"Query".*?"QueryText"\s*:\s*"(.*?)"',
    re.DOTALL,
)
# Запасной вариант — построчный поиск секций ВЫБРАТЬ/SELECT внутри metadata.
_SELECT_LINE_RE = re.compile(r"^\s*(ВЫБРАТЬ|SELECT)\b", re.IGNORECASE | re.MULTILINE)


def _unescape_1c(s: str) -> str:
    """Минимальный анэскейп 1С-строк (двойные кавычки → одинарные)."""
    return s.replace('""', '"')


def find_metadata_file(unpack_dir: Path) -> Path | None:
    """Найти файл metadata внутри распакованной директории.

    v8unpack кладёт metadata в корень распакованной структуры:
    ``<unpack_dir>/root/metadata`` или ``<unpack_dir>/metadata``.
    """
    for candidate in (
        unpack_dir / "root" / "metadata",
        unpack_dir / "metadata",
    ):
        if candidate.exists():
            return candidate
    # Рекурсивный fallback: первый файл с именем metadata
    found = next(unpack_dir.rglob("metadata"), None)
    return found


def extract_queries(metadata_path: Path) -> list[dict]:
    """Извлечь запросы СКД из файла metadata.

    Возвращает список словарей вида::

        [{"name": "Основной", "query": "ВЫБРАТЬ ..."}]

    Если ни один запрос не найден — возвращает пустой список.
    """
    text = metadata_path.read_text(encoding="utf-8", errors="replace")

    datasets: list[dict] = []

    # Попытка 1: структурный разбор по маркерам JSON-like сериализации
    for i, m in enumerate(_QUERY_BLOCK_RE.finditer(text), start=1):
        raw_query = _unescape_1c(m.group(1).replace("\\n", "\n"))
        datasets.append({"name": f"Dataset{i}", "query": raw_query.strip()})

    if datasets:
        return datasets

    # Попытка 2: эвристика — вырезать блоки начиная с ВЫБРАТЬ/SELECT
    # Используется как fallback для нестандартных сериализаций.
    blocks: list[str] = []
    current: list[str] = []
    in_block = False
    for line in text.splitlines():
        if _SELECT_LINE_RE.match(line):
            if current and in_block:
                blocks.append("\n".join(current).strip())
                current = []
            in_block = True
        if in_block:
            current.append(line)
    if current and in_block:
        blocks.append("\n".join(current).strip())

    return [
        {"name": f"Dataset{i}", "query": q}
        for i, q in enumerate(blocks, start=1)
        if q
    ]


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

    metadata_path = find_metadata_file(unpack_dir)
    if metadata_path is None:
        print(
            f"Ошибка: файл metadata не найден в {unpack_dir}. "
            "Убедитесь, что v8unpack -E выполнен перед запуском скрипта.",
            file=sys.stderr,
        )
        return 1

    datasets = extract_queries(metadata_path)
    if not datasets:
        print(
            f"Предупреждение: запросы СКД не найдены в {metadata_path}. "
            "Нестандартная сериализация или отчёт не содержит запросов.",
            file=sys.stderr,
        )
        # Пишем пустой JSON, не возвращаем ошибку — пайплайн продолжит работу.
        datasets = []

    report_name = args.report_name or unpack_dir.name
    output_data = build_output(report_name, datasets)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"skd_queries.json записан: {args.output} ({len(datasets)} наборов данных)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
