"""managed_forms — discovery форм по *.elem.json.

Реализует issue #55.

v8unpack 1.2.11 материализует каждую форму (обычную и управляемую) как
``*.elem.json`` в каталоге формы. Реальный носитель структуры —
``*.elem.json``; ``Form.xml`` в pipeline v8unpack-agent не используется.

Классификация ordinary/managed закрыта как not_planned (issue #56).

РЕАЛЬНАЯ СХЕМА ИМЕНОВАНИЯ (подтверждена на 2212 формах, июль 2026).
В типовой распаковке (без ``--descent``) артефакты каталога формы таковы:

- ``<stem>.elem.json`` — структура формы (обязательный);
- ``<stem>.json``      — метаданные формы  → meta_json_path;
- ``<stem>.id.json``   — UUID формы (``{"uuid": ...}``) → id_json_path;
- ``<stem>.obj.bsl``   — BSL-модуль формы → bsl_path.

ВНИМАНИЕ (не «чинить» обратно под descent!): суффикс ``id`` в ``<stem>.id.json``
— это НЕ значение ``--descent``, а отдельный UUID-файл. Он всегда попадает в
``id_json_path`` и НИКОГДА не трактуется как descent-набор.

Модули НЕ форм игнорируются: ``.mgr.bsl`` (менеджер объекта),
``.seance.bsl`` / ``.con.bsl`` / ``.app.bsl`` / ``.802.bsl`` (модули
конфигурации). Модуль формы — строго ``<stem>.obj.bsl`` либо
``<stem>.obj.<descent>.bsl``.

DESCENT (опциональный слой для чужих распаковок с ``--descent``).
Если распаковка выполнена с ``--descent``, сопутствующие файлы получают суффикс:

- ``<stem>.<descent>.json`` — вспомогательный JSON;
- ``<stem>.obj.<descent>.bsl`` — BSL-модуль.

Значение ``<descent>``: простое число ``10`` или составное до четырёх
компонентов ``3.0.75.100`` (не более трёх цифр в компоненте). Литерал ``id``
descent-ом НЕ является (см. выше). Такие наборы складываются в
``descent_artifacts``; на типовой распаковке этот список пуст.

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

#: Только числовой descent: до четырёх компонентов по 1..3 цифры.
#: Примеры: ``10``, ``3.0.75.100``. Литерал ``id`` СЮДА НЕ ВХОДИТ — это
#: отдельный UUID-файл, а не descent (см. docstring модуля).
_DESCENT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){0,3}$")

#: Средние сегменты BSL, обозначающие модуль формы.
_FORM_BSL_MARKER = ".obj."


def _extract_descent(candidate: str) -> Optional[str]:
    """Вернуть числовой descent-суффикс либо ``None``.

    Литерал ``id`` намеренно НЕ распознаётся как descent.
    """
    if _DESCENT_RE.match(candidate):
        return candidate
    return None


# ---------------------------------------------------------------------------
# Модель результата
# ---------------------------------------------------------------------------


@dataclass
class DescentArtifacts:
    """Набор сопутствующих артефактов для одного числового ``--descent``.

    Заполняется только для распаковок с ``--descent``. На типовой распаковке
    (без параметра) список :attr:`ElemFormEntry.descent_artifacts` пуст, а
    файлы попадают в явные поля ``meta_json_path`` / ``id_json_path`` /
    ``bsl_path``. Все пути относительны от корня распаковки.
    """

    descent: str
    aux_json_path: Optional[Path] = None
    bsl_path: Optional[Path] = None


@dataclass
class ElemFormEntry:
    """Одна форма с элементным представлением, найденная при discovery.

    Тип формы (ordinary/managed) не определяется — классифика��ия закрыта
    как not_planned (issue #56).
    """

    elem_json_path: Path
    """Путь к ``*.elem.json`` — основной артефакт формы. Относительный."""

    meta_json_path: Optional[Path] = None
    """``<stem>.json`` — метаданные формы (или ``None``). Относительный."""

    id_json_path: Optional[Path] = None
    """``<stem>.id.json`` — UUID формы (или ``None``). Относительный."""

    bsl_path: Optional[Path] = None
    """``<stem>.obj.bsl`` — BSL-модуль формы (или ``None``). Относительный.

    На распаковках с ``--descent`` тут первый непустой BSL из
    :attr:`descent_artifacts` (см. логику сборки)."""

    descent_artifacts: list[DescentArtifacts] = field(default_factory=list)
    """Наборы сопутствующих артефактов для числовых значений ``--descent``.
    Пустой на типовой распаковке. Отсортирован по ``descent``."""

    extra_warnings: list[str] = field(default_factory=list)
    """Нефатальные предупреждения (несколько descent-наборов, несколько
    ``*.elem.json`` в каталоге и т.п.)."""

    # ---- deprecated-совместимость с прежним API ----

    @property
    def aux_json_path(self) -> Optional[Path]:
        """DEPRECATED: используйте :attr:`meta_json_path`.

        Возвращает метаданные формы (``<stem>.json``), а при их отсутствии —
        первый вспомогательный JSON из :attr:`descent_artifacts`.
        """
        if self.meta_json_path is not None:
            return self.meta_json_path
        for da in self.descent_artifacts:
            if da.aux_json_path is not None:
                return da.aux_json_path
        return None


#: Устаревший псевдоним; используйте :class:`ElemFormEntry`.
ManagedFormEntry = ElemFormEntry


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def discover_elem_forms(root: Path) -> list[ElemFormEntry]:
    """Обойти дерево ``root`` и вернуть список форм с ``*.elem.json``.

    Находит все формы с элементным JSON-представлением ``*.elem.json``
    (обычные и управляемые). Тип формы не определяется — классификация
    закрыта как not_planned (issue #56).

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


def _bsl_descent(name: str) -> Optional[str]:
    """descent из ``<stem>.obj.<descent>.bsl`` (числовой) либо ``None``."""
    core = name[: -len(".bsl")]
    idx = core.rfind(_FORM_BSL_MARKER)
    if idx == -1:
        return None
    candidate = core[idx + len(_FORM_BSL_MARKER):]
    return _extract_descent(candidate)


def _json_descent(name: str) -> Optional[str]:
    """descent из ``<stem>.<descent>.json`` (числовой) либо ``None``.

    Составной descent (``3.0.75.100``) содержит точки, поэтому берётся
    максимально длинный суффикс слева, проходящий числовую валидацию.
    Литерал ``id`` числовую проверку не проходит и сюда не попадёт.
    """
    core = name[: -len(".json")]
    idx = core.find(".")
    while idx != -1:
        candidate = core[idx + 1:]
        if _extract_descent(candidate) is not None:
            return candidate
        idx = core.find(".", idx + 1)
    return None


def _scan_elem_form_dir(
    form_dir: Path,
    root: Path,
) -> Optional[ElemFormEntry]:
    """Собрать :class:`ElemFormEntry` из одного каталога формы.

    Возвращает ``None``, если нет ``*.elem.json``. Классификация файлов
    по приоритету реальной схемы v8unpack 1.2.11 (см. docstring модуля):

    - ``<stem>.obj.bsl``   → bsl_path (бессуффиксный BSL формы);
    - ``<stem>.obj.<num>.bsl`` → descent_artifacts;
    - ``.mgr/.seance/.con/.app/.802 .bsl`` → игнор (не модуль формы);
    - ``<stem>.id.json``   → id_json_path (UUID, приоритет над descent);
    - ``<stem>.<num>.json`` → descent_artifacts;
    - ``<stem>.json``      → meta_json_path.
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

    meta_json: Optional[Path] = None
    id_json: Optional[Path] = None
    plain_bsl: Optional[Path] = None
    # descent -> {"json": Path|None, "bsl": Path|None}
    by_descent: dict[str, dict[str, Optional[Path]]] = {}

    for path in sorted(form_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".elem.json"):
            continue

        if name.endswith(".bsl"):
            # Модуль формы — строго <stem>.obj[.<descent>].bsl.
            core = name[: -len(".bsl")]
            if _FORM_BSL_MARKER not in core and not core.endswith(".obj"):
                continue  # .mgr / .seance / .con / .app / .802 — не форма
            descent = _bsl_descent(name)
            if descent is None:
                # <stem>.obj.bsl — бессуффиксный BSL формы
                if plain_bsl is None:
                    plain_bsl = path
            else:
                slot = by_descent.setdefault(descent, {"json": None, "bsl": None})
                if slot["bsl"] is None:
                    slot["bsl"] = path

        elif name.endswith(".id.json"):
            # UUID формы — приоритет над descent-трактовкой.
            if id_json is None:
                id_json = path

        elif name.endswith(".json"):
            descent = _json_descent(name)
            if descent is None:
                # <stem>.json — метаданные формы
                if meta_json is None:
                    meta_json = path
            else:
                slot = by_descent.setdefault(descent, {"json": None, "bsl": None})
                if slot["json"] is None:
                    slot["json"] = path

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

    # bsl_path: бессуффиксный приоритетнее; иначе первый descent-BSL.
    bsl_path = plain_bsl
    if bsl_path is None:
        for da in descent_artifacts:
            if da.bsl_path is not None:
                bsl_path = root / da.bsl_path
                break

    return ElemFormEntry(
        elem_json_path=elem_path.relative_to(root),
        meta_json_path=meta_json.relative_to(root) if meta_json is not None else None,
        id_json_path=id_json.relative_to(root) if id_json is not None else None,
        bsl_path=bsl_path.relative_to(root) if bsl_path is not None else None,
        descent_artifacts=descent_artifacts,
        extra_warnings=warnings,
    )
