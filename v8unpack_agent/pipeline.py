"""Распаковка форм как pre-step индексации.

Встраивает распаковку в общий пайплайн индексации, а не вызывает её вручную.
Это превращает ``Form.bin`` из «слепого пятна» в обычный артефакт::

    index_cf(<путь_к_выгрузке>)
      └─► 1) unpack_all_forms()       # v8unpack по всем Form.bin → текстовый слой
      │        └─► parse_elem_json()   # elem.json → form_elements_index (best-effort)
      ├─► 1') unpack_erf()            # для внешних отчётов (.erf): текстовый слой
      │        └─► extract_skd_queries()  # СКД → skd_queries.json (best-effort)
      └─► 2) update_forms_index()     # JSON-карта актуальности
           └─► 3) rag.rebuild()        # code_context() видит формы

Свойства схемы:

- **Идемпотентность.** Повторный запуск не перекладывает формы, у которых
  ``bin_mtime == unpacked_mtime`` — только новые/изменённые.
- **Отказоустойчивость.** Если по одной форме ``extraction_ok=False`` —
  пайплайн не падает, индекс честно помечает её как частичную.
- **Best-effort обогащение.** Разбор ``elem.json`` (структура формы) и
  извлечение СКД (система компоновки данных) — необязательные шаги. Их неудача
  не меняет ``extraction_ok``, а лишь оставляет ``elem_index_ok=False`` /
  ``skd_extracted=False`` и дополняет предупреждения.
- **Прозрачность для агента.** Со стороны индексации это просто ещё один
  источник текстов; агент не знает, что под капотом был бинарник.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_paths import form_root
from v8unpack_agent.forms_index import FormsIndex, FormsIndexEntry

from v8unpack_agent.skd_extractor import SkdResult, extract_skd_queries
from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json

# Функция распаковки одной формы: (bin_path, unpacked_root, form_name) -> артефакт.
# Конкретную реализацию (через v8unpack) инжектирует вызывающий код — модуль
# остаётся domain-neutral и тестируемым без платформы 1С.
FormUnpacker = Callable[[Path, Path, str], FormArtifact]

# Функция распаковки .erf-файла: (erf_path, unpacked_root) -> FormArtifact.
ErfUnpacker = Callable[[Path, Path], FormArtifact]


def discover_form_bins(dump_root: Path) -> dict[str, Path]:
    """Найти все ``Form.bin`` в выгрузке и сопоставить им имена форм.

    Имя формы берётся из структуры ``.../Forms/<ИмяФормы>/Ext/Form.bin``.
    """
    result: dict[str, Path] = {}
    for bin_path in sorted(dump_root.rglob("Form.bin")):
        # .../Forms/<ИмяФормы>/Ext/Form.bin  ->  parents[1].name == <ИмяФормы>
        parts = bin_path.parts
        if "Forms" in parts:
            idx = len(parts) - 1 - parts[::-1].index("Forms")
            if idx + 1 < len(parts):
                form_name = parts[idx + 1]
                result[form_name] = bin_path
    return result


def unpack_all_forms(
    dump_root: Path,
    unpacked_root: Path,
    unpacker: FormUnpacker,
    *,
    form_names: Iterable[str] | None = None,
) -> list[FormArtifact]:
    """Распаковать все (или указанные) формы выгрузки в текстовый слой.

    Распаковщик не падает на частичных формах — он отдаёт ``FormArtifact`` с
    ``extraction_ok=False`` и предупреждениями, а пайплайн продолжает работу.
    """
    bins = discover_form_bins(dump_root)
    selected = (
        {n: bins[n] for n in form_names if n in bins}
        if form_names is not None
        else bins
    )
    artifacts: list[FormArtifact] = []
    for name, bin_path in sorted(selected.items()):
        artifact = unpacker(bin_path, unpacked_root, name)

        elem_result: ElemIndexResult = parse_elem_json(form_root(unpacked_root, name))

        if elem_result.elem_index_ok or elem_result.warnings:
            artifact = replace(
                artifact,
                elem_index_ok=elem_result.elem_index_ok,
                extraction_warnings=[*artifact.extraction_warnings, *elem_result.warnings],
            )

        artifacts.append(artifact)

    return artifacts


def unpack_erf(
    erf_path: Path,
    unpacked_root: Path,
    unpacker: ErfUnpacker,
) -> FormArtifact:
    """Распаковать внешний отчёт (.erf) и извлечь запросы СКД.

    Выполняет двухэтапную схему:
    1. Вызывает unpacker(erf_path, unpacked_root) — получает текстовый слой (BSL).
    2. Вызывает extract_skd_queries(unpacked_root) — best-effort, не влияет на extraction_ok.

    Если СКД не извлечена, FormArtifact.skd_extracted остаётся False.
    """
    artifact = unpacker(erf_path, unpacked_root)

    skd_result: SkdResult = extract_skd_queries(unpacked_root)

    if skd_result.skd_extracted:
        artifact = replace(artifact, skd_extracted=True)

    return artifact


def update_forms_index(
    dump_root: Path,
    unpacked_root: Path,
    artifacts: Iterable[FormArtifact],
    *,
    index: FormsIndex | None = None,
) -> FormsIndex:
    """Обновить JSON-карту актуальности по результатам распаковки.

    ``bin_mtime`` берётся из исходного ``Form.bin``, ``unpacked_mtime`` — из
    каталога распакованной формы. Если ``bin_mtime > unpacked_mtime``, форма
    считается устаревшей (см. :func:`is_form_stale`).
    """
    idx = index or FormsIndex()
    bins = discover_form_bins(dump_root)
    for art in artifacts:
        bin_path = bins.get(art.name)
        if bin_path is None or not bin_path.exists():
            continue
        froot = form_root(unpacked_root, art.name)
        unpacked_mtime = froot.stat().st_mtime if froot.exists() else 0.0
        idx.upsert(
            art.name,
            FormsIndexEntry(
                bin_path=str(bin_path.relative_to(dump_root)),
                unpacked_root=str(froot),
                bin_mtime=bin_path.stat().st_mtime,
                unpacked_mtime=unpacked_mtime,
                extraction_ok=art.extraction_ok,
                warnings=list(art.extraction_warnings),
            ),
        )
    return idx
