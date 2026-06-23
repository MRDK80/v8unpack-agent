"""Извлечение запросов СКД из распакованного .erf-файла.

Второй шаг двухэтапной схемы обработки внешних отчётов::

    my_report.erf
      └─► v8unpack.extract() → текстовый слой (BSL виден)
           └─► skd_extractor.extract_skd_queries() → skd_queries.json

Если ``skd_extracted=False`` — агент видит только BSL модуля отчёта.
Это не ошибка пайплайна, а сигнал о неполноте контекста.

Модуль использует regex по тексту XML (best-effort).
Ошибка СКД-шага не влияет на ``extraction_ok`` основного артефакта.
"""
from __future__ import annotations

import json
import re
import warnings as _warnings_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_TEMPLATE_BIN = "Template.bin"

# v8-контейнер: 8 байт заголовка, затем UTF-8 BOM (3 байта) + XML
_UTF8_BOM = b"\xef\xbb\xbf"

# Regex: ищем запросы ВЫБРАТЬ в атрибутах и текстовых узлах XML
# Захватываем всё до конца «строки запроса» (до кавычки или конца значения)
_QUERY_RE = re.compile(
    r"(ВЫБРАТЬ\b[^<\"]{10,})",
    re.IGNORECASE | re.DOTALL,
)

