"""Извлечение запросов СКД из распакованного .erf-файла.

Второй шаг двухэтапной схемы обработки внешних отчётов::

    my_report.erf
      └─► v8unpack.extract() → текстовый слой (BSL виден)
           └─► skd_extractor.extract_skd_queries() → skd_queries.json

Если ``skd_extracted=False`` — агент видит только BSL модуля отчёта.
Это не ошибка пайплайна, а сигнал о неполноте контекста. Причину
различает поле ``SkdResult.status`` (issue #287): схема без текстового
запроса, макет другого вида или доказанная ошибка чтения/разбора.

Модуль использует regex по тексту XML (best-effort).
Ошибка СКД-шага не влияет на ``extraction_ok`` основного артефакта.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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

# Машинные статусы результата (issue #287)
STATUS_QUERY_EXTRACTED = "query_extracted"
STATUS_SCHEMA_WITHOUT_QUERY = "schema_without_query"
STATUS_SCHEMA_WITHOUT_DATASETS = "schema_without_datasets"
STATUS_NOT_SCHEMA = "not_schema"
STATUS_ERROR = "error"

_SCHEMA_ROOTS = frozenset({"SchemaFile", "DataCompositionSchema"})
_ROOT_TAG_RE = re.compile(r"<([A-Za-z_][\w.:-]*)[\s>/]")
_DATASET_TAG_RE = re.compile(r"<dataSet\b", re.IGNORECASE)
_QUERY_TAG_RE = re.compile(r"<query>\s*[^<\s]", re.IGNORECASE)


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
    status:
        Машинный статус (issue #287): ``query_extracted``,
        ``schema_without_query`` (валидная СКД, наборы без текста запроса,
        например из объекта), ``schema_without_datasets``, ``not_schema``
        (макет другого вида) или ``error`` (доказанный сбой либо
        нераспознанный случай). Если не задан — выводится из
        ``skd_extracted``.
    template_path:
        Путь к разобранному ``Template.bin`` (``None``, если файл не найден).
    """

    skd_extracted: bool
    datasets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = ""
    template_path: str | None = None

    def __post_init__(self) -> None:
        if not self.status:
            self.status = STATUS_QUERY_EXTRACTED if self.skd_extracted else STATUS_ERROR


@dataclass
class SkdBatchResult:
    skd_extracted: bool
    results: list[SkdResult]
    warnings: list[str] = field(default_factory=list)


def extract_all_skd_queries(
    unpacked_root: str | Path,
    *,
    write_json: bool = True,
) -> SkdBatchResult:
    """Разобрать все ``Template.bin`` под корнем.

    ``write_json=True`` (по умолчанию) пишет ``skd_queries.json`` в корень
    каждого объекта. ``write_json=False`` не создаёт и не изменяет файлов:
    результат доступен только в ``SkdBatchResult`` (issue #335).
    """
    unpacked_root = Path(unpacked_root)
    warnings: list[str] = []
    results: list[SkdResult] = []

    template_paths = sorted(unpacked_root.rglob("Template.bin"))

    if not template_paths:
        return SkdBatchResult(
            skd_extracted=False,
            results=[],
            warnings=[f"Template.bin не найден в {unpacked_root}"],
        )

    # Каждый Template.bin разбирается сам (issue #287): объект может содержать
    # несколько макетов. skd_queries.json пишется один на корень объекта и
    # объединяет запросы всех его макетов.
    groups: dict[Path, list[SkdResult]] = {}
    for template_path in template_paths:
        report_root = _guess_report_root(template_path)
        result = _extract_template(template_path)
        groups.setdefault(report_root, []).append(result)
        results.append(result)

    for report_root, group in groups.items():
        datasets = [d for r in group for d in r.datasets]
        if datasets and write_json:
            error = _write_datasets(report_root, datasets)
            if error is not None:
                for r in group:
                    if r.datasets:
                        r.skd_extracted = False
                        r.status = STATUS_ERROR
                        r.warnings.append(error)
        for r in group:
            if not r.skd_extracted:
                warnings.extend(f"{report_root}: {w}" for w in r.warnings)

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


