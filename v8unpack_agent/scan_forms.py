"""scan_forms — обобщённый обход *Form-контейнеров и сборка FormScanIndex.

Реализует issues #9, #13, #25, #32, #38, #40, #57, #88.

v8unpack формирует несколько layout-ов.

**4-уровневый** (большинство объектов конфигурации)::

    cf_export/<Тип>/<Объект>/<ContainerName>/<ИмяФормы>/

**3-уровневый** (общие формы — нет объекта-владельца)::

    cf_export/CommonForm/<ИмяФормы>/

**External** (распакованные внешние обработки/отчёты, mode="external")::

    External/<имя обработки>/Form/<ИмяФормы>/Form.obj.bsl
    External/<имя отчёта>/ReportForm/<ИмяФормы>/ReportForm.obj.bsl

**Elem-формы** (issue #57): управляемые формы без ``.obj.bsl`` обнаруживаются
через :func:`~v8unpack_agent.managed_forms.discover_elem_forms` и попадают в единый
``FormScanIndex`` с заполненным ``elem_json_path``.

**Ссылочные типы** (issue #88): во время того же обхода конфигурации собирается
глобальный индекс ``uuid типа -> имя ссылочного типа``
(``CatalogRef.<Имя>``, ``DocumentRef.<Имя>``). Второй обход дерева не вводится.
Индекс используется как ``type_resolver`` для
:func:`~v8unpack_agent.object_decoder.decode_object_attributes`.

OS-нейтральность:
- Пути строятся через :mod:`pathlib`.
- Текст читается/пишется как UTF-8 явно.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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

# Контейнер, который однозначно определяет тип «отчёт».
EXTERNAL_REPORT_CONTAINER = "ReportForm"
EXTERNAL_REPORT_OBJECT_TYPE = "ExternalReport"

# Fallback-тип, если тип не удалось определить (обратная совместимость).
EXTERNAL_DEFAULT_OBJECT_TYPE = "ExternalDataProcessor"

# --- ссылочные типы (issue #88) ---------------------------------------------
# Единая таблица соответствия вида метаданных и префикса ссылки.
# Включены только виды, у которых есть ссылочный тип и которые подтверждены
# на контрольной выгрузке. Регистры ссылочного типа не имеют и не входят сюда.
REFERENCE_TYPE_PREFIXES: dict[str, str] = {
    "Catalog": "CatalogRef",
    "Document": "DocumentRef",
    "Enum": "EnumRef",
    "ChartOfCharacteristicType": "ChartOfCharacteristicTypeRef",
    "ExchangePlan": "ExchangePlanRef",
    "BusinessProcess": "BusinessProcessRef",
    "Task": "TaskRef",
}

# Слоты блока идентификации ``header[0][1]``, в которых лежат идентификаторы
# объекта метаданных. У объекта их несколько, и ссылка реквизита адресует
# не всегда слот 2: на контрольной выгрузке встречаются также слоты 1 и 3.
# Все они принадлежат одному объекту, поэтому имя типа для них одно и то же.
_IDENTITY_UUID_SLOTS = (1, 2, 3)

# Структурно значимые поля нормализованного элемента (issue #40).
# Косметические поля (left, top, width, height, color, font, guid, …)
# намеренно исключены, чтобы правка разметки без смысловых изменений
# не порождала ложный structure drift.
_ELEM_STRUCTURAL_KEYS = frozenset({
    "name", "type", "path", "parent", "parent_path",
    "page", "source", "data_path", "handler",
})


SCAN_WARNING_CODE_MARKER = " [code="
"""Маркер стабильного суффикса машинного кода предупреждения (issue #167)."""

SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE = "REFERENCE_METADATA_INCOMPLETE"
SCAN_WARNING_REFERENCE_UUID_CONFLICT = "REFERENCE_UUID_CONFLICT"
SCAN_WARNING_FORM_MODULE_MISSING = "FORM_MODULE_MISSING"
SCAN_WARNING_FORM_SCAN_ERROR = "FORM_SCAN_ERROR"
SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE = "ELEM_DISCOVERY_UNAVAILABLE"
SCAN_WARNING_SCAN_ROOT_INVALID = "SCAN_ROOT_INVALID"

SCAN_WARNING_CODES = frozenset(
    {
        SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE,
        SCAN_WARNING_FORM_MODULE_MISSING,
        SCAN_WARNING_FORM_SCAN_ERROR,
        SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE,
        SCAN_WARNING_REFERENCE_UUID_CONFLICT,
        SCAN_WARNING_SCAN_ROOT_INVALID,
    }
)
"""Полный перечень машинных кодов scan_warnings (issue #167)."""

_SCAN_WARNING_CODE_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _format_scan_warning(code: str, message: str) -> str:
    """Собрать предупреждение: исходный текст + стабильный суффикс кода.

    Внутренний API: предупреждения формирует scan_forms, а не потребитель.
    """
    if code not in SCAN_WARNING_CODES:
        raise ValueError(f"unknown scan warning code: {code!r}")
    return f"{message}{SCAN_WARNING_CODE_MARKER}{code}]"


def scan_warning_code(warning: str) -> str | None:
    """Вернуть машинный код предупреждения либо None (issue #167).

    None означает: legacy-запись без кода, повреждённый суффикс либо код вне
    задокументированного перечня. Исключений не бросает, текст не меняет.
    """
    _, marker, tail = warning.rpartition(SCAN_WARNING_CODE_MARKER)
    if not marker or not tail.endswith("]"):
        return None
    code = tail[:-1]
    if not code or not set(code) <= _SCAN_WARNING_CODE_ALPHABET:
        return None
    return code if code in SCAN_WARNING_CODES else None

def _compute_sha256(path: Path) -> str | None:
    """Вернуть hex-дайджест SHA-256 содержимого файла или None при ошибке."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _compute_elem_sha256(form_dir: Path) -> str | None:
    """Вычислить SHA-256 нормализованного дерева элементов формы (issue #40).

    Граница достоверности: вложенность групп не реконструируется; хэш строится
    по достоверной части дерева.
    """
    try:
        from v8unpack_agent.elem_parser import (
            parse_elem_json,  # local import — избегаем цикл
        )
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
    object_name: str
    container_name: str
    form_name: str
    form_path: Path
    bsl_path: Path
    json_path: Path

    warnings: list[str] = field(default_factory=list)

    bsl_mtime: float = 0.0
    """mtime bsl-файла на момент сканирования (legacy-fallback для drift)."""

    form_elem_path: Path | None = None
    """Путь к ``Form.elem`` внешнего объекта (issue #25)."""

    bsl_sha256: str | None = None
    """SHA-256 содержимого bsl-файла (issue #38)."""

    elem_sha256: str | None = None
    """SHA-256 нормализованного дерева элементов (issue #40)."""

    elem_json_path: Path | None = None
    """Путь к ``*.elem.json``, relative-to-root (issue #57)."""


@dataclass
class FormScanIndex:
    """Результат сканирования cf_export."""

    forms: list[FormEntry] = field(default_factory=list)
    total: int = 0
    scanned_at: str = ""
    scan_warnings: list[str] = field(default_factory=list)
    reference_types: dict[str, str] = field(default_factory=dict)
    """Индекс ``uuid типа -> имя ссылочного типа`` (issue #88)."""

    def resolve_reference_type(self, uuid: str) -> str | None:
        """Вернуть читаемое имя ссылочного типа либо ``None`` (issue #88).

        Подходит как ``type_resolver`` для ``decode_object_attributes``:
        неизвестный UUID оставляет безопасный fallback ``Ref#<uuid>``.
        """
        return self.reference_types.get(uuid)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "scanned_at": self.scanned_at,
            "scan_warnings": self.scan_warnings,
            "reference_types": self.reference_types,
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
    def load(cls, index_path: Path) -> FormScanIndex:
        """Загрузить :class:`FormScanIndex` из JSON-файла, сохранённого :meth:`save`.

        Обратная совместимость: отсутствующие ``bsl_sha256`` / ``elem_sha256`` /
        ``elem_json_path`` → ``None``; отсутствующий ``reference_types`` → ``{}``;
        старое поле ``form_xml_path`` игнорируется.
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
                elem_sha256=row.get("elem_sha256"),  # None for old indexes
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
            reference_types=dict(raw.get("reference_types", {})),
        )


def _first_existing(directory: Path, candidates: tuple[str, ...]) -> Path | None:
    """Вернуть первый существующий файл из candidates (по приоритету) или None."""
    for name in candidates:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _external_bsl_candidates(container_name: str) -> tuple[str, ...]:
    """Кандидаты bsl-файла формы: .bsl (v8unpack 1.2.11), затем legacy."""
    return (f"{container_name}.obj.bsl", f"{container_name}.obj")


def _resolve_external_object_type(proc_dir: Path, container_name: str) -> str:
    """Определить тип внешнего объекта (issue #32)."""
    if container_name == EXTERNAL_REPORT_CONTAINER:
        return EXTERNAL_REPORT_OBJECT_TYPE
    for object_type, candidates in EXTERNAL_OBJECT_MODULE_CANDIDATES.items():
        if _first_existing(proc_dir, candidates) is not None:
            return object_type
    return EXTERNAL_DEFAULT_OBJECT_TYPE


def _is_form_container(directory: Path) -> bool:
    """True, если каталог является контейнером форм (*Form)."""
    return directory.is_dir() and directory.name.endswith("Form")


def _find_elem_json_path(form_dir: Path, root: Path) -> Path | None:
    """Найти ``*.elem.json`` и вернуть relative-to-root путь (issue #57)."""
    elem_files = sorted(form_dir.glob("*.elem.json"))
    if not elem_files:
        return None
    return elem_files[0].relative_to(root)


# --- индекс ссылочных типов (issue #88) --------------------------------------

def _is_metadata_uuid(value: object) -> bool:
    """Проверить, что значение — канонический UUID."""
    if not isinstance(value, str) or len(value) != 36:
        return False
    parts = value.split("-")
    if [len(part) for part in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(char in "0123456789abcdefABCDEF" for part in parts for char in part)


def _metadata_object_uuids(object_json: Path) -> list[str]:
    """Вернуть все идентификаторы объекта из блока ``header[0][1]``.

    У объекта метаданных несколько UUID, и ссылка реквизита адресует один из них,
    а не обязательно слот 2. Порядок слотов фиксирован, дубли убираются —
    результат детерминирован. Best-effort: ошибка чтения или неполная структура
    дают пустой список без исключения — тип не угадывается.
    """
    try:
        raw = json.loads(object_json.read_text(encoding="utf-8-sig"))
        identity = raw["header"][0][1]
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return []

    if not isinstance(identity, list):
        return []

    found: list[str] = []
    for slot in _IDENTITY_UUID_SLOTS:
        if slot >= len(identity):
            continue
        candidate = identity[slot]
        if _is_metadata_uuid(candidate) and candidate not in found:
            found.append(candidate)
    return found


def _collect_reference_type(
    obj_dir: Path,
    object_type: str,
    object_name: str,
    reference_types: dict[str, str],
    scan_warnings: list[str],
) -> None:
    """Дополнить индекс UUID → имя типа в точке уже идущего обхода (issue #88).

    Второй обход дерева не выполняется: функция вызывается из :func:`_scan_config`
    для того же каталога объекта, который уже перебирается ради контейнеров форм.
    Все идентификаторы одного объекта ведут на одно имя типа; конфликт разных
    объектов по одному UUID фиксируется предупреждением, первая запись сохраняется.
    """
    prefix = REFERENCE_TYPE_PREFIXES.get(object_type)
    if prefix is None:
        return

    candidates = (obj_dir / f"{object_type}.json", obj_dir / f"{object_name}.json")
    object_json = next((path for path in candidates if path.is_file()), None)
    if object_json is None:
        return

    uuids = _metadata_object_uuids(object_json)
    if not uuids:
        scan_warnings.append(
            _format_scan_warning(
                SCAN_WARNING_REFERENCE_METADATA_INCOMPLETE,
                f"reference type metadata is incomplete: {object_json.name}",
            )
        )
        return

    type_name = f"{prefix}.{object_name}"
    for uuid in uuids:
        previous = reference_types.get(uuid)
        if previous is None:
            reference_types[uuid] = type_name
        elif previous != type_name:
            scan_warnings.append(
                _format_scan_warning(
                    SCAN_WARNING_REFERENCE_UUID_CONFLICT,
                    f"reference type duplicate UUID {uuid}: "
                    f"kept {previous}, skipped {type_name}",
                )
            )


def _scan_form_dir(
    form_dir: Path,
    object_type: str,
    object_name: str,
    container_name: str,
    root: Path,
) -> FormEntry | None:
    """Собрать FormEntry из директории формы конфигурации.

    Возвращает ``None``, если обязательный артефакт ``.obj.bsl`` отсутствует.
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
                scan_warnings.append(
                    _format_scan_warning(SCAN_WARNING_FORM_MODULE_MISSING, msg)
                )
                logger.debug(msg)
        except Exception as exc:  # noqa: BLE001
            msg = f"error scanning {form_dir}: {exc}"
            scan_warnings.append(
                _format_scan_warning(SCAN_WARNING_FORM_SCAN_ERROR, msg)
            )
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
    """Собрать FormEntry из директории формы внешнего объекта (issues #25, #32)."""
    candidates = _external_bsl_candidates(container_name)
    bsl_path = _first_existing(form_dir, candidates)
    if bsl_path is None:
        names = " / ".join(candidates)
        msg = f"skipped (no {names}): {form_dir.relative_to(root).as_posix()}"
        scan_warnings.append(
            _format_scan_warning(SCAN_WARNING_FORM_MODULE_MISSING, msg)
        )
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
    """Обход структуры External/<объект>/<контейнер>/<форма>/ (issues #25, #32)."""
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
                    scan_warnings.append(
                        _format_scan_warning(SCAN_WARNING_FORM_SCAN_ERROR, msg)
                    )
                    logger.warning(msg)


def _scan_config(
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
    reference_types: dict[str, str] | None = None,
) -> None:
    """Обход структуры конфигурации (4- и 3-уровневый layout). Логика #9/#13.

    Если передан ``reference_types``, тот же обход попутно наполняет индекс
    ссылочных типов (issue #88) — без второго discovery.
    """
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

            if reference_types is not None:
                _collect_reference_type(
                    obj_dir, object_type, object_name, reference_types, scan_warnings
                )

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
    """Добавить elem-формы, не попавшие в обычный/external scan (issue #57)."""
    try:
        from v8unpack_agent.managed_forms import discover_elem_forms  # local import
    except ImportError as exc:
        scan_warnings.append(
            _format_scan_warning(
                SCAN_WARNING_ELEM_DISCOVERY_UNAVAILABLE,
                f"cannot import discover_elem_forms: {exc}",
            )
        )
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
    save_to: Path | None = None,
    mode: Literal["config", "external"] = "config",
    include_elem_only: bool = True,
) -> FormScanIndex:
    """Обойти ``cf_export_root`` и собрать FormScanIndex.

    В режиме ``config`` тот же обход попутно строит индекс ссылочных типов
    (``FormScanIndex.reference_types``, issue #88). Ошибка отдельной формы не
    останавливает обход (best-effort).
    """
    root = Path(cf_export_root)
    forms: list[FormEntry] = []
    scan_warnings: list[str] = []
    reference_types: dict[str, str] = {}

    if not root.is_dir():
        scan_warnings.append(
            _format_scan_warning(
                SCAN_WARNING_SCAN_ROOT_INVALID,
                f"cf_export_root not found or not a directory: {root}",
            )
        )
        return FormScanIndex(
            forms=[],
            total=0,
            scanned_at=datetime.now(tz=timezone.utc).isoformat(),
            scan_warnings=scan_warnings,
        )

    if mode == "external":
        _scan_external(root, forms, scan_warnings)
    else:
        _scan_config(root, forms, scan_warnings, reference_types)

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
        reference_types=reference_types,
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
