# v8unpack_agent/drift_checker.py
"""drift_checker — детект дрейфа форм через FormScanIndex.

Реализует issue #10.

Сравнивает текущее состояние cf_export_root с ранее сохранённым
FormScanIndex (forms_index.json) и определяет рассинхрон:
какие формы добавились, удалились или изменились.

Детекция ``modified`` работает при наличии поля ``bsl_mtime``
в индексе (issue #18). Старые индексы без этого поля
(или с значением 0.0) возвращают ``modified=[]`` (обратная совместимость).

OS-нейтральность:
- Пути строятся через pathlib / os.path.join.
- Текст читается/пишется как UTF-8 явно.
- Устройство (CPU/GPU) не предполагается — модуль не использует
  тензорные операции.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Тип ключа формы: object_type/object_name/container_name/form_name
# Для CommonForm-layout object_name пустой: "CommonForm//CommonForm/ФормаИмя"
# ---------------------------------------------------------------------------
_KEY_SEP = "/"


def _form_key(
    object_type: str,
    object_name: str,
    container_name: str,
    form_name: str,
) -> str:
    return _KEY_SEP.join([object_type, object_name, container_name, form_name])


# ---------------------------------------------------------------------------
# DriftReport
# ---------------------------------------------------------------------------

@dataclass
class DriftReport:
    """Результат проверки дрейфа."""

    has_drift: bool
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    stale_extractions: list[str] = field(default_factory=list)
    checked_at: str = ""

    def save_to(self, path: Path) -> Path:
        """Сохранить отчёт как UTF-8 JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load_from(cls, path: Path) -> "DriftReport":
        """Загрузить отчёт из JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


# ---------------------------------------------------------------------------
# Внутренние функции
# ---------------------------------------------------------------------------

def _load_index_dict(index_path: Path) -> list[dict]:
    """Загрузить список записей форм из forms_index.json.

    Возвращает список dict-записей. Бросает FileNotFoundError если файл
    не найден.
    """
    data = json.loads(index_path.read_text(encoding="utf-8"))
    return data.get("forms", [])


def _index_snapshot(index_path: Path) -> dict[str, float]:
    """Построить dict[form_key -> bsl_mtime] из index_path.

    mtime читается из поля ``bsl_mtime`` JSON-записи (issue #18).
    Если поле отсутствует (старый индекс) — fallback 0.0.
    Значение 0.0 трактуется как «базелайн отсутствует»: в
    ``check_drift`` такие формы пропускаются в ``modified``.
    """
    snapshot: dict[str, float] = {}
    entries = _load_index_dict(index_path)
    for e in entries:
        key = _form_key(
            e.get("object_type", ""),
            e.get("object_name", ""),
            e.get("container_name", ""),
            e.get("form_name", ""),
        )
        snapshot[key] = float(e.get("bsl_mtime", 0.0))
    return snapshot


def _disk_snapshot(cf_export_root: Path) -> dict[str, float]:
    """Обойти cf_export_root и вернуть dict[form_key -> bsl_mtime].

    Повторяет логику scan_forms: 4-уровневый layout и 3-уровневый
    (CommonForm). Форма без .obj.bsl не включается.
    Best-effort: ошибки одной формы не останавливают обход.
    """
    root = Path(cf_export_root)
    snapshot: dict[str, float] = {}

    if not root.is_dir():
        return snapshot

    for type_dir in root.iterdir():
        if not type_dir.is_dir():
            continue
        object_type = type_dir.name

        # 3-уровневый layout: CommonForm и аналоги
        if type_dir.name.endswith("Form"):
            container_name = type_dir.name
            for form_dir in type_dir.iterdir():
                if not form_dir.is_dir():
                    continue
                bsl = form_dir / (container_name + ".obj.bsl")
                try:
                    if bsl.exists():
                        key = _form_key(object_type, "", container_name, form_dir.name)
                        snapshot[key] = bsl.stat().st_mtime
                except OSError as exc:
                    logger.warning("drift scan error %s: %s", form_dir, exc)
            continue

        # 4-уровневый layout
        for obj_dir in type_dir.iterdir():
            if not obj_dir.is_dir():
                continue
            object_name = obj_dir.name
            for container_dir in obj_dir.iterdir():
                if not container_dir.is_dir():
                    continue
                if not container_dir.name.endswith("Form"):
                    continue
                container_name = container_dir.name
                for form_dir in container_dir.iterdir():
                    if not form_dir.is_dir():
                        continue
                    bsl = form_dir / (container_name + ".obj.bsl")
                    try:
                        if bsl.exists():
                            key = _form_key(
                                object_type, object_name, container_name, form_dir.name
                            )
                            snapshot[key] = bsl.stat().st_mtime
                    except OSError as exc:
                        logger.warning("drift scan error %s: %s", form_dir, exc)

    return snapshot


def _stale_keys(index_path: Path) -> list[str]:
    """Вернуть ключи форм, чей .obj.bsl не существует на диске."""
    stale = []
    for e in _load_index_dict(index_path):
        bsl = e.get("bsl_path", "")
        if bsl and not Path(bsl).exists():
            key = _form_key(
                e.get("object_type", ""),
                e.get("object_name", ""),
                e.get("container_name", ""),
                e.get("form_name", ""),
            )
            stale.append(key)
    return stale


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def check_drift(
    cf_export_root: Path,
    index_path: Path,
    save_to: Optional[Path] = None,
) -> DriftReport:
    """Сравнить cf_export_root с сохранённым FormScanIndex.

    Параметры
    ---------
    cf_export_root:
        Корень выгрузки (тот же, что передаётся в scan_forms).
    index_path:
        Путь к forms_index.json, созданному scan_forms().
        Если файл не найден — все формы на диске считаются новыми
        (has_drift=True, added=все).
    save_to:
        Если задан, сохранить DriftReport как JSON по этому пути.

    Возвращает
    ----------
    :class:`DriftReport` с полями added / removed / modified /
    stale_extractions / has_drift.
    """
    root = Path(cf_export_root)
    ipath = Path(index_path)
    now = datetime.now(tz=timezone.utc).isoformat()

    # --- index_path не найден: всё новое ---
    if not ipath.exists():
        disk = _disk_snapshot(root)
        report = DriftReport(
            has_drift=bool(disk),
            added=sorted(disk),
            removed=[],
            modified=[],
            stale_extractions=[],
            checked_at=now,
        )
        if save_to:
            report.save_to(Path(save_to))
        return report

    # --- Штатный путь ---
    try:
        index_snap = _index_snapshot(ipath)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to load index %s: %s", ipath, exc)
        index_snap = {}

    disk_snap = _disk_snapshot(root)

    index_keys = set(index_snap)
    disk_keys = set(disk_snap)

    added = sorted(disk_keys - index_keys)
    removed = sorted(index_keys - disk_keys)
    modified = sorted(
        k for k in index_keys & disk_keys
        if index_snap[k] != 0.0  # 0.0 == baseline отсутствует (старый индекс)
        and abs(disk_snap[k] - index_snap[k]) > 1.0  # 1 сек — допуск на FAT/NTFS
    )

    try:
        stale = sorted(_stale_keys(ipath))
    except Exception as exc:  # noqa: BLE001
        logger.warning("stale check failed: %s", exc)
        stale = []

    has_drift = bool(added or removed or modified or stale)

    report = DriftReport(
        has_drift=has_drift,
        added=added,
        removed=removed,
        modified=modified,
        stale_extractions=stale,
        checked_at=now,
    )
    if save_to:
        report.save_to(Path(save_to))
    return report
