"""RAG-индекс форм 1С на основе плотных векторов (issue #78).

Артефакт ``rag_index.npz``
--------------------------
ZIP_STORED-архив с единственным entry ``vectors.npy``.
Формат: NPY v1.0, dtype ``<f8`` (float64 LE), C-order, 2D shape ``(N, dim)``.
Дата в ZIP фиксируется как (1980,1,1,0,0,0) → детерминированные байты.

Meta ``rag_meta.json``
----------------------
::

    {
        "schema_version": 1,
        "count": N,
        "dimension": D,
        "keys": [[object_type, object_name, container_name, form_name], ...]
    }

Только ключи; пути, BSL и промпт не сохраняются.

Ключ связи
----------
4-кортеж ``(object_type, object_name, container_name, form_name)`` —
тот же, что в :func:`~v8unpack_agent.drift_checker.form_key`.

Ленивые импорты
---------------
``scan_forms`` импортируется только под ``TYPE_CHECKING`` (issue #140).
При runtime-импорте использует строковые аннотации через ``from __future__``.
"""
from __future__ import annotations

import json
import math
import re
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

__all__ = ["FormRagIndex", "RagBuildError", "RagLoadError"]

_SCHEMA_VERSION = 1
_META_NAME = "rag_meta.json"
_NPZ_NAME = "rag_index.npz"
_NPY_ENTRY = "vectors.npy"
_NPY_MAGIC = b"\x93NUMPY"
_NPY_VER = b"\x01\x00"  # NPY v1.0
_DTYPE_STR = "<f8"  # float64 little-endian
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # детерминированный timestamp

# Максимальный допустимый размер NPY до выделения памяти (100 МБ)
_MAX_NPY_BYTES = 100 * 1024 * 1024


class RagBuildError(ValueError):
    """Ошибка при построении индекса: дубликат/конфликт ключа."""


class RagLoadError(ValueError):
    """Ошибка при загрузке артефакта: схема, dtype или shape не совпадают."""


# ---------------------------------------------------------------------------
# NPY v1.0 writer / reader — без numpy в runtime
# ---------------------------------------------------------------------------

def _npy_header(n_rows: int, n_cols: int) -> bytes:
    """Сформировать NPY v1.0 заголовок для 2D float64 C-order массива."""
    # %-формат для шаблона не трогает UP031 (не единственная подстановка).
    descr = (
        "{'descr': '%s', 'fortran_order': False, 'shape': (%d, %d), }"
        % (_DTYPE_STR, n_rows, n_cols)
    )
    prefix_len = len(_NPY_MAGIC) + len(_NPY_VER) + 2  # +2 = uint16 header_len
    raw = descr.encode("latin-1") + b"\n"
    pad = (-len(raw) - prefix_len) % 64
    raw = raw[:-1] + b" " * pad + b"\n"
    hlen = len(raw).to_bytes(2, "little")
    return _NPY_MAGIC + _NPY_VER + hlen + raw


def _encode_matrix(matrix: list[list[float]]) -> bytes:
    """Закодировать 2D список float в NPY v1.0 bytes."""
    if not matrix:
        return _npy_header(0, 0)
    n_rows = len(matrix)
    n_cols = len(matrix[0])
    header = _npy_header(n_rows, n_cols)
    body = struct.pack(
        f"<{n_rows * n_cols}d",
        *(v for row in matrix for v in row),
    )
    return header + body


def _parse_npy_header(hdr_bytes: bytes) -> dict:
    """Разобрать NPY-заголовок через re; возвращает dict с 'descr', 'fortran_order', 'shape'."""
    try:
        text = hdr_bytes.decode("latin-1").strip()
    except Exception as exc:
        raise RagLoadError(f"Cannot decode NPY header: {exc}") from exc

    result: dict = {}

    # descr: 'descr': '<f8'  или  "descr": "<f8"
    # Одинарные кавычки снаружи: двойные внутри r"..." обрывают строку.
    m = re.search(r'[\'"]descr[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', text)
    if not m:
        raise RagLoadError("NPY header missing key 'descr'")
    result["descr"] = m.group(1)

    # fortran_order: True / False (без кавычек)
    m = re.search(r'[\'"]fortran_order[\'"]\s*:\s*(True|False)', text)
    if not m:
        raise RagLoadError("NPY header missing key 'fortran_order'")
    result["fortran_order"] = m.group(1) == "True"

    # shape: (N,) или (N, M) — числа, разделённые запятой внутри скобок
    m = re.search(r'[\'"]shape[\'"]\s*:\s*\(([^)]*)\)', text)
    if not m:
        raise RagLoadError("NPY header missing key 'shape'")
    shape_inner = m.group(1).strip()
    if not shape_inner:
        result["shape"] = ()
    else:
        parts = [p.strip() for p in shape_inner.split(",") if p.strip()]
        try:
            result["shape"] = tuple(int(p) for p in parts)
        except ValueError as exc:
            raise RagLoadError(
                f"Cannot parse shape {shape_inner!r}: {exc}"
            ) from exc

    return result