def extract_skd_queries(
    unpacked_root: str | Path,
    *,
    write_json: bool = True,
) -> SkdResult:
    """Извлечь запросы СКД из распакованного .erf.

    Parameters
    ----------
    unpacked_root:
        Корень директории, в которую был распакован .erf-файл.
        Принимает как ``str``, так и ``pathlib.Path``.
    write_json:
        ``True`` (по умолчанию) — записать ``skd_queries.json`` в
        ``unpacked_root``; ``False`` — ничего не записывать (issue #335).

    Returns
    -------
    SkdResult
        Всегда возвращает результат (не бросает исключений).
        При любой ошибке ``skd_extracted=False``, детали — в ``warnings``.
    """
    unpacked_root = Path(unpacked_root)

    try:
        template_path = _find_template_bin(unpacked_root)
    except _TemplateNotFoundError as exc:
        return SkdResult(
            skd_extracted=False,
            warnings=[str(exc)],
            status=STATUS_ERROR,
        )

    result = _extract_template(template_path)
    if result.datasets and write_json:
        error = _write_datasets(unpacked_root, result.datasets)
        if error is not None:
            result.warnings.append(error)
            result.skd_extracted = False
            result.status = STATUS_ERROR
    return result


def _write_datasets(root: Path, datasets: list[dict]) -> str | None:
    """Записать skd_queries.json в корень; вернуть текст ошибки или None."""
    try:
        (root / _SKD_QUERIES_JSON).write_text(
            json.dumps(datasets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return f"Не удалось сохранить {_SKD_QUERIES_JSON}: {exc}"
    return None


def _extract_template(template_path: Path) -> SkdResult:
    """Разобрать один Template.bin без записи на диск и классифицировать."""
    result_warnings: list[str] = []
    path_text = str(template_path)

    def done(status: str, datasets: list[dict] | None = None) -> SkdResult:
        return SkdResult(
            skd_extracted=status == STATUS_QUERY_EXTRACTED,
            datasets=datasets or [],
            warnings=result_warnings,
            status=status,
            template_path=path_text,
        )

    try:
        xml_text = _read_xml_from_v8_container(template_path, result_warnings)
    except Exception as exc:  # noqa: BLE001
        result_warnings.append(f"Не удалось прочитать {template_path.name}: {exc}")
        return done(STATUS_ERROR)

    if xml_text is None:
        return done(STATUS_ERROR)

    datasets = _extract_datasets(xml_text, result_warnings)
    if datasets:
        return done(STATUS_QUERY_EXTRACTED, datasets)

    result_warnings.append(
        "Запросы ВЫБРАТЬ в Template.bin не найдены (regex не дал совпадений)."
    )
    root = _root_tag(xml_text)
    if root is None:
        if xml_text.lstrip().startswith("{"):
            # Скобочный формат 1С без XML-схемы — макет другого вида.
            result_warnings.append(f"{template_path.name}: макет другого вида (не СКД).")
            return done(STATUS_NOT_SCHEMA)
        result_warnings.append(f"{template_path.name}: корневой элемент XML не найден.")
        return done(STATUS_ERROR)
    if root not in _SCHEMA_ROOTS:
        result_warnings.append(
            f"{template_path.name}: макет другого вида (корень {root}, не СКД)."
        )
        return done(STATUS_NOT_SCHEMA)
    if _QUERY_TAG_RE.search(xml_text):
        # Текст запроса есть, но не распознан — не объявлять успехом.
        return done(STATUS_ERROR)
    if not _DATASET_TAG_RE.search(xml_text):
        result_warnings.append(f"{template_path.name}: СКД без наборов данных.")
        return done(STATUS_SCHEMA_WITHOUT_DATASETS)
    result_warnings.append(
        f"{template_path.name}: СКД без текстового запроса (набор из объекта или объединение)."
    )
    return done(STATUS_SCHEMA_WITHOUT_QUERY)


def _root_tag(xml_text: str) -> str | None:
    """Имя корневого элемента: первый тег, кроме объявления и комментариев."""
    for match in _ROOT_TAG_RE.finditer(xml_text):
        name = match.group(1)
        if name.lower() == "xml":
            continue
        return name.split(":")[-1]
    return None


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
