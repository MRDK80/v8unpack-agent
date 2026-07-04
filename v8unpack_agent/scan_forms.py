"""scan_forms — обобщённый обход *Form-контейнеров и сборка FormScanIndex.

Реализует issues #9, #13 и #25.

v8unpack формирует несколько layout-ов.

**4-уровневый** (большинство объектов конфигурации)::

    cf_export/<Тип>/<Объект>/<ContainerName>/<ИмяФормы>/

**3-уровневый** (общие формы — нет объекта-владельца)::

    cf_export/CommonForm/<ИмяФормы>/

**External** (распакованные внешние обработки, mode="external", issue #25)::

    External/<имя обработки>/Form/<ИмяФормы>/Form.obj

Отличия External от конфигурации:
- bsl-файл называется ``Form.obj`` (БЕЗ суффикса ``.bsl``);
- верхний уровень — имя конкретной обработки, а не ``object_type``;
- формы вложены через фиксированный контейнер ``Form/<ИмяФормы>/``.

Артефакты формы конфигурации::

    <ContainerName>.obj.bsl
    <ContainerName>.json

Артефакты формы внешней обработки::

    Form.obj      # bsl формы
    Form.json     # метаданные
    Form.elem     # структура формы (элементы)
    Form.id

OS-нейтральность:
- Пути строятся через :mod:`pathlib` / :func:`os.path.join`.
- Текст читается/пишется как UTF-8 явно.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# --- константы структуры External (issue #25) ------------------------------
EXTERNAL_ROOT = "External"
EXTERNAL_OBJECT_TYPE = "ExternalDataProcessor"
EXTERNAL_FORM_CONTAINER = "Form"
EXTERNAL_BSL_NAME = "Form.obj"
EXTERNAL_JSON_NAME = "Form.json"
EXTERNAL_ELEM_NAME = "Form.elem"


@dataclass
class FormEntry:
    """Одна форма, найденная при сканировании cf_export."""

    object_type: str
    """Тип метаобъекта: ``Catalog``, ``Document``, ``CommonForm`` и т.д.
    Для внешних обработок — ``ExternalDataProcessor`` (не пересекается с
    типами конфигурации)."""

    object_name: str
    """Имя метаобъекта. Пустая строка для общих форм (CommonForm-layout).
    Для внешних обработок — имя конкретной обработки."""

    container_name: str
    """Имя контейнера: ``CatalogForm``, ``Form``, ``CommonForm`` и т.д."""

    form_name: str
    """Имя директории формы."""

    form_path: Path
    """Абсолютный путь к директории формы."""

    bsl_path: Path
    """Путь к bsl-файлу формы: ``<ContainerName>.obj.bsl`` (config) либо
    ``Form.obj`` (external)."""

    json_path: Path
    """Путь к json-файлу формы."""

    warnings: list[str] = field(default_factory=list)

    bsl_mtime: float = 0.0
    """mtime bsl-файла на момент сканирования. Baseline для
    ``drift_checker.check_drift()``. 0.0 означает «не известно»."""

    form_elem_path: Optional[Path] = None
    """Путь к ``Form.elem`` (структура формы внешней обработки). ``None`` —
    файла нет либо это форма конфигурации (issue #25). Additive-поле:
    дефолт сохраняет обратную совместимость индекса."""


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


def _is_form_container(directory: Path) -> bool:
    """True, если каталог является контейнером форм (*Form)."""
    return directory.is_dir() and directory.name.endswith("Form")


def _scan_form_dir(
    form_dir: Path,
    object_type: str,
    object_name: str,
    container_name: str,
) -> Optional[FormEntry]:
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
            entry = _scan_form_dir(form_dir, object_type, object_name, container_name)
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
    object_name: str,
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Собрать FormEntry из директории формы внешней обработки (issue #25).

    Обязательный артефакт — ``Form.obj`` (БЕЗ ``.bsl``). Его отсутствие →
    best-effort skip с предупреждением. ``Form.elem`` опционален.
    """
    bsl_path = form_dir / EXTERNAL_BSL_NAME
    if not bsl_path.exists():
        msg = f"skipped (no {EXTERNAL_BSL_NAME}): {form_dir.relative_to(root).as_posix()}"
        scan_warnings.append(msg)
        logger.debug(msg)
        return

    json_path = form_dir / EXTERNAL_JSON_NAME
    elem_path = form_dir / EXTERNAL_ELEM_NAME

    warnings: list[str] = []
    if not json_path.exists():
        warnings.append(f"missing {EXTERNAL_JSON_NAME}")

    try:
        bsl_mtime = bsl_path.stat().st_mtime
    except OSError:
        bsl_mtime = 0.0

    forms.append(FormEntry(
        object_type=EXTERNAL_OBJECT_TYPE,
        object_name=object_name,
        container_name=EXTERNAL_FORM_CONTAINER,
        form_name=form_dir.name,
        form_path=form_dir.resolve(),
        bsl_path=bsl_path.resolve(),
        json_path=json_path.resolve(),
        warnings=warnings,
        bsl_mtime=bsl_mtime,
        form_elem_path=elem_path.resolve() if elem_path.exists() else None,
    ))


def _scan_external(
    root: Path,
    forms: list[FormEntry],
    scan_warnings: list[str],
) -> None:
    """Обход структуры External/<имя обработки>/Form/<форма>/ (issue #25).

    Устойчив к обоим вариантам корня: если внутри ``root`` есть каталог
    ``External/`` — идём от него; иначе ``root`` уже указывает на уровень
    обработок. Тихого fallback между режимами нет — режим задаётся явно.
    """
    external_root = root / EXTERNAL_ROOT
    if not external_root.is_dir():
        external_root = root

    for proc_dir in sorted(external_root.iterdir()):
        if not proc_dir.is_dir():
            continue
        object_name = proc_dir.name

        container = proc_dir / EXTERNAL_FORM_CONTAINER
        if not container.is_dir():
            continue

        for form_dir in sorted(container.iterdir()):
            if not form_dir.is_dir():
                continue
            try:
                _scan_external_form_dir(
                    form_dir, object_name, root, forms, scan_warnings
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


def scan_forms(
    cf_export_root: Path,
    save_to: Optional[Path] = None,
    mode: Literal["config", "external"] = "config",
) -> FormScanIndex:
    """Обойти ``cf_export_root`` и собрать FormScanIndex.

    Параметры
    ---------
    cf_export_root:
        Корень выгрузки. Для ``mode="config"`` — ``Catalog/``, ``Document/``,
        ``CommonForm/`` и др. Для ``mode="external"`` — каталог с ``External/``
        либо непосредственно уровень обработок.
    save_to:
        Если задан, сохранить JSON-индекс в этот файл.
    mode:
        ``"config"`` (по умолчанию) — структура конфигурации; ``"external"`` —
        структура распакованных внешних обработок (issue #25). Режимы не
        смешиваются: ``object_type`` внешних форм (``ExternalDataProcessor``)
        не пересекается с типами конфигурации.

    Возвращает
    ----------
    :class:`FormScanIndex` с найденными формами. Ошибка отдельной формы не
    останавливает обход (best-effort).
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
        help="Сохранить forms_index.json в root",
    )

    args = parser.parse_args()

    save_to = args.root / "forms_scan_index.json" if args.save else None
    index = scan_forms(args.root, save_to=save_to, mode=args.mode)

    print(f"Найдено форм: {len(index.forms)}")
    if save_to is not None:
        print(f"Индекс сохранён: {save_to}")


if __name__ == "__main__":
    main()