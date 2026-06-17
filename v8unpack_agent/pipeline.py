"""Распаковка форм как pre-step индексации.

Встраивает распаковку в общий пайплайн индексации, а не вызывает её вручную.
Это превращает ``Form.bin`` из «слепого пятна» в обычный артефакт::

    index_cf(<путь_к_выгрузке>)
      └─► 1) unpack_all_forms()        # v8unpack по всем Form.bin → текстовый слой
           └─► 2) update_forms_index() # JSON-карта актуальности
                └─► 3) rag.rebuild()   # code_context() видит формы

Свойства схемы:

- **Идемпотентность.** Повторный запуск не перекладывает формы, у которых
  ``bin_mtime == unpacked_mtime`` — только новые/изменённые.
- **Отказоустойчивость.** Если по одной форме ``extraction_ok=False`` —
  пайплайн не падает, индекс честно помечает её как частичную.
- **Прозрачность для агента.** Со стороны индексации это просто ещё один
  источник текстов; агент не знает, что под капотом был бинарник.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_paths import form_root
from v8unpack_agent.forms_index import FormsIndex, FormsIndexEntry

# Функция распаковки одной формы: (bin_path, unpacked_root, form_name) -> артефакт.
# Конкретную реализацию (через v8unpack) инжектирует вызывающий код — модуль
# остаётся domain-neutral и тестируемым без платформы 1С.
FormUnpacker = Callable[[Path, Path, str], FormArtifact]


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
        artifacts.append(unpacker(bin_path, unpacked_root, name))
    return artifacts


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
