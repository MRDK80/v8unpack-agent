"""drift_checker — детект дрейфа форм через FormScanIndex.

Реализует issues #10, #38, #40, #58, #73.

Сравнивает текущее состояние cf_export_root с ранее сохранённым
FormScanIndex (forms_index.json) и определяет рассинхрон:
какие формы добавились, удалились или изменились.

Алгоритм детекции ``modified`` (issue #38):
- Если в baseline-индексе есть поле ``bsl_sha256`` — сравниваем хэш текущего
  BSL-файла с сохранённым. Изменение только ``mtime`` / пересоздание файла с
  тем же содержимым **не** даёт ``modified``.
- Если ``bsl_sha256`` в индексе отсутствует (старый формат без hash-поля) —
  используется legacy-fallback через ``bsl_mtime`` (поведение до fix #38).

Алгоритм детекции ``structure_modified`` (issue #40, #58):
- Независимый от ``modified`` сигнал: сравнивается ``elem_sha256`` из
  baseline-индекса с хэшем текущего нормализованного дерева элементов.
- Если ``elem_sha256`` в baseline отсутствует (старый индекс) — сигнал
  ``structure_modified`` не порождается (обратная совместимость).
- Правка кода формы без изменения разметки: ``modified`` есть,
  ``structure_modified`` нет.
- Добавление/удаления элемента без правки кода: ``structure_modified`` есть,
  ``modified`` нет.
- Elem-only формы (без .obj.bsl) участвуют в ``structure_modified``:
  они присутствуют в index_elem, но отсутствуют в disk_snapshot (нет BSL).
  Пересканирование выполняется с include_elem_only=True.

Поддержка external-layout (issue #73):
- ``_disk_snapshot`` принимает параметр ``mode`` и делегирует обход
  в ``scan_forms(mode=mode, include_elem_only=False)``.
  Собственный hard-coded обход удалён — дублирования логики нет.
- ``check_drift`` принимает параметр ``mode`` (default ``'config'``) и
  пробрасывает его в ``_disk_snapshot`` и в ``scan_forms`` при вычислении
  ``structure_modified``.

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
from typing import Literal, Optional

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
    structure_modified: list[str] = field(default_factory=list)
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
        # Обратная совместимость: старые отчёты без structure_modified
        data.setdefault("structure_modified", [])
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


def _index_snapshot(
    index_path: Path,
) -> tuple[
    dict[str, float],
    dict[str, Optional[str]],
    dict[str, Optional[str]],
    set[str],
]:
    """Построить структуры из index_path.

    Возвращает кортеж из четырёх объектов:

    - ``mtime_map``:    dict[form_key -> baseline_mtime]  (legacy fallback)
    - ``hash_map``:     dict[form_key -> bsl_sha256 | None]
    - ``elem_map``:     dict[form_key -> elem_sha256 | None]
    - ``elem_only_keys``: set[form_key] — ключи форм, у которых
      ``bsl_path`` отсутствует (None/пусто) или не существует на диске
      И при этом ``elem_json_path`` заполнен. Это elem-only формы (#58):
      они участвуют в ``structure_modified``, но не в BSL-based modified/stale.

    ``hash_map[key]`` / ``elem_map[key]`` равны ``None``, если запись
    не содержит соответствующего поля (старый индекс).

    Fallback-поведение mtime (обратная совместимость со старым форматом):
    - Если ``bsl_mtime`` отсутствует или равно 0.0 — берём mtime с диска
      по ``bsl_path`` (старое поведение до fix #22).
    - Если ``bsl_path`` отсутствует на диске — mtime = -1.0.
    """
    mtime_map: dict[str, float] = {}
    hash_map: dict[str, Optional[str]] = {}
    elem_map: dict[str, Optional[str]] = {}
    elem_only_keys: set[str] = set()

    entries = _load_index_dict(index_path)
    for e in entries:
        key = _form_key(
            e.get("object_type", ""),
            e.get("object_name", ""),
            e.get("container_name", ""),
            e.get("form_name", ""),
        )
        # --- hash ---
        hash_map[key] = e.get("bsl_sha256")    # None when absent
        elem_map[key] = e.get("elem_sha256")   # None when absent

        # --- elem-only detection (#58) ---
        # Форма считается elem-only, если у неё нет реального bsl_path
        # (None/пусто/несуществующий файл) И есть elem_json_path.
        bsl_raw = e.get("bsl_path") or ""
        has_real_bsl = bool(bsl_raw) and Path(bsl_raw).exists()
        has_elem_json = bool(e.get("elem_json_path"))
        is_elem_only = has_elem_json and not has_real_bsl
        if is_elem_only:
            elem_only_keys.add(key)

        # --- mtime (legacy) ---
        # Для elem-only форм bsl_mtime бессмысленен, но заполняем -1.0
        # чтобы ключ присутствовал в mtime_map (для set-операций).
        if is_elem_only:
            mtime_map[key] = -1.0
        else:
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

    return mtime_map, hash_map, elem_map, elem_only_keys


def _disk_snapshot(
    cf_export_root: Path,
    mode: Literal["config", "external"] = "config",
) -> dict[str, tuple[float, Optional[str]]]:
    """Обойти cf_export_root и вернуть dict[form_key -> (bsl_mtime, bsl_sha256)].

    Делегирует обход в ``scan_forms(mode=mode, include_elem_only=False)``.
    Поддерживает оба layout: ``config`` (4-уровневый и 3-уровневый CommonForm)
    и ``external`` (External/<объект>/<контейнер>/<форма>/, issue #73).
    Форма без .obj.bsl не включается (include_elem_only=False).
    Best-effort: ошибки одной формы не останавливают обход (внутри scan_forms).
    """
    from v8unpack_agent.scan_forms import scan_forms as _scan_forms

    root = Path(cf_export_root)
    if not root.is_dir():
        return {}

    idx = _scan_forms(root, mode=mode, include_elem_only=False)
    return {
        _form_key(
            e.object_type, e.object_name, e.container_name, e.form_name
        ): (e.bsl_mtime, e.bsl_sha256)
        for e in idx.forms
        if e.bsl_path.exists()
    }


def _stale_keys(index_path: Path, elem_only_keys: set[str]) -> list[str]:
    """Вернуть ключи форм, чей .obj.bsl не существует на диске.

    Elem-only формы (ключи из ``elem_only_keys``) пропускаются: у них
    нет BSL по дизайну — это не признак устаревшей экстракции (#58).
    """
    stale = []
    for e in _load_index_dict(index_path):
        key = _form_key(
            e.get("object_type", ""),
            e.get("object_name", ""),
            e.get("container_name", ""),
            e.get("form_name", ""),
        )
        if key in elem_only_keys:
            # Elem-only форма — нет BSL по дизайну, stale не применяется
            continue
        bsl = e.get("bsl_path", "")
        if bsl and not Path(bsl).exists():
            stale.append(key)
    return stale


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def check_drift(
    cf_export_root: Path,
    index_path: Path,
    save_to: Optional[Path] = None,
    mode: Literal["config", "external"] = "config",
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
    mode:
        Режим обхода диска: ``'config'`` (по умолчанию) или ``'external'``.
        Должен совпадать с режимом, использованным при создании baseline
        через ``scan_forms``. Пробрасывается в ``_disk_snapshot`` и в
        ``scan_forms`` при вычислении ``structure_modified`` (issue #73).

    Возвращает
    ----------
    :class:`DriftReport` с полями added / removed / modified /
    stale_extractions / structure_modified / has_drift.

    Детекция ``modified`` (issue #38):
    - При наличии ``bsl_sha256`` в baseline сравнивается hash текущего
      BSL-файла с сохранённым. Изменение только ``mtime`` не даёт
      ``modified``.
    - Без ``bsl_sha256`` (старый индекс) используется legacy-fallback
      через ``bsl_mtime``.

    Детекция ``structure_modified`` (issue #40, #58):
    - При наличии ``elem_sha256`` в baseline пересчитывается хэш
      нормализованного дерева элементов текущей формы через scan_forms
      (``elem_sha256`` из нового сканирования) и сравнивается с baseline.
    - Если ``elem_sha256`` в baseline отсутствует (старый индекс) —
      сигнал ``structure_modified`` не порождается (обратная совместимость).
    - Сигнал независим от ``modified``.
    - Elem-only формы (#58): участвуют в ``structure_modified`` напрямую
      через ``index_elem`` (не требуют присутствия в disk_snapshot).
      Пересканирование выполняется с ``include_elem_only=True``.
    """
    root = Path(cf_export_root)
    ipath = Path(index_path)
    now = datetime.now(tz=timezone.utc).isoformat()

    # --- index_path не найден: всё новое ---
    if not ipath.exists():
        disk = _disk_snapshot(root, mode=mode)
        report = DriftReport(
            has_drift=bool(disk),
            added=sorted(disk),
            removed=[],
            modified=[],
            stale_extractions=[],
            structure_modified=[],
            checked_at=now,
        )
        if save_to:
            report.save_to(Path(save_to))
        return report

    # --- Штатный путь ---
    try:
        index_mtime, index_hash, index_elem, elem_only_keys = _index_snapshot(ipath)
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to load index %s: %s", ipath, exc)
        index_mtime, index_hash, index_elem, elem_only_keys = {}, {}, {}, set()

    disk_snap = _disk_snapshot(root, mode=mode)

    # index_keys_bsl: только BSL-формы (не elem-only) — для added/removed/modified
    index_keys_bsl = set(index_mtime) - elem_only_keys
    disk_keys = set(disk_snap)

    added = sorted(disk_keys - index_keys_bsl)
    removed = sorted(index_keys_bsl - disk_keys)

    modified: list[str] = []
    for k in sorted(index_keys_bsl & disk_keys):
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

    # --- structure_modified (issue #40 + #58) ---
    # Кандидаты: все ключи из index_elem с непустым baseline elem_sha256.
    # Для BSL-форм — только те, что присутствуют на диске (index_keys_bsl & disk_keys).
    # Для elem-only форм (#58) — проверяем напрямую по ключу в current_elem_map
    # (пересканирование с include_elem_only=True).
    structure_modified: list[str] = []
    keys_with_baseline_elem = {
        k for k, v in index_elem.items() if v is not None
    }
    if keys_with_baseline_elem:
        from v8unpack_agent.scan_forms import scan_forms as _scan_forms
        # include_elem_only=True — чтобы elem-only формы (#58) попали в результат
        # mode пробрасывается для корректного обхода external-layout (#73)
        current_index = _scan_forms(root, mode=mode, include_elem_only=True)
        current_elem_map = {
            _form_key(
                e.object_type, e.object_name, e.container_name, e.form_name
            ): e.elem_sha256
            for e in current_index.forms
        }
        for k in sorted(keys_with_baseline_elem):
            # Для BSL-форм: проверяем только если форма есть на диске
            if k not in elem_only_keys and k not in disk_keys:
                continue
            baseline_elem = index_elem[k]
            current_elem = current_elem_map.get(k)  # None if no elem.json
            if current_elem is not None and current_elem != baseline_elem:
                structure_modified.append(k)

    try:
        stale = sorted(_stale_keys(ipath, elem_only_keys))
    except Exception as exc:  # noqa: BLE001
        logger.warning("stale check failed: %s", exc)
        stale = []

    has_drift = bool(added or removed or modified or stale or structure_modified)

    report = DriftReport(
        has_drift=has_drift,
        added=added,
        removed=removed,
        modified=modified,
        stale_extractions=stale,
        structure_modified=structure_modified,
        checked_at=now,
    )
    if save_to:
        report.save_to(Path(save_to))
    return report
