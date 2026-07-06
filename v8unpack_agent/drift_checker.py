# v8unpack_agent/drift_checker.py
"""drift_checker — детект дрейфа форм через FormScanIndex.

Реализует issues #10, #38.

Сравнивает текущее состояние cf_export_root с ранее сохранённым
FormScanIndex (forms_index.json) и определяет рассинхрон:
какие формы добавились, удалились или изменились.

Алгоритм детекции ``modified`` (issue #38):
- Если в baseline-индексе есть поле ``bsl_sha256`` — сравниваем хэш текущего
  BSL-файла с сохранённым. Изменение только ``mtime`` / пересоздание файла с
  тем же содержимым **не** даёт ``modified``.
- Если ``bsl_sha256`` в индексе отсутствует (старый формат без hash-поля) —
  используется legacy-fallback через ``bsl_mtime`` (поведение до fix #38).

OS-нейтральность:
- Пути строятся через pathlib / os.path.join.
- Текст читается/пишется как UTF-8 явно.
- Устройство (CPU/GPU) не предполагается — модуль не использует
  тензорные операции.
"""
from __future__ import annotations

import hashlib
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


def _sha256_file(path: Path) -> Optional[str]:
    """Вернуть hex-дайджест SHA-256 содержимого файла или None при ошибке."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


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


def _index_snapshot(index_path: Path) -> tuple[dict[str, float], dict[str, Optional[str]]]:
    """Построить два словаря из index_path:

    - ``mtime_map``: dict[form_key -> baseline_mtime]  (legacy fallback)
    - ``hash_map``:  dict[form_key -> bsl_sha256 | None]

    ``hash_map[key]`` равно ``None``, если запись не содержит ``bsl_sha256``
    (старый индекс без hash-поля).

    Fallback-поведение mtime (обратная совместимость со старым форматом):
    - Если ``bsl_mtime`` отсутствует или равно 0.0 — берём mtime с диска
      по ``bsl_path`` (старое поведение до fix #22).
    - Если ``bsl_path`` отсутствует на диске — mtime = -1.0.
    """
    mtime_map: dict[str, float] = {}
    hash_map: dict[str, Optional[str]] = {}
    entries = _load_index_dict(index_path)
    for e in entries:
        key = _form_key(
            e.get("object_type", ""),
            e.get("object_name", ""),
            e.get("container_name", ""),
            e.get("form_name", ""),
        )
        # --- hash ---
        hash_map[key] = e.get("bsl_sha256")  # None when absent

        # --- mtime (legacy) ---
        stored_mtime = float(e.get("bsl_mtime", 0.0))
        if stored_mtime != 0.0:
            mtime_map[key] = stored_mtime
        else:
            bsl = e.get("bsl_path", "")
            try:
                mtime = Path(bsl).stat().st_mtime if bsl else -1.0
            except OSError:
                mtime = -1.0
            mtime_map[key] = mtime
    return mtime_map, hash_map


def _disk_snapshot(cf_export_root: Path) -> dict[str, tuple[float, Optional[str]]]:
    """Обойти cf_export_root и вернуть dict[form_key -> (bsl_mtime, bsl_sha256)].

    Повторяет логику scan_forms: 4-уровневый layout и 3-уровневый
    (CommonForm). Форма без .obj.bsl не включается.
    Best-effort: ошибки одной формы не останавливают обход.
    """
    root = Path(cf_export_root)
    snapshot: dict[str, tuple[float, Optional[str]]] = {}

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
                        snapshot[key] = (bsl.stat().st_mtime, _sha256_file(bsl))
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
                            snapshot[key] = (bsl.stat().st_mtime, _sha256_file(bsl))
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

    Детекция ``modified`` (issue #38):
    - При наличии ``bsl_sha256`` в baseline сравнивается hash текущего
      BSL-файла с сохранённым. Изменение только ``mtime`` не даёт
      ``modified``.
    - Без ``bsl_sha256`` (старый индекс) используется legacy-fallback
      через ``bsl_mtime``.
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
        index_mtime, index_hash = _index_snapshot(ipath)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to load index %s: %s", ipath, exc)
        index_mtime, index_hash = {}, {}

    disk_snap = _disk_snapshot(root)

    index_keys = set(index_mtime)
    disk_keys = set(disk_snap)

    added = sorted(disk_keys - index_keys)
    removed = sorted(index_keys - disk_keys)

    modified: list[str] = []
    for k in sorted(index_keys & disk_keys):
        disk_mtime, disk_hash = disk_snap[k]
        baseline_hash = index_hash.get(k)  # None → old index
        if baseline_hash is not None:
            # Hash-based detection (issue #38)
            if disk_hash != baseline_hash:
                modified.append(k)
        else:
            # Legacy mtime fallback for old indexes without bsl_sha256
            if abs(disk_mtime - index_mtime[k]) > 1.0:
                modified.append(k)

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
