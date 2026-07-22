"""scan_forms — обобщённый обход *Form-контейнеров и сборка FormScanIndex.

Реализует issues #9, #13, #25, #32, #38, #40, #57.

v8unpack формирует несколько layout-ов.

**4-уровневый** (большинство объектов конфигурации)::

    cf_export/<Тип>/<Объект>/Form/<ИмяФормы>/

**3-уровневый** (общие формы — нет объекта-владельца)::

    cf_export/CommonForm/<ИмяФормы>/

**External** (распакованные внешние обработки/отчёты, mode="external", issues #25/#32)::

    External/<имя обработки>/Form/<ИмяФормы>/Form.obj.bsl
    External/<имя отчёта>/ReportForm/<ИмяФормы>/ReportForm.obj.bsl

Отличия External от конфигурации:
- контейнер формы — ``Form`` (обработка) либо ``ReportForm`` (отчёт);
- bsl-файл формы называется ``.obj.bsl`` (v8unpack 1.2.11) либо
  ``.obj`` (старый вариант без суффикса, issue #32) — берётся первый
  существующий;
- верхний уровень — имя конкретной обработки/отчёта, а не ``object_type``;
- тип объекта определяется так: контейнер ``ReportForm`` ⇒ ``ExternalReport``;
  контейнер ``Form`` ⇒ по имени модуля объекта обработки
  (``<Тип>.obj.bsl`` / ``<Тип>.obj``), fallback ``ExternalDataProcessor``.

Артефакты формы конфигурации::

    .obj.bsl
    .json

Артефакты формы внешнего объекта::

    .obj.bsl   # bsl формы; либо .obj (legacy)
    .json      # метаданные формы; Form.json остаётся fallback для совместимости
    .elem      # структура формы; Form.elem остаётся fallback для совместимости
    .id

**Elem-формы** (issue #57):

Управляемые формы не имеют ``.obj.bsl`` в ряде конфигураций, но
всегда дают ``*.elem.json``. ``scan_forms`` использует
:func:`~v8unpack_agent.managed_forms.discover_elem_forms` для обнаружения
этих форм и добавляет их в ``FormScanIndex`` с заполненным ``elem_json_path``.
Для ordinary/external форм ``elem_json_path`` берётся из той же discovery,
если ``*.elem.json`` присутствует в каталоге формы.

``FormEntry.elem_json_path`` — всегда relative-to-root (согласованно с
``ElemFormEntry.elem_json_path`` из discovery #55). Реестр хранит только
путь; структуру по требованию даёт ``parse_elem_json`` (второй парсер НЕ
вводится).

OS-нейтральность:
- Пути строятся через :mod:`pathlib` / :func:`os.path.join`.
- Текст читается/пишется как UTF-8 явно.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# --- константы структуры External (issues #25, #32) ------------------------
EXTERNAL_ROOT = "External"

# Контейнеры форм внешних объектов. Порядок важен только для детерминизма обхода.
EXTERNAL_FORM_CONTAINERS = ("Form", "ReportForm")

EXTERNAL_JSON_NAME = "Form.json"  # legacy fallback for external Form metadata
EXTERNAL_ELEM_NAME = "Form.elem"

# Тип объекта по модулю объекта обработки (для контейнера Form).
# Кортеж кандидатов на каждый тип: сначала .bsl (v8unpack 1.2.11), затем legacy.
EXTERNAL_OBJECT_MODULE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "ExternalDataProcessor": (
        "ExternalDataProcessor.obj.bsl",
        "ExternalDataProcessor.obj",
    ),
    "ExternalReport": (
        "ExternalReport.obj.bsl",
        "ExternalReport.obj",
    ),
}

# Контейнер, который однозначно определяет тип «отчёт» (подтверждено на живых данных).
EXTERNAL_REPORT_CONTAINER = "ReportForm"
EXTERNAL_REPORT_OBJECT_TYPE = "ExternalReport"

# Fallback-тип, если тип не удалось определить (обратная совместимость).
EXTERNAL_DEFAULT_OBJECT_TYPE = "ExternalDataProcessor"

# Структурно значимые поля нормализованного элемента (issue #40).
# Косметические поля (left, top, width, height, color, font, guid, …)
# намеренно исключены, чтобы правка разметки без смысловых изменений
# не порождала ложный structure drift.
_ELEM_STRUCTURAL_KEYS = frozenset({
    "name", "type", "path", "parent", "parent_path",
    "page", "source", "data_path", "handler",
})


def _compute_sha256(path: Path) -> Optional[str]:
    """Вернуть hex-дайджест SHA-256 содержимого файла или None при ошибке."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _compute_elem_sha256(form_dir: Path) -> Optional[str]:
    """Вычислить SHA-256 нормализованного дерева элементов формы (issue #40).

    Алгоритм:
    1. Найти ``*.elem.json`` в ``form_dir`` (через elem_parser).
    2. Распарсить нормализованное дерево (``ElemIndexResult.elements``).
       Если ``elem_index_ok=False`` (файл не найден или не разобран) — вернуть ``None``.
    3. Из каждого элемента оставить только структурно значимые поля
       (``_ELEM_STRUCTURAL_KEYS``): name, type, path, parent, parent_path,
       page, source, data_path, handler. Косметические поля (координаты,
       цвета, шрифты, GUID) не хэшируются.
    4. Сериализовать список отфильтрованных элементов в UTF-8 JSON
       с ``sort_keys=True, ensure_ascii=False`` для детерминизма.
    5. Вернуть SHA-256 hex-дайджест байтов этой строки.

    Граница достоверности: вложенность групп не реконструируется
    (``elem_parser`` помечает это предупреждением); хэш строится
    по достоверной части дерева.
    """
    try:
        from v8unpack_agent.elem_parser import parse_elem_json  # local import — избегаем цикл
        result = parse_elem_json(form_dir)
        if not result.elem_index_ok or not result.elements:
            return None
        structural = [
            {k: v for k, v in elem.items() if k in _ELEM_STRUCTURAL_KEYS}
            for elem in result.elements
        ]
        payload = json.dumps(structural, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class FormEntry:
    """Одна форма, найденная при сканировании cf_export."""

    object_type: str
    """Тип метаобъекта: ``Catalog``, ``Document``, ``CommonForm`` и т.д.
    Для внешних объектов — ``ExternalDataProcessor`` либо ``ExternalReport``
    (не пересекается с типами конфигурации).
    Для форм, обнаруженных только через discovery (elem-only) — тип берётся
    из пути файловой системы (container-name) при наличии, иначе пустая строка."""

    object_name: str
    """Имя метаобъекта. Пустая строка для общих форм (CommonForm-layout).
    Для внешних объектов — имя конкретной обработки/отчёта."""

    container_name: str
    """Имя контейнера: ``CatalogForm``, ``Form``, ``ReportForm``, ``CommonForm``."""

    form_name: str
    """Имя директории формы."""

    form_path: Path
    """Абсолютный путь к директории формы."""

    bsl_path: Path
    """Путь к bsl-файлу формы: ``.obj.bsl`` (config) либо
    ``.obj.bsl`` / ``.obj`` (external).
    Для elem-only форм путь может указывать на несуществующий файл
    (заполняется заглушкой из form_path для сохранения схемы)."""

    json_path: Path
    """Путь к json-файлу формы.
    Для elem-only форм может указывать на несуществующий файл."""

    warnings: list[str] = field(default_factory=list)

    bsl_mtime: float = 0.0
    """mtime bsl-файла на момент сканирования. Legacy-поле; используется как
    fallback в ``drift_checker.check_drift()`` для старых индексов без hash.
    0.0 означает «не известно»."""

    form_elem_path: Optional[Path] = None
    """Путь к ``Form.elem`` (структура формы внешнего объекта). ``None`` —
    файла нет либо это форма конфигурации (issue #25). Additive-поле:
    дефолт сохраняет обратную совместимость индекса."""

    bsl_sha256: Optional[str] = None
    """SHA-256 hex-дайджест содержимого bsl-файла на момент сканирования.
    Основной критерий изменения в ``drift_checker.check_drift()`` (issue #38).
    ``None`` означает «не вычислен» — используется legacy-fallback через
    ``bsl_mtime``."""

    elem_sha256: Optional[str] = None
    """SHA-256 hex-дайджест нормализованного дерева элементов формы (issue #40).
    Хэшируется только структурно значимая часть ``form_elements_index``
    (name, type, path, parent, parent_path, page, source, data_path, handler).
    Косметические поля (координаты, цвета, шрифты, GUID) исключены.
    ``None`` — ``*.elem.json`` не найден, не разобран или элементов нет.
    Используется ``drift_checker.check_drift()`` как независимый сигнал
    ``structure_modified`` (отдельно от ``modified``)."""

    elem_json_path: Optional[Path] = None
    """Путь к ``*.elem.json`` — источник структуры elem-формы (issue #57).
    Relative-to-root, согласованно с ``ElemFormEntry.elem_json_path`` из
    discovery (#55). Для ordinary/external форм заполнен, если ``*.elem.json``
    присутствует в каталоге формы; иначе ``None``. Для elem-only форм всегда
    заполнен. Реестр хранит только путь; структуру по требованию даёт
    ``parse_elem_json`` — второй парсер НЕ вводится."""


@dataclass
class FormScanIndex:
    """Результат сканирования cf_export."""

    forms: list[FormEntry] = field(default_factory=list)
    total: int = 0
    scanned_at: str = ""
    scan_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "scanned_at": self.scanned_at,
            "scan_warnings": self.scan_warnings,
            "forms": [
                {
                    "object_type": e.object_type,
                    "object_name": e.object_name,
                    "container_name": e.container_name,
                    "form_name": e.form_name,
                    "form_path": e.form_path.as_posix(),
                    "bsl_path": e.bsl_path.as_posix(),
                    "json_path": e.json_path.as_posix(),
                    "warnings": e.warnings,
                    "bsl_mtime": e.bsl_mtime,
                    "form_elem_path": (
                        e.form_elem_path.as_posix()
                        if e.form_elem_path is not None else None
                    ),
                    "bsl_sha256": e.bsl_sha256,
                    "elem_sha256": e.elem_sha256,
                    "elem_json_path": (
                        e.elem_json_path.as_posix()
                        if e.elem_json_path is not None else None
                    ),
                }
                for e in self.forms
            ],
        }

    def save(self, out_path: Path) -> Path:
        """Сохранить индекс как UTF-8 JSON."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path

    @classmethod
    def load(cls, index_path: Path) -> "FormScanIndex":
        """Загрузить :class:`FormScanIndex` из JSON-файла, сохранённого :meth:`save`.

        Если файл отсутствует — возвращает пустой индекс (аналогично
        :meth:`FormsIndex.load`).
        Пути (``form_path``, ``bsl_path``, ``json_path``, ``form_elem_path``,
        ``elem_json_path``) восстанавливаются через :class:`pathlib.Path` —
        OS-нейтрально.

        Обратная совместимость:
        - поле ``bsl_sha256`` отсутствующее в старом индексе → ``None``;
        - поле ``elem_sha256`` отсутствующее в старом индексе → ``None``;
        - поле ``elem_json_path`` отсутствующее в старом индексе → ``None``;
        - поле ``form_xml_path`` в старом индексе — игнорируется (не обязательно).

        Parameters
        ----------
        index_path:
            Путь к JSON-файлу (например ``forms_scan_index.json``).
        """
        if not Path(index_path).exists():
            return cls()
        raw = json.loads(Path(index_path).read_text(encoding="utf-8"))
        forms: list[FormEntry] = [
            FormEntry(
                object_type=row["object_type"],
                object_name=row["object_name"],
                container_name=row["container_name"],
                form_name=row["form_name"],
                form_path=Path(row["form_path"]),
                bsl_path=Path(row["bsl_path"]),
                json_path=Path(row["json_path"]),
                warnings=list(row.get("warnings", [])),
                bsl_mtime=float(row.get("bsl_mtime", 0.0)),
                form_elem_path=(
                    Path(row["form_elem_path"])
                    if row.get("form_elem_path") is not None
                    else None
                ),
                bsl_sha256=row.get("bsl_sha256"),   # None for old indexes
                elem_sha256=row.get("elem_sha256"), # None for old indexes
                elem_json_path=(
                    Path(row["elem_json_path"])
                    if row.get("elem_json_path") is not None
                    else None
                ),  # None for old indexes; form_xml_path silently ignored
            )
            for row in raw.get("forms", [])
        ]
        return cls(
            forms=forms,
            total=int(raw.get("total", len(forms))),
            scanned_at=str(raw.get("scanned_at", "")),
            scan_warnings=list(raw.get("scan_warnings", [])),
        )


def _first_existing(directory: Path, candidates: tuple[str, ...]) -> Optional[Path]:
    """Вернуть первый существующий файл из candidates (по приоритету) или None."""
    for name in candidates:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _external_bsl_candidates(container_name: str) -> tuple[str, ...]:
    """Кандидаты bsl-файла формы для контейнера: .bsl (v8unpack 1.2.11), затем legacy."""
    return (f"{container_name}.obj.bsl", f"{container_name}.obj")


def _resolve_external_object_type(proc_dir: Path, container_name: str) -> str:
    """Определить тип внешнего объекта.

    Правило (подтверждено на живых данных, issue #32):
    - контейнер ``ReportForm`` ⇒ ``ExternalReport``;
    - контейнер ``Form`` ⇒ по имени модуля объекта (``<Тип>.obj.bsl`` / ``<Тип>.obj``),
      fallback ``ExternalDataProcessor``.
    """
    if container_name == EXTERNAL_REPORT_CONTAINER:
        return EXTERNAL_REPORT_OBJECT_TYPE
    for object_type, candidates in EXTERNAL_OBJECT_MODULE_CANDIDATES.items():
        if _first_existing(proc_dir, candidates) is not None:
            return object_type
    return EXTERNAL_DEFAULT_OBJECT_TYPE


def _is_form_container(directory: Path) -> bool:
    """True, если каталог является контейнером форм (*Form)."""
    return directory.is_dir() and directory.name.endswith("Form")


def _find_elem_json_path(form_dir: Path, root: Path) -> Optional[Path]:
    """Найти ``*.elem.json`` в каталоге формы и вернуть relative-to-root путь.

    Возвращает ``None``, если ``*.elem.json`` не найден.
    Не импортирует парсер — только определяет путь (issue #57).
    """
    elem_files = sorted(form_dir.glob("*.elem.json"))
    if not elem_files:
        return None
    return elem_files[0].relative_to(root)


def _scan_form_dir(
    form_dir: Path,
    object_type: str,
    object_name: str,
    container_name: str,
    root: Path,
) -> Optional["FormEntry"]:
    """Собрать FormEntry из директории формы конфигурации.

    Возвращает ``None``, если обязательный артефакт ``.obj.bsl`` отсутствует.
    Заполняет ``elem_json_path`` (relative-to-root) если ``*.elem.json`` есть
    в каталоге формы (issue #57).
    """
    bsl_path = form_dir / (container_name + ".obj.bsl")
    json_path = form_dir / (container_name + ".json")

    if not bsl_path.exists():
        return None

    warnings: list[str] = []
    if not json_path.exists():
        warnings.append(f"missing {json_path.name}")

    try:
        bsl_mtime = bsl_path.stat().st_mtime
    except OSError:
        bsl_mtime = 0.0

    bsl_sha256 = _compute_sha256(bsl_path)
    elem_sha256 = _compute_elem_sha256(form_dir)
    elem_json_path = _find_elem_json_path(form_dir, root)

    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container_name,
        form_name=form_dir.name,
        form_path=form_dir.resolve(),
        bsl_path=bsl_path.resolve(),
        json_path=json_path.resolve(),
        warnings=warnings,
        bsl_mtime=bsl_mtime,
        bsl_sha256=bsl_sha256,
        elem_sha256=elem_sha256,
        elem_json_path=elem_json_path,
    )


def _collect_forms_from_container(
    container_dir: Path,
    object_type: str,
    object_name: str,
    container_name: str,
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Обход всех форм внутри контейнера конфигурации, best-effort."""
    for form_dir in sorted(container_dir.iterdir()):
        if not form_dir.is_dir():
            continue
        try:
            entry = _scan_form_dir(form_dir, object_type, object_name, container_name, root)
            if entry is not None:
                forms.append(entry)
            else:
                msg = f"skipped (no .obj.bsl): {form_dir.relative_to(root).as_posix()}"
                scan_warnings.append(msg)
                logger.debug(msg)
        except Exception as exc:  # noqa: BLE001
            msg = f"error scanning {form_dir}: {exc}"
            scan_warnings.append(msg)
            logger.warning(msg)


def _scan_external_form_dir(
    form_dir: Path,
    object_type: str,
    object_name: str,
    container_name: str,
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Собрать FormEntry из директории формы внешнего объекта (issues #25, #32).

    Обязательный артефакт — bsl формы: ``.obj.bsl`` (v8unpack 1.2.11)
    либо ``.obj`` (legacy) — берётся первый существующий. Отсутствие
    обоих → best-effort skip с предупреждением. ``Form.elem`` опционален.
    Заполняет ``elem_json_path`` если ``*.elem.json`` присутствует (issue #57).
    """
    candidates = _external_bsl_candidates(container_name)
    bsl_path = _first_existing(form_dir, candidates)
    if bsl_path is None:
        names = " / ".join(candidates)
        msg = f"skipped (no {names}): {form_dir.relative_to(root).as_posix()}"
        scan_warnings.append(msg)
        logger.debug(msg)
        return

    json_candidates = [form_dir / f"{container_name}.json"]
    if container_name != "Form":
        json_candidates.append(form_dir / EXTERNAL_JSON_NAME)
    json_path = next(
        (candidate for candidate in json_candidates if candidate.exists()),
        json_candidates[0],
    )

    elem_candidates = [form_dir / f"{container_name}.elem"]
    if container_name != "Form":
        elem_candidates.append(form_dir / EXTERNAL_ELEM_NAME)
    elem_path = next(
        (candidate for candidate in elem_candidates if candidate.exists()),
        elem_candidates[0],
    )

    warnings: list[str] = []
    if not json_path.exists():
        warnings.append(f"missing {json_path.name}")

    try:
        bsl_mtime = bsl_path.stat().st_mtime
    except OSError:
        bsl_mtime = 0.0

    bsl_sha256 = _compute_sha256(bsl_path)
    elem_sha256 = _compute_elem_sha256(form_dir)
    elem_json_path = _find_elem_json_path(form_dir, root)

    forms.append(FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container_name,
        form_name=form_dir.name,
        form_path=form_dir.resolve(),
        bsl_path=bsl_path.resolve(),
        json_path=json_path.resolve(),
        warnings=warnings,
        bsl_mtime=bsl_mtime,
        form_elem_path=elem_path.resolve() if elem_path.exists() else None,
        bsl_sha256=bsl_sha256,
        elem_sha256=elem_sha256,
        elem_json_path=elem_json_path,
    ))


def _scan_external(
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Обход структуры External/<объект>/<контейнер>/<форма>/ (issues #25, #32).

    Устойчив к обоим вариантам корня: если внутри ``root`` есть каталог
    ``External/`` — идём от него; иначе ``root`` уже указывает на уровень
    объектов. Тихого fallback между режимами нет — режим задаётся явно.

    Для каждого объекта перебираются известные контейнеры форм
    (``Form``, ``ReportForm``). Тип объекта определяется по контейнеру и
    (для ``Form``) по модулю объекта — см. :func:`_resolve_external_object_type`.
    """
    external_root = root / EXTERNAL_ROOT
    if not external_root.is_dir():
        external_root = root

    for proc_dir in sorted(external_root.iterdir()):
        if not proc_dir.is_dir():
            continue
        object_name = proc_dir.name

        for container_name in EXTERNAL_FORM_CONTAINERS:
            container = proc_dir / container_name
            if not container.is_dir():
                continue

            object_type = _resolve_external_object_type(proc_dir, container_name)

            for form_dir in sorted(container.iterdir()):
                if not form_dir.is_dir():
                    continue
                try:
                    _scan_external_form_dir(
                        form_dir,
                        object_type,
                        object_name,
                        container_name,
                        root,
                        forms,
                        scan_warnings,
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"error scanning {form_dir}: {exc}"
                    scan_warnings.append(msg)
                    logger.warning(msg)


def _scan_config(
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Обход структуры конфигурации (4- и 3-уровневый layout). Логика #9/#13."""
    for type_dir in sorted(root.iterdir()):
        if not type_dir.is_dir():
            continue
        object_type = type_dir.name

        # --- 3-уровневый layout: CommonForm и аналоги ---
        if _is_form_container(type_dir):
            _collect_forms_from_container(
                container_dir=type_dir,
                object_type=object_type,
                object_name="",
                container_name=object_type,
                root=root,
                forms=forms,
                scan_warnings=scan_warnings,
            )
            continue

        # --- 4-уровневый layout: Catalog, Document, DataProcessor и др. ---
        for obj_dir in sorted(type_dir.iterdir()):
            if not obj_dir.is_dir():
                continue
            object_name = obj_dir.name

            for container_dir in sorted(obj_dir.iterdir()):
                if not _is_form_container(container_dir):
                    continue
                _collect_forms_from_container(
                    container_dir=container_dir,
                    object_type=object_type,
                    object_name=object_name,
                    container_name=container_dir.name,
                    root=root,
                    forms=forms,
                    scan_warnings=scan_warnings,
                )


def _infer_elem_only_metadata(
    rel_form_path: Path,
    mode: str,
) -> tuple[str, str, str, str]:
    """Infer FormEntry metadata from elem-only form path.

    For config layout::

        <object_type>/<object_name>/<container_name>/<form_name>

    For external layout::

        <object_name>.epf/Form/<form_name>
        <object_name>.erf/ReportForm/<form_name>

    Returns
    -------
    tuple(object_type, object_name, container_name, form_name)
    """
    parts = rel_form_path.parts

    # External layout: <object>.(epf|erf)/(Form|ReportForm)/<form_name>
    if mode == "external" and len(parts) >= 3 and parts[-2] in {"Form", "ReportForm"}:
        object_name = parts[-3]
        container_name = parts[-2]
        form_name = parts[-1]
        object_type = (
            EXTERNAL_REPORT_OBJECT_TYPE
            if container_name == "ReportForm" or object_name.lower().endswith(".erf")
            else EXTERNAL_DEFAULT_OBJECT_TYPE
        )
        return object_type, object_name, container_name, form_name

    # Config 4-level layout: <type>/<object>/<container>/<form>
    if len(parts) >= 4:
        return parts[-4], parts[-3], parts[-2], parts[-1]

    # Config 3-level layout (CommonForm): <container>/<form>
    if len(parts) >= 2:
        return "", "", parts[-2], parts[-1]

    if len(parts) == 1:
        return "", "", "", parts[0]

    return "", "", "", ""


def _collect_elem_only_forms(
    root: Path,
    existing_form_paths: set[Path],
    forms: list[FormEntry],
    scan_warnings: list[str],
    mode: str = "config",
) -> None:
    """Добавить elem-формы, не попавшие в обычный/external scan (issue #57).

    Использует :func:`~v8unpack_agent.managed_forms.discover_elem_forms`
    для обнаружения всех ``*.elem.json``. Пропускает формы, уже
    добавленные через ``_scan_form_dir`` / ``_scan_external_form_dir``
    (по абсолютному пути директории формы). Добавляет оставшиеся
    как ``FormEntry`` с заполненным ``elem_json_path`` и ``None``
    в полях ``bsl_sha256`` / ``bsl_mtime``.

    Метаданные (object_type / object_name / container_name / form_name)
    восстанавливаются из relative-пути формы через
    :func:`_infer_elem_only_metadata` — с учётом ``mode``: для external
    layout корректно разбирается ``<object>.(epf|erf)/(Form|ReportForm)/<form>``
    (issue #57, фикс метаданных внешних elem-only форм без кода).
    """
    try:
        from v8unpack_agent.managed_forms import discover_elem_forms  # local import
    except ImportError as exc:
        scan_warnings.append(f"cannot import discover_elem_forms: {exc}")
        return

    for elem_entry in discover_elem_forms(root):
        form_dir_rel = elem_entry.elem_json_path.parent
        form_dir_abs = (root / form_dir_rel).resolve()

        if form_dir_abs in existing_form_paths:
            continue

        object_type, object_name, container_name, form_name = _infer_elem_only_metadata(
            form_dir_rel,
            mode,
        )

        elem_sha256 = _compute_elem_sha256(form_dir_abs)

        # bsl_path и json_path — заглушки (форма без bsl)
        bsl_stub = (
            form_dir_abs / (container_name + ".obj.bsl")
            if container_name else form_dir_abs / "Form.obj.bsl"
        )
        json_stub = (
            form_dir_abs / (container_name + ".json")
            if container_name else form_dir_abs / "Form.json"
        )

        forms.append(FormEntry(
            object_type=object_type,
            object_name=object_name,
            container_name=container_name,
            form_name=form_name,
            form_path=form_dir_abs,
            bsl_path=bsl_stub,
            json_path=json_stub,
            warnings=["elem-only: no .obj.bsl found"],
            bsl_mtime=0.0,
            bsl_sha256=None,
            elem_sha256=elem_sha256,
            elem_json_path=elem_entry.elem_json_path,
        ))

        existing_form_paths.add(form_dir_abs)


def scan_forms(
    cf_export_root: Path,
    save_to: Optional[Path] = None,
    mode: Literal["config", "external"] = "config",
    include_elem_only: bool = True,
) -> FormScanIndex:
    """Обойти ``cf_export_root`` и собрать FormScanIndex.

    Параметры
    ---------
    cf_export_root:
        Корень выгрузки. Для ``mode="config"`` — ``Catalog/``, ``Document/``,
        ``CommonForm/`` и др. Для ``mode="external"`` — каталог с ``External/``
        либо непосредственно уровень объектов.
    save_to:
        Если задан, сохранить JSON-индекс в этот файл.
    mode:
        ``"config"`` (по умолчанию) — структура конфигурации; ``"external"`` —
        структура распакованных внешних обработок/отчётов (issues #25, #32).
        Режимы не смешиваются: ``object_type`` внешних форм
        (``ExternalDataProcessor`` / ``ExternalReport``) не пересекается с
        типами конфигурации.
    include_elem_only:
        Если ``True`` (по умолчанию), после основного обхода добавляет
        elem-формы без ``.obj.bsl``, обнаруженные через ``discover_elem_forms``
        (issue #57). Управляемые формы в конфигурациях смешанного типа
        попадают в единый ``FormScanIndex``.

    Возвращает
    ----------
    :class:`FormScanIndex` с найденными формами. Ошибка отдельной формы не
    останавливает обход (best-effort). Единый индекс содержит обычные,
    внешние и elem-формы.
    """
    root = Path(cf_export_root)
    forms: list[FormEntry] = []
    scan_warnings: list[str] = []

    if not root.is_dir():
        scan_warnings.append(f"cf_export_root not found or not a directory: {root}")
        return FormScanIndex(
            forms=[],
            total=0,
            scanned_at=datetime.now(tz=timezone.utc).isoformat(),
            scan_warnings=scan_warnings,
        )

    if mode == "external":
        _scan_external(root, forms, scan_warnings)
    else:
        _scan_config(root, forms, scan_warnings)

    if include_elem_only:
        existing_form_paths: set[Path] = {
            e.form_path.resolve() for e in forms
        }
        _collect_elem_only_forms(root, existing_form_paths, forms, scan_warnings, mode)

    index = FormScanIndex(
        forms=forms,
        total=len(forms),
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        scan_warnings=scan_warnings,
    )

    if save_to is not None:
        index.save(Path(save_to))

    return index


def _configure_cli_output() -> None:
    """Настроить UTF-8 stdout для CLI-вывода на Windows/CI."""
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    """CLI-entrypoint для scan_forms."""
    import argparse

    _configure_cli_output()

    parser = argparse.ArgumentParser(
        description="Сканировать cf_export и собрать индекс форм."
    )
    parser.add_argument(
        "root",
        type=Path,
        help="Корень cf_export (config) либо каталог с External/ (external)",
    )
    parser.add_argument(
        "--mode",
        choices=["config", "external"],
        default="config",
        help="Режим сканирования: config (по умолчанию) или external",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Сохранить forms_scan_index.json в root",
    )
    parser.add_argument(
        "--no-elem-only",
        action="store_true",
        help="Не добавлять elem-only формы (управляемые без .obj.bsl)",
    )

    args = parser.parse_args()

    save_to = args.root / "forms_scan_index.json" if args.save else None
    index = scan_forms(
        args.root,
        save_to=save_to,
        mode=args.mode,
        include_elem_only=not args.no_elem_only,
    )

    print(f"Найдено форм: {len(index.forms)}")
    if save_to is not None:
        print(f"Индекс сохранён: {save_to}")


if __name__ == "__main__":
    main()