# Имя датасета ищем в ближайшем атрибуте Name/Имя перед блоком запроса
_DATASET_NAME_RE = re.compile(
    r'(?:Name|Имя)\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)

_SKD_QUERIES_JSON = "skd_queries.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class SkdResult:
    """Результат попытки извлечения запросов СКД.

    Attributes
    ----------
    skd_extracted:
        ``True`` — хотя бы один датасет с запросом найден и сохранён.
    datasets:
        Список словарей ``{"name": str, "query": str}``.
    warnings:
        Диагностические сообщения (неполнота, отсутствие файла и т.п.).
    """

    skd_extracted: bool
    datasets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SkdBatchResult:
    skd_extracted: bool
    results: list[SkdResult]
    warnings: list[str] = field(default_factory=list)


def extract_all_skd_queries(unpacked_root: Path) -> SkdBatchResult:
    warnings: list[str] = []
    results: list[SkdResult] = []

    template_paths = sorted(unpacked_root.rglob("Template.bin"))

    if not template_paths:
        return SkdBatchResult(
            skd_extracted=False,
            results=[],
            warnings=[f"Template.bin не найден в {unpacked_root}"],
        )

    for template_path in template_paths:
        report_root = _guess_report_root(template_path)
        result = extract_skd_queries(report_root)

        if not result.skd_extracted:
            warnings.extend(f"{report_root}: {w}" for w in result.warnings)

        results.append(result)

    return SkdBatchResult(
        skd_extracted=any(r.skd_extracted for r in results),
        results=results,
        warnings=warnings,
    )


def _guess_report_root(template_path: Path) -> Path:
    # Конвенция: Report/<Имя>/Template/<ИмяСКД>/Template.bin
    parts = template_path.parts
    if "Report" in parts:
        i = parts.index("Report")
        if len(parts) > i + 1:
            return Path(*parts[: i + 2])
    # Fallback: <Имя>/Template/<СКД>/Template.bin → корень на 3 уровня выше
    # parents[0] = <СКД>/, parents[1] = Template/, parents[2] = <Имя>/
    if len(template_path.parents) >= 3:
        return template_path.parents[2]
    return template_path.parent


def extract_skd_queries(unpacked_root: Path) -> SkdResult:
    """Извлечь запросы СКД из распакованного .erf.

    Parameters
    ----------
    unpacked_root:
        Корень директории, в которую был распакован .erf-файл.

    Returns
    -------
    SkdResult
        Всегда возвращает результат (не бросает исключений).
        При любой ошибке ``skd_extracted=False``, детали — в ``warnings``.
    """
    result_warnings: list[str] = []

    try:
        template_path = _find_template_bin(unpacked_root)
    except _TemplateNotFoundError as exc:
        return SkdResult(
            skd_extracted=False,
            warnings=[str(exc)],
        )

    try:
        xml_text = _read_xml_from_v8_container(template_path, result_warnings)
    except Exception as exc:  # noqa: BLE001
        return SkdResult(
            skd_extracted=False,
            warnings=[f"Не удалось прочитать {template_path.name}: {exc}"],
        )

    if xml_text is None:
        return SkdResult(skd_extracted=False, warnings=result_warnings)

    datasets = _extract_datasets(xml_text, result_warnings)

    if not datasets:
        result_warnings.append(
            "Запросы ВЫБРАТЬ в Template.bin не найдены (regex не дал совпадений)."
        )
        return SkdResult(skd_extracted=False, warnings=result_warnings)

    output_path = unpacked_root.parent / _SKD_QUERIES_JSON
    try:
        output_path.write_text(
            json.dumps(datasets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        result_warnings.append(f"Не удалось сохранить {_SKD_QUERIES_JSON}: {exc}")
        return SkdResult(skd_extracted=False, warnings=result_warnings)

    return SkdResult(
        skd_extracted=True,
        datasets=datasets,
        warnings=result_warnings,
    )


# ---------------------------------------------------------------------------
# Внутренние функции
# ---------------------------------------------------------------------------


class _TemplateNotFoundError(FileNotFoundError):
    pass


def _find_template_bin(unpacked_root: Path) -> Path:
    """Рекурсивно найти Template.bin в распакованной директории."""
    if not unpacked_root.is_dir():
        raise _TemplateNotFoundError(
            f"Директория не существует или не является директорией: {unpacked_root}"
        )

    candidates: list[Path] = list(unpacked_root.rglob(_TEMPLATE_BIN))
    if not candidates:
        raise _TemplateNotFoundError(
            f"{_TEMPLATE_BIN} не найден в {unpacked_root}"
        )

    if len(candidates) > 1:
        # Несколько Template.bin — возможно вложенные отчёты; берём первый
        pass  # warnings добавим на уровне вызывающего при необходимости

    return candidates[0]


def _read_xml_from_v8_container(
    path: Path,
    warnings: list[str],
) -> str | None:
    """Прочитать XML из v8-контейнера.

    Размер заголовка контейнера варьируется (8 или 24 байта) в зависимости
    от версии платформы 1С. Поэтому начало XML определяется динамически:
    ищем UTF-8 BOM (``ef bb bf``), а при его отсутствии — ``<?xml``.
    """
    raw = path.read_bytes()

    # Ищем BOM — он всегда предшествует XML в v8-контейнере.
    bom_pos = raw.find(_UTF8_BOM)
    if bom_pos != -1:
        payload = raw[bom_pos + len(_UTF8_BOM):]
    else:
        # Fallback: BOM отсутствует, ищем начало XML-декларации напрямую.
        xml_start = raw.find(b"<?xml")
        if xml_start == -1:
            warnings.append(
                f"{path.name}: ни UTF-8 BOM, ни XML-заголовок не найдены "
                f"(размер файла: {len(raw)} байт)."
            )
            return None
        payload = raw[xml_start:]

    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        warnings.append(f"{path.name}: ошибка декодирования UTF-8: {exc}")
        return None


def _extract_datasets(
    xml_text: str,
    warnings: list[str],
) -> list[dict]:
    """Найти датасеты (имя + запрос) в XML-тексте СКД."""
    datasets: list[dict] = []
    seen_queries: set[str] = set()

    for match in _QUERY_RE.finditer(xml_text):
        raw_query = match.group(1).strip()

        # Нормализуем пробелы
        query = re.sub(r"\s+", " ", raw_query)

        if query in seen_queries:
            continue
        seen_queries.add(query)

        # Пытаемся найти имя датасета в предшествующем контексте (до 500 символов)
        start = max(0, match.start() - 500)
        context = xml_text[start : match.start()]
        name_match = _DATASET_NAME_RE.search(context)
        name = name_match.group(1) if name_match else f"Dataset{len(datasets) + 1}"

        datasets.append({"name": name, "query": query})

    return datasets
