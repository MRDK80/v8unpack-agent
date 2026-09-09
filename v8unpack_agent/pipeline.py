"""Распаковка форм как pre-step индексации.

Модуль даёт orchestration-контракт: функции компонует вызывающая сторона,
единой функции-фасада для всей индексации пакет не предоставляет. Это
превращает ``Form.bin`` из «слепого пятна» в обычный артефакт::

    подготовленное дерево выгрузки (dump_root)
      ├─► discover_form_sources()     # dump_root → FormBinSource с form_id
      ├─► unpack_all_forms(..., unpacker)  # Form.bin → текстовый слой (BSL виден)
      │        └─► parse_elem_json()   # elem.json → form_elements_index (best-effort)
      ├─► unpack_erf(..., unpacker)   # для внешних отчётов (.erf): текстовый слой
      │        └─► extract_skd_queries()  # СКД → skd_queries.json (best-effort)
      └─► update_forms_index(..., artifacts)  # JSON-карта актуальности по form_id

Полный входной контракт и ответственность распаковщика описаны в
``docs/pipeline.md``.

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
- **Граница пакета.** Внешняя индексация и RAG находятся вне scope пакета:
  модуль возвращает ``FormArtifact`` и ``FormsIndex``, а их передача внешнему
  индексатору — ответственность вызывающей стороны.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json
from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_identity import (
    FormBinSource,
    discover_form_sources,
    select_sources,
)
from v8unpack_agent.form_paths import form_root
from v8unpack_agent.forms_index import FormsIndex, FormsIndexEntry
from v8unpack_agent.skd_extractor import SkdResult, extract_skd_queries

# Функция распаковки одной формы: (source, unpacked_root) -> артефакт.
# Источник несёт канонический ``form_id``, имя формы и путь к ``Form.bin``
# (issue #226), поэтому одноимённые формы разных владельцев различимы.
# Конкретную реализацию (через v8unpack) инжектирует вызывающий код — модуль
# остаётся domain-neutral и тестируемым без платформы 1С.
FormUnpacker = Callable[[FormBinSource, Path], FormArtifact]

#: Устаревший протокол: (bin_path, unpacked_root, form_name) -> артефакт.
#: Используйте :func:`~v8unpack_agent.form_identity.adapt_legacy_unpacker`.
LegacyFormUnpacker = Callable[[Path, Path, str], FormArtifact]

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
    form_ids: Iterable[str] | None = None,
    form_names: Iterable[str] | None = None,
) -> list[FormArtifact]:
    """Распаковать все (или указанные) формы выгрузки в текстовый слой.

    Распаковщик получает канонический источник формы, поэтому одноимённые
    формы разных владельцев не затирают друг друга (issue #226). Отбор по
    ``form_ids`` каноничен; ``form_names`` оставлен для совместимости и при
    неоднозначном имени поднимает ``AmbiguousFormNameError``.

    Распаковщик не падает на частичных формах — он отдаёт ``FormArtifact`` с
    ``extraction_ok=False`` и предупреждениями, а пайплайн продолжает работу.
    """
    sources = select_sources(
        discover_form_sources(dump_root),
        form_ids=form_ids,
        form_names=form_names,
    )
    artifacts: list[FormArtifact] = []
    for source in sources:
        artifact = unpacker(source, unpacked_root)

        if not artifact.form_id or artifact.source is None:
            artifact = replace(artifact, form_id=source.form_id, source=source)

        elem_result: ElemIndexResult = parse_elem_json(
            form_root(unpacked_root, artifact.form_id)
        )

        if elem_result.elem_index_ok or elem_result.warnings:
            artifact = replace(
                artifact,
                elem_index_ok=elem_result.elem_index_ok,
                extraction_warnings=[
                    *artifact.extraction_warnings,
                    *elem_result.warnings,
                ],
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

    Источник берётся из самого артефакта, поэтому повторное обнаружение форм
    не выполняется и потеря по имени невозможна (issue #226). Ключ записи —
    ``form_id``. В индекс пишутся только относительные POSIX-пути: файл
    остаётся обезличенным и одинаковым на POSIX и NT.
    """
    idx = index or FormsIndex()
    for art in artifacts:
        source = art.source
        if source is None:
            continue
        bin_path = source.bin_path
        if not bin_path.exists():
            continue
        key = art.form_id or source.form_id
        froot = form_root(unpacked_root, key)
        unpacked_mtime = froot.stat().st_mtime if froot.exists() else 0.0
        try:
            rel_bin = bin_path.relative_to(dump_root).as_posix()
        except ValueError:
            rel_bin = bin_path.name
        try:
            rel_root = froot.relative_to(unpacked_root).as_posix()
        except ValueError:
            rel_root = key
        idx.upsert(
            key,
            FormsIndexEntry(
                bin_path=rel_bin,
                unpacked_root=rel_root,
                bin_mtime=bin_path.stat().st_mtime,
                unpacked_mtime=unpacked_mtime,
                extraction_ok=art.extraction_ok,
                warnings=list(art.extraction_warnings),
                form_id=key,
                form_name=art.name,
            ),
        )
    return idx
