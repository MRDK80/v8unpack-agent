"""managed_forms — discovery форм по *.elem.json.

Реализует issue #55.

v8unpack 1.2.11 материализует каждую форму (обычную и управляемую) как
``*.elem.json`` в каталоге формы. Реальный носитель структуры —
``*.elem.json``; ``Form.xml`` в pipeline v8unpack-agent не используется.

Классификация ordinary/managed не входит в scope этого модуля — см. issue #56.

Layout-варианты каталогов форм (устойчиво к глубине):

- ``<root>/…/<ContainerForm>/<form_name>/*.elem.json``  (любая глубина)

Контейнер определяется по имени родителя каталога формы: суффикс ``Form``
(``CatalogForm``, ``Form``, ``ReportForm``, ``CommonForm``, ``DocumentForm``
и т.д.) — без привязки к конкретной глубине.

Сопутствующие артефакты зависят от параметра распаковщика ``--descent``:

- ``<stem>.<descent>.json``      — вспомогательный JSON;
- ``<stem>.obj.<descent>.bsl``   — BSL-модуль.

Значение ``<descent>`` может быть:

- литералом ``id`` (если ``--descent`` не указан);
- простым числом, например ``10``;
- составным значением до четырёх компонентов, например ``3.0.75.100``
  (не более трёх цифр в каждом компоненте).

Discovery не привязан к конкретному значению ``--descent``. Основной артефакт
``*.elem.json`` от descent не зависит и обязателен; всё остальное опционально.

OS-нейтральность:
- Пути строятся через :mod:`pathlib`.
- Текст читается/пишется как UTF-8 явно.
- Нет литеральных разделителей путей и абсолютных путей в коде.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Распознавание descent-суффикса
# ---------------------------------------------------------------------------

#: Литерал ``id`` ИЛИ до четырёх числовых компонентов по 1..3 цифры.
#: Примеры допустимых значений: ``id``, ``10``, ``3.0.75.100``.
_DESCENT_RE = re.compile(r"^(?:id|\d{1,3}(?:\.\d{1,3}){0,3})$")


def _extract_descent(stem_after_base: str) -> Optional[str]:
    """Вернуть descent-суффикс, если строка ему соответствует, иначе ``None``.

    ``stem_after_base`` — часть имени файла между базовым именем формы и
    расширением. Для ``CatalogForm.3.0.75.100.json`` при базе ``CatalogForm``
    это ``3.0.75.100``.
    """
    if _DESCENT_RE.match(stem_after_base):
        return stem_after_base
    return None


# ---------------------------------------------------------------------------
# Модель результата
# ---------------------------------------------------------------------------


@dataclass
class DescentArtifacts:
    """Набор сопутствующих артефактов для одного значения ``--descent``.

    JSON и BSL сгруппированы по одинаковому descent-суффиксу.
    Все пути относительны от корня распаковки.
    """

    descent: str
    aux_json_path: Optional[Path] = None
    bsl_path: Optional[Path] = None


@dataclass
class ElemFormEntry:
    """Одна форма с элементным представлением, найденная при discovery.

    Тип формы (ordinary/managed) не определяется — см. issue #56.
    """

    elem_json_path: Path
    """Путь к ``*.elem.json`` — основной артефакт формы. Относительный."""

    descent_artifacts: list[DescentArtifacts] = field(default_factory=list)
    """Наборы сопутствующих артефактов, сгруппированные по descent-суффиксу.
    Пустой список, если ни JSON, ни BSL не найдены. Отсортирован по ``descent``."""

    extra_warnings: list[str] = field(default_factory=list)
    """Нефатальные предупреждения (напр. несколько descent-наборов)."""

    # ---- deprecated-совместимость с прежним API (одиночные поля) ----

    @property
    def aux_json_path(self) -> Optional[Path]:
        """DEPRECATED: первый ``aux_json_path`` из :attr:`descent_artifacts`.

        Сохранено для обратной совместимости. Используйте
        :attr:`descent_artifacts`.
        """
        for da in self.descent_artifacts:
            if da.aux_json_path is not None:
                return da.aux_json_path
        return None

    @property
    def bsl_path(self) -> Optional[Path]:
        """DEPRECATED: первый ``bsl_path`` из :attr:`descent_artifacts`.

        Сохранено для обратной совместимости. Используйте
        :attr:`descent_artifacts`.
        """
        for da in self.descent_artifacts:
            if da.bsl_path is not None:
                return da.bsl_path
        return None


#: Устаревший псевдоним; используйте :class:`ElemFormEntry`.
ManagedFormEntry = ElemFormEntry


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def discover_elem_forms(root: Path) -> list[ElemFormEntry]:
    """Обойти дерево ``root`` и вернуть список форм с ``*.elem.json``.

    Находит все формы с элементным JSON-представлением ``*.elem.json``
    (обычные и управляемые). Тип формы не определяется — см. issue #56.

    Контейнер формы — родительский каталог с суффиксом ``Form``. Обход
    устойчив к глубине. Все пути в результате — относительные от ``root``.

    Returns
    -------
    list[ElemFormEntry]
        Отсортированный по ``elem_json_path`` список. Пустой, если форм нет
        либо ``root`` не существует.
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

        container = form_dir.parent
        if container == root:
            continue
        if not container.name.endswith("Form"):
            continue

        seen_form_dirs.add(form_dir)
        entry = _scan_elem_form_dir(form_dir, root)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda e: e.elem_json_path)
    return entries


