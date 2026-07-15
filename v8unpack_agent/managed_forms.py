"""managed_forms — discovery управляемых форм по *.elem.json.

Реализует issue #55.

Управляемая форма v8unpack 1.2.11 не имеет ``Form.xml``. Реальный носитель
структуры — ``*.elem.json``, расположенный в каталоге формы. Рядом могут
находиться сопутствующие артефакты ``*.10.json`` и ``*.obj.10.bsl``.

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
class ManagedFormEntry:
    """Одна управляемая форма, найденная при discovery."""

    elem_json_path: Path
    """Путь к ``*.elem.json`` — основной артефакт управляемой формы.
    Относительный от корня распаковки."""

    aux_json_path: Optional[Path] = None
    """Путь к ``*.10.json`` (если найден), иначе ``None``. Относительный."""

    bsl_path: Optional[Path] = None
    """Путь к ``*.obj.10.bsl`` (если найден), иначе ``None``. Относительный."""

    extra_warnings: list[str] = field(default_factory=list)
    """Нефатальные предупреждения, собранные при обходе этой записи."""


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def discover_managed_forms(root: Path) -> list[ManagedFormEntry]:
    """Обойти дерево ``root`` и вернуть список управляемых форм.

    Управляемая форма определяется по наличию хотя бы одного ``*.elem.json``
    в каталоге формы. Контейнер формы — родительский каталог с суффиксом
    ``Form`` (``CatalogForm``, ``Form``, ``ReportForm``, ``CommonForm`` и т.д.).

    Обход устойчив к глубине: поддерживает 3-уровневый layout ``CommonForm``
    и внешних объектов, а также стандартный 4-уровневый ``Catalog``/
    ``Document``/… layout.

    Все пути в возвращаемых :class:`ManagedFormEntry` — **относительные**
    от ``root``.

    Parameters
    ----------
    root:
        Корень распаковки (например путь к распакованному ``.cf`` / ``.epf`` /
        ``.erf``). Должен быть существующим каталогом; если нет — возвращается
        пустой список.

    Returns
    -------
    list[ManagedFormEntry]
        Отсортированный по ``elem_json_path`` список найденных форм.
        Пустой список, если управляемые формы не найдены.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    entries: list[ManagedFormEntry] = []
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
        entry = _scan_managed_form_dir(form_dir, root)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda e: e.elem_json_path)
    return entries


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------


def _scan_managed_form_dir(
    form_dir: Path,
    root: Path,
) -> Optional[ManagedFormEntry]:
    """Попытаться собрать ManagedFormEntry из одного каталога формы.

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

    return ManagedFormEntry(
        elem_json_path=elem_path.relative_to(root),
        aux_json_path=aux_json_path.relative_to(root) if aux_json_path is not None else None,
        bsl_path=bsl_path.relative_to(root) if bsl_path is not None else None,
        extra_warnings=warnings,
    )
