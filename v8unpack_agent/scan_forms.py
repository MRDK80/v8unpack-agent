"""scan_forms — обобщённый обход *Form-контейнеров и сборка FormScanIndex.

Реализует issues #9 и #13.

v8unpack формирует два layout-а:

**4-уровневый** (большинство объектов)::

    cf_export/<Тип>/<Объект>/<ContainerName>/<ИмяФормы>/

**3-уровневый** (общие формы — нет объекта-владельца)::

    cf_export/CommonForm/<ИмяФормы>/

POO различения без фиксов v8unpack: если верхний каталог сам
заканчивается на ``Form`` — это 3-уровневый layout (``object_name=""``).

Артефакты каждой формы::

    <ContainerName>.obj.bsl
    <ContainerName>.json

Контейнер ``Form`` покрывает два типа объектов — ``DataProcessor`` (внутри
``.cf``) и ``ExternalDataProcessor`` (``.epf``); различать по ``object_type``.
Контейнер ``ReportForm`` покрывает ``Report`` и ``ExternalReport``; различать
по ``object_type``. Изменений в v8unpack не планируется — это каноничный
layout.

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
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FormEntry:
    """Одна форма, найденная при сканировании cf_export."""

    object_type: str
    """Тип метаобъекта: ``Catalog``, ``Document``, ``CommonForm`` и т.д."""

    object_name: str
    """Имя метаобъекта. Пустая строка для общих форм (CommonForm-layout)."""

    container_name: str
    """Имя контейнера: ``CatalogForm``, ``Form``, ``CommonForm`` и т.д."""

    form_name: str
    """Имя директории формы."""

    form_path: Path
    """Абсолютный путь к директории формы."""

    bsl_path: Path
    """Путь к ``<ContainerName>.obj.bsl``."""

    json_path: Path
    """Путь к ``<ContainerName>.json``."""

    warnings: list[str] = field(default_factory=list)


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
    """Попытаться собрать FormEntry из директории формы.

    Возвращает ``None``, если обязательный артефакт ``.obj.bsl`` отсутствует.
    """
    bsl_path = form_dir / (container_name + ".obj.bsl")
    json_path = form_dir / (container_name + ".json")

    if not bsl_path.exists():
        return None

    warnings: list[str] = []
    if not json_path.exists():
        warnings.append(f"missing {json_path.name}")

    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container_name,
        form_name=form_dir.name,
        form_path=form_dir.resolve(),
        bsl_path=bsl_path.resolve(),
        json_path=json_path.resolve(),
        warnings=warnings,
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
    """Обход всех форм внутри контейнера, best-effort."""
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


def scan_forms(
    cf_export_root: Path,
    save_to: Optional[Path] = None,
) -> FormScanIndex:
    """Обойти ``cf_export_root`` и собрать FormScanIndex.

    Параметры
    ---------
    cf_export_root:
        Корень выгрузки (``Catalog/``, ``Document/``, ``CommonForm/`` и др.).
    save_to:
        Если задан, сохранить JSON-индекс в этот файл.

    Возвращает
    ----------
    :class:`FormScanIndex` с найденными формами.

    Логика обхода
    -------------
    4-уровневый layout (большинство объектов)::

        cf_export_root/
          <object_type>/          # Catalog, Document …
            <object_name>/        # Склады, АктСписания …
              <ContainerName>/    # *Form
                <form_name>/
                  <ContainerName>.obj.bsl
                  <ContainerName>.json

    3-уровневый layout (общие формы, нет object_name)::

        cf_export_root/
          CommonForm/             # сам каталог заканчивается на Form
            <form_name>/
              CommonForm.obj.bsl
              CommonForm.json

    Ошибка отдельной формы не останавливает обход (best-effort).
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

    for type_dir in sorted(root.iterdir()):
        if not type_dir.is_dir():
            continue
        object_type = type_dir.name

        # --- 3-уровневый layout: CommonForm и аналоги ---
        # type_dir сам является контейнером форм;
        # вложенные каталоги — непосредственно form_nameе.
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

    index = FormScanIndex(
        forms=forms,
        total=len(forms),
        scanned_at=datetime.now(tz=timezone.utc).isoformat(),
        scan_warnings=scan_warnings,
    )

    if save_to is not None:
        index.save(Path(save_to))

    return index