def _decode_matrix(data: bytes) -> list[list[float]]:
    """Декодировать NPY v1.0 bytes → 2D list[float]. Fail-closed."""
    if len(data) > _MAX_NPY_BYTES:
        raise RagLoadError(
            f"NPY entry size {len(data)} exceeds limit {_MAX_NPY_BYTES}"
        )
    if not data.startswith(_NPY_MAGIC + _NPY_VER):
        raise RagLoadError("NPY magic/version mismatch; expected v1.0")

    hlen = int.from_bytes(data[8:10], "little")
    hdr_bytes = data[10: 10 + hlen]
    hdr = _parse_npy_header(hdr_bytes)

    if hdr["descr"] != _DTYPE_STR:
        raise RagLoadError(
            f"dtype {hdr['descr']!r} != {_DTYPE_STR!r}"
        )
    if hdr["fortran_order"]:
        raise RagLoadError("Only C-order (fortran_order=False) is supported")

    shape = hdr["shape"]
    if len(shape) == 0 or (len(shape) == 2 and shape[0] == 0):
        return []
    if len(shape) != 2:
        raise RagLoadError(
            f"Only 2D arrays are supported, got shape {shape!r}"
        )

    n_rows, n_cols = shape
    expected_body = n_rows * n_cols * 8
    body = data[10 + hlen:]
    if len(body) != expected_body:
        raise RagLoadError(
            f"NPY body size {len(body)} != expected {expected_body}"
        )
    flat = struct.unpack(f"<{n_rows * n_cols}d", body)
    return [list(flat[r * n_cols: (r + 1) * n_cols]) for r in range(n_rows)]


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity ∈ [0, 1]. Возвращает 0 при нулевых векторах."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _entry_key(entry: FormEntry) -> tuple:
    return (
        entry.object_type,
        entry.object_name,
        entry.container_name,
        entry.form_name,
    )


# ---------------------------------------------------------------------------
# FormRagIndex
# ---------------------------------------------------------------------------

@dataclass
class _Record:
    key: tuple
    vector: list[float]