#: Устаревший псевдоним; используйте :func:`discover_elem_forms`.
discover_managed_forms = discover_elem_forms


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------


def _descent_from_json(name: str) -> Optional[str]:
    """Вернуть descent из ``<stem>.<descent>.json`` или ``None``.

    Descent может быть составным (``3.0.75.100``), поэтому берётся
    максимально длинный суффикс перед ``.json``, проходящий валидацию.
    Проход слева направо: первый кандидат, прошедший ``_extract_descent``,
    и есть полный descent (базовое имя формы содержит буквы и валидацию
    не проходит).
    """
    core = name[: -len(".json")]  # напр. "CatalogForm.3.0.75.100"
    idx = core.find(".")
    while idx != -1:
        candidate = core[idx + 1:]
        if _extract_descent(candidate) is not None:
            return candidate
        idx = core.find(".", idx + 1)
    return None


def _descent_from_bsl(name: str) -> Optional[str]:
    """Вернуть descent из ``<stem>.obj.<descent>.bsl`` или ``None``."""
    core = name[: -len(".bsl")]  # напр. "CatalogForm.obj.3.0.75.100"
    marker = ".obj."
    idx = core.rfind(marker)
    if idx == -1:
        return None
    candidate = core[idx + len(marker):]
    return _extract_descent(candidate)


def _scan_elem_form_dir(
    form_dir: Path,
    root: Path,
) -> Optional[ElemFormEntry]:
    """Собрать :class:`ElemFormEntry` из одного каталога формы.

    Возвращает ``None``, если нет ``*.elem.json``. Сопутствующие
    ``<stem>.<descent>.json`` и ``<stem>.obj.<descent>.bsl`` — опциональны и
    группируются по descent-суффиксу без привязки к конкретному значению.
    """
    elem_files = sorted(form_dir.glob("*.elem.json"))
    if not elem_files:
        return None

    elem_path = elem_files[0]

    warnings: list[str] = []
    if len(elem_files) > 1:
        extras = [f.name for f in elem_files[1:]]
        warnings.append(
            f"multiple *.elem.json in {form_dir.name}: {extras!r}; "
            f"using {elem_path.name!r}"
        )

    # descent -> {"json": Path|None, "bsl": Path|None}
    by_descent: dict[str, dict[str, Optional[Path]]] = {}

    for path in sorted(form_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".elem.json"):
            continue

        if name.endswith(".bsl"):
            descent = _descent_from_bsl(name)
            if descent is None:
                continue
            by_descent.setdefault(descent, {"json": None, "bsl": None})
            if by_descent[descent]["bsl"] is None:
                by_descent[descent]["bsl"] = path
        elif name.endswith(".json"):
            descent = _descent_from_json(name)
            if descent is None:
                continue
            by_descent.setdefault(descent, {"json": None, "bsl": None})
            if by_descent[descent]["json"] is None:
                by_descent[descent]["json"] = path

    descent_artifacts: list[DescentArtifacts] = []
    for descent in sorted(by_descent):
        pair = by_descent[descent]
        aux = pair["json"]
        bsl = pair["bsl"]
        descent_artifacts.append(
            DescentArtifacts(
                descent=descent,
                aux_json_path=aux.relative_to(root) if aux is not None else None,
                bsl_path=bsl.relative_to(root) if bsl is not None else None,
            )
        )

    if len(descent_artifacts) > 1:
        found = [da.descent for da in descent_artifacts]
        warnings.append(
            f"multiple descent sets in {form_dir.name}: {found!r}; "
            f"all sets preserved"
        )

    return ElemFormEntry(
        elem_json_path=elem_path.relative_to(root),
        descent_artifacts=descent_artifacts,
        extra_warnings=warnings,
    )