"""managed_forms — discovery форм по *.elem.json.

Реализует issue #55.

v8unpack 1.2.11 материализует каждую форму (обычную и управляемую) как
``*.elem.json`` в каталоге формы. Реальный носитель структуры —
``*.elem.json``; ``Form.xml`` в pipeline v8unpack-agent не используется.

По итогам проверки на 2216 формах реальной конфигурации установлено: все
формы (обычные и управляемые) получают ``*.elem.json`` независимо от типа.
Классификация ordinary/managed не входит в scope этого модуля — см. issue #56.

Гипотеза для issue #56 (зафиксирована как наблюдение, не как гарантированный
контракт):
  ``props[0].raw[0] == "9"``  →  управляемая форма
  ``props[0].raw[0]`` — список (напр. ``["0"]``) →  обычная форма
Эмпирически проверено на выборке из 2216 форм v8unpack 1.2.11: 857 управляемых,
1076 обычных, 283 — неопределено. Признак хрупкий (реверс-инжиниринг формата
без официальной спецификации). Надёжная реализация — отдельный issue #56.

Layout-варианты каталогов форм, поддержанные discovery (устойчиво к глубине):

- ``<root>/…/<ContainerForm>/<form_name>/*.elem.json``  (любая глубина)

Типичные примеры:

- ``<root>/<object_type>/<object_name>/CatalogForm/<form_name>/``  — 4 уровня
- ``<root>/<object_type>/<object_name>/Form/<form_name>/``         — 4 уровня
- ``<root>/<object_type>/<object_name>/ReportForm/<form_name>/``   — 4 уровня
- ``<root>/CommonForm/<form_name>/``                               — 3 уровня
- ``<root>/<object_name>/Form/<form_name>/``   (внешние объекты)  — 3 уровня

Контейнер определяется по имени родителя каталога формы: суффикс ``Form``
(``CatalogForm``, ``Form``, ``ReportForm``, ``CommonForm``, ``DocumentForm``
и т.д.) — без привязки к конкретной глубине.

OS-нейтральность:
- Пути строятся через :mod:`pathlib`.
- Текст читается/пишется как UTF-8 явно.
- Нет литеральных ``\\`` и абсолютных путей в коде.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Модель результата
# ---------------------------------------------------------------------------


@dataclass
class ElemFormEntry:
    """Одна форма с элементным представлением, найденная при discovery.

    Тип формы (ordinary/managed) не определяется этим dataclass;
    классификация — отдельный issue #56.
    """

    elem_json_path: Path
    """Путь к ``*.elem.json`` — основной артефакт формы.
    Относительный от корня распаковки."""

    aux_json_path: Optional[Path] = None
    """Путь к ``*.10.json`` (если найден), иначе ``None``. Относительный."""

    bsl_path: Optional[Path] = None
    """Путь к ``*.obj.10.bsl`` (если найден), иначе ``None``. Относительный."""

    extra_warnings: list[str] = field(default_factory=list)
    """Нефатальные предупреждения, собранные при обходе этой записи."""


# ---------------------------------------------------------------------------
# Обратная совместимость (deprecated alias)
# ---------------------------------------------------------------------------

#: Устаревший псевдоним; используйте :class:`ElemFormEntry`.
ManagedFormEntry = ElemFormEntry


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def discover_elem_forms(root: Path) -> list[ElemFormEntry]:
    """Обойти дерево ``root`` и вернуть список форм с ``*.elem.json``.

    Находит все формы, у которых есть элементное JSON-представление
    ``*.elem.json`` (обычные и управляемые). Тип формы не определяется —
    см. issue #56.

    Контейнер формы — родительский каталог с суффиксом
    ``Form`` (``CatalogForm``, ``Form``, ``ReportForm``, ``CommonForm`` и т.д.).

    Обход устойчив к глубине: поддерживает 3-уровневый layout ``CommonForm``
    и внешних объектов, а также стандартный 4-уровневый ``Catalog``/
    ``Document``/… layout.

    Все пути в возвращаемых :class:`ElemFormEntry` — **относительные**
    от ``root``.

    Parameters
    ----------
    root:
        Корень распаковки (например путь к распакованному ``.cf`` / ``.epf`` /
        ``.erf``). Должен быть существующим каталогом; если нет — возвращается
        пустой список.

    Returns
    -------
    list[ElemFormEntry]
        Отсортированный по ``elem_json_path`` список найденных форм.
        Пустой список, если формы не найдены.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    entries: list[ElemFormEntry] = []
    seen_form_dirs: set[Path] = set()

    for elem in sorted(root.rglob("*.elem.json")):
        form_dir = elem.parent
        if form_dir in seen_form_dirs:
            continue

        # Контейнер — родитель каталога формы; должен оканчиваться на 'Form'.
        # Пример: …/CatalogForm/ФормаЭлемента/ → container.name = 'CatalogForm'
        container = form_dir.parent
        if container == root:
            # *.elem.json прямо в корне — не форма
            continue
        if not container.name.endswith("Form"):
            continue

        seen_form_dirs.add(form_dir)
        entry = _scan_elem_form_dir(form_dir, root)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda e: e.elem_json_path)
    return entries


# ---------------------------------------------------------------------------
# Обратная совместимость (deprecated alias)
# ---------------------------------------------------------------------------

#: Устаревший псевдоним; используйте :func:`discover_elem_forms`.
discover_managed_forms = discover_elem_forms


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------


def _scan_elem_form_dir(
    form_dir: Path,
    root: Path,
) -> Optional[ElemFormEntry]:
    """Попытаться собрать ElemFormEntry из одного каталога формы.

    Возвращает ``None``, если в каталоге нет ``*.elem.json``.
    Сопутствующие артефакты ``*.10.json`` и ``*.obj.10.bsl`` — опциональны.
    """
    elem_files = sorted(form_dir.glob("*.elem.json"))
    if not elem_files:
        return None

    # Берём первый *.elem.json (по алфавиту — детерминизм).
    elem_path = elem_files[0]

    # Сопутствующие артефакты — best-effort.
    aux_json_files = sorted(form_dir.glob("*.10.json"))
    aux_json_path = aux_json_files[0] if aux_json_files else None

    bsl_files = sorted(form_dir.glob("*.obj.10.bsl"))
    bsl_path = bsl_files[0] if bsl_files else None

    warnings: list[str] = []
    if len(elem_files) > 1:
        extras = [f.name for f in elem_files[1:]]
        warnings.append(
            f"multiple *.elem.json in {form_dir.name}: {extras!r}; "
            f"using {elem_path.name!r}"
        )

    return ElemFormEntry(
        elem_json_path=elem_path.relative_to(root),
        aux_json_path=aux_json_path.relative_to(root) if aux_json_path is not None else None,
        bsl_path=bsl_path.relative_to(root) if bsl_path is not None else None,
        extra_warnings=warnings,
    )