class FormRagIndex:
    """RAG-индекс поверх :class:`~v8unpack_agent.scan_forms.FormScanIndex`.

    Parameters
    ----------
    scan_index:
        Канонический источник :class:`~v8unpack_agent.scan_forms.FormEntry`.
        ``FormRagIndex`` не хранит копии записей — при ``query()`` обращается
        к словарю, построенному из ``scan_index.forms`` в ``__init__``.

    Notes
    -----
    Связь между вектором и записью — 4-кортеж
    ``(object_type, object_name, container_name, form_name)``.
    При дубликате ключа в ``scan_index`` поднимается :exc:`RagBuildError`.
    """

    def __init__(self, scan_index: FormScanIndex) -> None:
        self._scan_index = scan_index
        lookup: dict = {}
        for entry in scan_index.forms:
            k = _entry_key(entry)
            if k in lookup:
                raise RagBuildError(
                    f"Duplicate form key in scan_index: {k!r}"
                )
            lookup[k] = entry
        self._lookup: dict = lookup
        self._records: list[_Record] = []
        self._dim: int = 0

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        contexts: list,
        embedder: Callable[[str], list[float]],
    ) -> None:
        """Вычислить векторы для списка контекстов.

        Parameters
        ----------
        contexts:
            Список :class:`~v8unpack_agent.form_context.FormContext`.
            Каждый контекст должен иметь ключ, присутствующий в ``scan_index``.
        embedder:
            Функция ``str → list[float]``. Вызывается через
            ``to_llm_prompt_fragment(ctx)`` как входной текст.

        Raises
        ------
        RagBuildError
            Если ключ контекста отсутствует в scan_index, является дубликатом
            в переданном списке, или размерности векторов не совпадают.
        """
        from v8unpack_agent.form_context import to_llm_prompt_fragment

        seen: set = set()
        records: list[_Record] = []
        dim: int | None = None

        for ctx in contexts:
            k = (
                ctx.object_type,
                ctx.object_name,
                ctx.container_name,
                ctx.form_name,
            )
            if k not in self._lookup:
                raise RagBuildError(
                    f"Context key {k!r} not found in scan_index"
                )
            if k in seen:
                raise RagBuildError(
                    f"Duplicate context key in build(): {k!r}"
                )
            seen.add(k)

            text = to_llm_prompt_fragment(ctx)
            vec = embedder(text)
            if not isinstance(vec, list) or not vec:
                raise RagBuildError(
                    f"embedder must return non-empty list[float] for key {k!r}"
                )
            if dim is None:
                dim = len(vec)
            elif len(vec) != dim:
                raise RagBuildError(
                    f"dimension mismatch: expected {dim}, got {len(vec)} for key {k!r}"
                )
            records.append(_Record(key=k, vector=vec))

        self._records = records
        self._dim = dim if dim is not None else 0

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        vector: list[float],
        top_k: int = 5,
    ) -> list:
        """Вернуть top_k результатов по cosine similarity.

        Parameters
        ----------
        vector:
            Вектор запроса той же размерности, что у индекса.
        top_k:
            Максимальное число результатов.

        Returns
        -------
        list[RouteResult]
            Каждый результат: ``matched=[entry]``, ``confidence`` = cosine ∈ [0, 1].
            Сортировка: убывание confidence, tie-break по ключу формы (лекс.).
        """
        from v8unpack_agent.form_router import RouteResult

        if not self._records:
            return []

        scored = []
        for rec in self._records:
            score = _cosine(vector, rec.vector)
            scored.append((score, rec.key))

        scored.sort(key=lambda t: (-t[0], t[1]))

        results = []
        for score, key in scored[:top_k]:
            entry = self._lookup[key]
            results.append(
                RouteResult(
                    matched=[entry],
                    confidence=round(score, 6),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def save(self, index_dir: Path) -> None:
        """Сохранить ``rag_index.npz`` и ``rag_meta.json`` в ``index_dir``.

        Параметры не записываются в мета. Пути, BSL и промпт не сохраняются.
        """
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)

        matrix = [r.vector for r in self._records]
        npy_bytes = _encode_matrix(matrix)
        npz_path = index_dir / _NPZ_NAME
        with zipfile.ZipFile(npz_path, "w", compression=zipfile.ZIP_STORED) as zf:
            zi = zipfile.ZipInfo(_NPY_ENTRY, date_time=_ZIP_DATE)
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, npy_bytes)

        meta = {
            "schema_version": _SCHEMA_VERSION,
            "count": len(self._records),
            "dimension": self._dim,
            "keys": [list(r.key) for r in self._records],
        }
        meta_path = index_dir / _META_NAME
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, index_dir: Path) -> None:
        """Загрузить ``rag_index.npz`` и ``rag_meta.json`` из ``index_dir``.

        Raises
        ------
        RagLoadError
            Если схема, dtype, shape или ключи не совпадают с ``scan_index``.
        FileNotFoundError
            Если артефакты отсутствуют.
        """
        index_dir = Path(index_dir)
        npz_path = index_dir / _NPZ_NAME
        meta_path = index_dir / _META_NAME

        raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        sv = raw_meta.get("schema_version")
        if sv != _SCHEMA_VERSION:
            raise RagLoadError(
                f"Unsupported rag_meta schema_version: {sv!r}"
            )
        raw_keys = raw_meta.get("keys", [])
        dimension = int(raw_meta.get("dimension", 0))
        count = int(raw_meta.get("count", 0))

        if len(raw_keys) != count:
            raise RagLoadError(
                f"rag_meta count={count} != len(keys)={len(raw_keys)}"
            )

        keys = []
        for raw_k in raw_keys:
            if len(raw_k) != 4:
                raise RagLoadError(f"Invalid key length: {raw_k!r}")
            k = (raw_k[0], raw_k[1], raw_k[2], raw_k[3])
            if k not in self._lookup:
                raise RagLoadError(
                    f"Key from rag_meta not found in scan_index: {k!r}"
                )
            keys.append(k)

        if len(set(keys)) != len(keys):
            raise RagLoadError("Duplicate keys in rag_meta")

        with zipfile.ZipFile(npz_path, "r") as zf:
            names = zf.namelist()
            if names != [_NPY_ENTRY]:
                raise RagLoadError(
                    f"Expected exactly [{_NPY_ENTRY!r}] in NPZ, got {names!r}"
                )
            npy_bytes = zf.read(_NPY_ENTRY)

        matrix = _decode_matrix(npy_bytes)

        if count == 0:
            self._records = []
            self._dim = 0
            return

        if len(matrix) != count:
            raise RagLoadError(
                f"NPY row count {len(matrix)} != meta count {count}"
            )
        actual_dim = len(matrix[0]) if matrix else 0
        if actual_dim != dimension:
            raise RagLoadError(
                f"NPY dimension {actual_dim} != meta dimension {dimension}"
            )

        self._records = [
            _Record(key=k, vector=row) for k, row in zip(keys, matrix)
        ]
        self._dim = dimension
