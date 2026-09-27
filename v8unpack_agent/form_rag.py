"""RAG-индекс форм 1С на основе плотных векторов (issue #78, #305).

Контракт (решение D2 из #78)
----------------------------
::

    index = FormRagIndex(scan_index)
    index.build(contexts, embedder)
    results = index.query("текст запроса", top_k=5)
    index.save(index_dir)

    restored = FormRagIndex(scan_index)
    restored.load(index_dir, embedder)

``embedder`` — функция ``str -> list[float]``, которую передаёт вызывающая
сторона. Модуль не знает о провайдерах, не читает переменные окружения и
не выполняет сетевых вызовов. Эмбеддер не сериализуется, поэтому после
``load()`` его передают явно вторым аргументом.

Артефакт ``rag_index.npz``
--------------------------
ZIP_STORED-архив с единственным entry ``vectors.npy``.
Формат: NPY v1.0, dtype ``<f8`` (float64 LE), C-order, 2D shape ``(N, dim)``.
Дата в ZIP фиксируется как (1980,1,1,0,0,0) → детерминированные байты.

Meta ``rag_meta.json``
----------------------
::

    {
        "schema_version": 2,
        "count": N,
        "dimension": D,
        "vectors_sha256": "<sha256 байтов rag_index.npz>",
        "keys": [[object_type, object_name, container_name, form_name], ...]
    }

Только ключи, размеры и контрольная сумма. Пути, BSL, промпт и текст
эмбеддинга не сохраняются. ``vectors_sha256`` связывает пару файлов:
смешанная по версиям пара не загружается.

Ключ связи
----------
4-кортеж ``(object_type, object_name, container_name, form_name)`` —
тот же, что в :meth:`~v8unpack_agent.form_router.FormRouter.reindex`.

Ленивые импорты
---------------
``scan_forms``, ``form_context`` и ``form_router`` импортируются только под
``TYPE_CHECKING`` или внутри методов (issue #140).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import struct
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from v8unpack_agent.form_context import FormContext
    from v8unpack_agent.form_router import RouteResult
    from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

__all__ = [
    "FormRagIndex",
    "RagBuildError",
    "RagError",
    "RagLoadError",
    "RagQueryError",
]

Embedder = Callable[[str], list[float]]
FormKey = tuple[str, str, str, str]

_SCHEMA_VERSION = 2
_META_NAME = "rag_meta.json"
_NPZ_NAME = "rag_index.npz"
_NPY_ENTRY = "vectors.npy"
_NPY_MAGIC = b"\x93NUMPY"
_NPY_VER = b"\x01\x00"  # NPY v1.0
_NPY_PREFIX_LEN = len(_NPY_MAGIC) + len(_NPY_VER) + 2  # +2 = uint16 header_len
_DTYPE_STR = "<f8"  # float64 little-endian
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # детерминированный timestamp

# Максимальный допустимый размер NPY до выделения памяти (100 МБ)
_MAX_NPY_BYTES = 100 * 1024 * 1024
# Архив — NPY плюс служебные заголовки ZIP.
_MAX_NPZ_BYTES = _MAX_NPY_BYTES + 64 * 1024
_MAX_META_BYTES = 64 * 1024 * 1024


class RagError(ValueError):
    """Базовая ошибка RAG-индекса форм."""


class RagBuildError(RagError):
    """Ошибка построения: ключ, эмбеддер или некорректный вектор."""


class RagQueryError(RagError):
    """Ошибка запроса: индекс не готов, неверный ``top_k`` или вектор."""


class RagLoadError(RagError):
    """Ошибка загрузки артефакта. Сообщение стабильное и без деталей."""


# ---------------------------------------------------------------------------
# Эмбеддер и валидация векторов
# ---------------------------------------------------------------------------

def _embed(
    embedder: Embedder,
    text: str,
    error: type[RagError],
    where: str,
) -> object:
    """Вызвать внешний эмбеддер; любой его сбой — типизированная ошибка.

    Текст исходного исключения не попадает в сообщение: эмбеддер может
    сообщать детали провайдера, которые не должны утекать в диагностику.
    """
    try:
        return embedder(text)
    except Exception:  # noqa: BLE001 - сбой внешнего эмбеддера → типизированная ошибка
        raise error(f"{where}: embedder failed") from None


def _checked_vector(
    raw: object,
    expected_dim: int | None,
    error: type[RagError],
    where: str,
) -> list[float]:
    """Проверить вектор и вернуть его как ``list[float]``.

    Отклоняются: не list/tuple, пустой вектор, нечисловые значения
    (включая ``bool``), NaN/inf, несовпадение размерности, нулевая норма.
    """
    if not isinstance(raw, (list, tuple)):
        raise error(f"{where}: embedder must return list[float]")
    if not raw:
        raise error(f"{where}: embedding vector is empty")
    vector: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise error(f"{where}: embedding vector has non-numeric value")
        number = float(value)
        if not math.isfinite(number):
            raise error(f"{where}: embedding vector has NaN or inf")
        vector.append(number)
    if expected_dim is not None and len(vector) != expected_dim:
        raise error(
            f"{where}: embedding dimension {len(vector)} != index dimension "
            f"{expected_dim}"
        )
    if _norm(vector) == 0.0:
        raise error(f"{where}: embedding vector has zero norm")
    return vector


def _norm(vector: list[float]) -> float:
    return math.sqrt(math.fsum(x * x for x in vector))


# ---------------------------------------------------------------------------
# NPY v1.0 writer / reader — без numpy в runtime
# ---------------------------------------------------------------------------

def _npy_header(n_rows: int, n_cols: int) -> bytes:
    """Сформировать NPY v1.0 заголовок для 2D float64 C-order массива."""
    descr = (
        f"{{'descr': '{_DTYPE_STR}', 'fortran_order': False, "
        f"'shape': ({n_rows}, {n_cols}), }}"
    )
    raw = descr.encode("latin-1") + b"\n"
    pad = (-len(raw) - _NPY_PREFIX_LEN) % 64
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


_DESCR_RE = re.compile(r"""['"]descr['"]\s*:\s*['"]([^'"]+)['"]""")
_FORTRAN_RE = re.compile(r"""['"]fortran_order['"]\s*:\s*(True|False)""")
_SHAPE_RE = re.compile(r"""['"]shape['"]\s*:\s*\(([^)]*)\)""")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _parse_npy_header(hdr_bytes: bytes) -> tuple[str, bool, tuple[int, ...]]:
    """Разобрать NPY-заголовок: ``(descr, fortran_order, shape)``."""
    text = hdr_bytes.decode("latin-1").strip()

    descr = _DESCR_RE.search(text)
    fortran = _FORTRAN_RE.search(text)
    shape = _SHAPE_RE.search(text)
    if descr is None or fortran is None or shape is None:
        raise RagLoadError("NPY header is malformed")

    parts = [p.strip() for p in shape.group(1).split(",") if p.strip()]
    if not all(p.isdigit() for p in parts):
        raise RagLoadError("NPY header shape is malformed")
    return (
        descr.group(1),
        fortran.group(1) == "True",
        tuple(int(p) for p in parts),
    )


def _decode_matrix(data: bytes) -> list[list[float]]:
    """Декодировать NPY v1.0 bytes → 2D list[float]. Fail-closed."""
    if len(data) > _MAX_NPY_BYTES:
        raise RagLoadError("NPY entry size exceeds limit")
    if not data.startswith(_NPY_MAGIC + _NPY_VER) or len(data) < _NPY_PREFIX_LEN:
        raise RagLoadError("NPY magic/version mismatch; expected v1.0")

    hlen = int.from_bytes(data[8:10], "little")
    if _NPY_PREFIX_LEN + hlen > len(data):
        raise RagLoadError("NPY header is malformed")
    descr, fortran_order, shape = _parse_npy_header(
        data[_NPY_PREFIX_LEN:_NPY_PREFIX_LEN + hlen]
    )

    if descr != _DTYPE_STR:
        raise RagLoadError("NPY dtype is not <f8")
    if fortran_order:
        raise RagLoadError("NPY fortran_order is not supported")
    if len(shape) != 2:
        raise RagLoadError("NPY shape must be 2D")

    n_rows, n_cols = shape
    body = data[_NPY_PREFIX_LEN + hlen:]
    if len(body) != n_rows * n_cols * 8:
        raise RagLoadError("NPY body size does not match shape")
    if n_rows == 0 or n_cols == 0:
        if n_rows != n_cols:
            raise RagLoadError("NPY shape must be 2D")
        return []
    flat = struct.unpack(f"<{n_rows * n_cols}d", body)
    return [list(flat[r * n_cols:(r + 1) * n_cols]) for r in range(n_rows)]


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, обрезанная в [0, 1].

    Размерности обязаны совпадать: молчаливого обрезания через ``zip()`` нет.
    Нулевая норма на входе исключена валидацией, здесь возвращается 0.
    """
    if len(a) != len(b):
        raise RagQueryError("vector dimensions do not match")
    na = _norm(a)
    nb = _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    return max(0.0, min(1.0, dot / (na * nb)))


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _entry_key(entry: FormEntry) -> FormKey:
    return (
        entry.object_type,
        entry.object_name,
        entry.container_name,
        entry.form_name,
    )


def _context_key(ctx: FormContext) -> FormKey:
    return (
        ctx.object_type,
        ctx.object_name,
        ctx.container_name,
        ctx.form_name,
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _write_temp(index_dir: Path, name: str, data: bytes) -> Path:
    """Записать ``data`` во временный файл в ``index_dir`` и вернуть путь."""
    fd, tmp_name = tempfile.mkstemp(dir=index_dir, prefix=f".{name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return tmp


def _read_limited(path: Path, limit: int, what: str) -> bytes:
    try:
        if path.stat().st_size > limit:
            raise RagLoadError(f"{what} size exceeds limit")
        return path.read_bytes()
    except OSError:
        raise RagLoadError(f"{what} is missing or unreadable") from None


def _meta_int(meta: dict[str, Any], name: str) -> int:
    value = meta.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RagLoadError(f"rag_meta.json field {name} is invalid")
    return value


def _read_npz(npz_bytes: bytes) -> list[list[float]]:
    try:
        with zipfile.ZipFile(io.BytesIO(npz_bytes)) as zf:
            infos = zf.infolist()
            if [i.filename for i in infos] != [_NPY_ENTRY]:
                raise RagLoadError("rag_index.npz has unexpected entries")
            if infos[0].file_size > _MAX_NPY_BYTES:
                raise RagLoadError("NPY entry size exceeds limit")
            npy_bytes = zf.read(_NPY_ENTRY)
    except RagLoadError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        OSError,
        EOFError,
        NotImplementedError,
        RuntimeError,
        ValueError,
    ):
        raise RagLoadError("rag_index.npz is not a valid archive") from None
    return _decode_matrix(npy_bytes)


# ---------------------------------------------------------------------------
# FormRagIndex
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Record:
    key: FormKey
    vector: list[float]


class FormRagIndex:
    """RAG-индекс поверх :class:`~v8unpack_agent.scan_forms.FormScanIndex`.

    Parameters
    ----------
    scan_index:
        Канонический источник :class:`~v8unpack_agent.scan_forms.FormEntry`.
        ``query()`` возвращает записи из ``scan_index`` по ключу формы.

    Notes
    -----
    Индекс готов к ``query()`` и ``save()`` только после успешного
    ``build()`` или ``load()``. Неудачный ``build()`` не меняет прежнее
    состояние; неудачный ``load()`` переводит индекс в неготовое состояние.
    """

    def __init__(self, scan_index: FormScanIndex) -> None:
        lookup: dict[FormKey, FormEntry] = {}
        for entry in scan_index.forms:
            k = _entry_key(entry)
            if k in lookup:
                raise RagBuildError("duplicate form key in scan_index")
            lookup[k] = entry
        self._scan_index = scan_index
        self._lookup = lookup
        self._records: list[_Record] = []
        self._dim = 0
        self._embedder: Embedder | None = None

    def _is_ready(self) -> bool:
        return self._embedder is not None

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        contexts: list[FormContext],
        embedder: Embedder,
        *,
        max_chars: int = -1,
        max_tokens: int | None = None,
        count_tokens: Callable[[str], int] | None = None,
    ) -> None:
        """Вычислить векторы для контекстов и запомнить ``embedder``.

        Текст для эмбеддинга — только
        :func:`~v8unpack_agent.form_context.to_llm_prompt_fragment`
        с переданными как есть ``max_chars``/``max_tokens``/``count_tokens``
        (#125). Санитизация #142 и строки отрицательного знания #141
        остаются внутри этого фрагмента.

        Raises
        ------
        RagBuildError
            Ключ контекста отсутствует в ``scan_index`` или повторяется,
            ``embedder`` не callable или упал, вектор некорректен
            (пустой, нечисловой, NaN/inf, нулевая норма, другая размерность).
        """
        from v8unpack_agent.form_context import to_llm_prompt_fragment

        if not callable(embedder):
            raise RagBuildError("embedder must be callable")

        seen: set[FormKey] = set()
        records: list[_Record] = []
        dim: int | None = None

        for position, ctx in enumerate(contexts):
            where = f"context #{position}"
            k = _context_key(ctx)
            if k not in self._lookup:
                raise RagBuildError(f"{where}: form key not found in scan_index")
            if k in seen:
                raise RagBuildError(f"{where}: duplicate form key in build()")
            seen.add(k)

            text = to_llm_prompt_fragment(
                ctx,
                max_chars,
                max_tokens=max_tokens,
                count_tokens=count_tokens,
            )
            raw = _embed(embedder, text, RagBuildError, where)
            vector = _checked_vector(raw, dim, RagBuildError, where)
            dim = len(vector)
            records.append(_Record(key=k, vector=vector))

        self._records = records
        self._dim = dim if dim is not None else 0
        self._embedder = embedder

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, text: str, top_k: int = 5) -> list[RouteResult]:
        """Вернуть до ``top_k`` результатов по cosine similarity.

        Returns
        -------
        list[RouteResult]
            Один результат на форму: ``matched=[entry]``,
            ``confidence`` = cosine ∈ [0, 1]. Сортировка: убывание
            ``confidence``, tie-break по ключу формы.

        Raises
        ------
        RagQueryError
            Индекс не готов, ``text`` не строка, ``top_k`` не целое
            положительное, эмбеддер упал или вернул некорректный вектор
            либо вектор другой размерности.
        """
        from v8unpack_agent.form_router import RouteResult

        embedder = self._embedder
        if embedder is None:
            raise RagQueryError("index is not ready: call build() or load() first")
        if not isinstance(text, str):
            raise RagQueryError("query text must be str")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise RagQueryError("top_k must be a positive int")

        raw = _embed(embedder, text, RagQueryError, "query")
        expected = self._dim if self._records else None
        vector = _checked_vector(raw, expected, RagQueryError, "query")

        scored = sorted(
            ((_cosine(vector, rec.vector), rec.key) for rec in self._records),
            key=lambda t: (-t[0], t[1]),
        )
        return [
            RouteResult(matched=[self._lookup[key]], confidence=round(score, 6))
            for score, key in scored[:top_k]
        ]

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def save(self, index_dir: Path) -> None:
        """Атомарно сохранить ``rag_index.npz`` и ``rag_meta.json``.

        Оба файла сначала пишутся во временные файлы в ``index_dir``, затем
        переносятся через :func:`os.replace`: сначала архив, затем meta.
        ``vectors_sha256`` в meta связывает пару, поэтому сбой между двумя
        переносами оставляет пару, которую ``load()`` отвергает.

        Raises
        ------
        RagBuildError
            Индекс не готов.
        OSError
            Ошибка файловой системы; временные файлы удаляются.
        """
        if not self._is_ready():
            raise RagBuildError("index is not ready: call build() or load() first")
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zf:
            zi = zipfile.ZipInfo(_NPY_ENTRY, date_time=_ZIP_DATE)
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, _encode_matrix([r.vector for r in self._records]))
        npz_bytes = buffer.getvalue()

        meta = {
            "schema_version": _SCHEMA_VERSION,
            "count": len(self._records),
            "dimension": self._dim,
            "vectors_sha256": hashlib.sha256(npz_bytes).hexdigest(),
            "keys": [list(r.key) for r in self._records],
        }
        meta_bytes = (
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

        temps: list[Path] = []
        try:
            npz_tmp = _write_temp(index_dir, _NPZ_NAME, npz_bytes)
            temps.append(npz_tmp)
            meta_tmp = _write_temp(index_dir, _META_NAME, meta_bytes)
            temps.append(meta_tmp)
            os.replace(npz_tmp, index_dir / _NPZ_NAME)
            os.replace(meta_tmp, index_dir / _META_NAME)
        finally:
            for tmp in temps:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)

    def load(self, index_dir: Path, embedder: Embedder) -> None:
        """Загрузить артефакты из ``index_dir`` и запомнить ``embedder``.

        Эмбеддер не сохраняется на диск, поэтому передаётся явно. Его
        размерность проверяется при ``query()``.

        Raises
        ------
        RagLoadError
            Любая проблема артефакта: файла нет, битый JSON или ZIP,
            неизвестная schema, несовпадение count/dimension/keys/sha256,
            некорректные значения векторов. Сообщение стабильное: без путей
            и без текста исходных исключений. После ошибки индекс не готов.
        """
        self._records = []
        self._dim = 0
        self._embedder = None

        if not callable(embedder):
            raise RagLoadError("embedder must be callable")
        index_dir = Path(index_dir)
        keys, dimension, digest = self._read_meta(index_dir / _META_NAME)
        npz_bytes = _read_limited(index_dir / _NPZ_NAME, _MAX_NPZ_BYTES, _NPZ_NAME)
        if hashlib.sha256(npz_bytes).hexdigest() != digest:
            raise RagLoadError("rag_index.npz does not match rag_meta.json")
        matrix = _read_npz(npz_bytes)

        if len(matrix) != len(keys):
            raise RagLoadError("NPY row count does not match rag_meta.json count")
        records: list[_Record] = []
        for position, (k, row) in enumerate(zip(keys, matrix, strict=True)):
            if len(row) != dimension:
                raise RagLoadError("NPY dimension does not match rag_meta.json")
            vector = _checked_vector(
                row, dimension, RagLoadError, f"NPY row #{position}"
            )
            records.append(_Record(key=k, vector=vector))

        self._records = records
        self._dim = dimension
        self._embedder = embedder

    def _read_meta(self, meta_path: Path) -> tuple[list[FormKey], int, str]:
        raw = _read_limited(meta_path, _MAX_META_BYTES, _META_NAME)
        try:
            meta = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RagLoadError("rag_meta.json is not valid JSON") from None
        if not isinstance(meta, dict):
            raise RagLoadError("rag_meta.json has invalid structure")
        schema = meta.get("schema_version")
        if isinstance(schema, bool) or schema != _SCHEMA_VERSION:
            raise RagLoadError("unsupported rag_meta.json schema_version")

        count = _meta_int(meta, "count")
        dimension = _meta_int(meta, "dimension")
        if (count == 0) != (dimension == 0):
            raise RagLoadError("rag_meta.json count and dimension are inconsistent")
        digest = meta.get("vectors_sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise RagLoadError("rag_meta.json field vectors_sha256 is invalid")

        raw_keys = meta.get("keys")
        if not isinstance(raw_keys, list):
            raise RagLoadError("rag_meta.json field keys is invalid")
        if len(raw_keys) != count:
            raise RagLoadError("rag_meta.json count does not match keys")
        keys: list[FormKey] = []
        for raw_k in raw_keys:
            if (
                not isinstance(raw_k, list)
                or len(raw_k) != 4
                or not all(isinstance(part, str) for part in raw_k)
            ):
                raise RagLoadError("rag_meta.json field keys is invalid")
            k: FormKey = (raw_k[0], raw_k[1], raw_k[2], raw_k[3])
            if k not in self._lookup:
                raise RagLoadError("rag_meta.json key not found in scan_index")
            keys.append(k)
        if len(set(keys)) != len(keys):
            raise RagLoadError("rag_meta.json has duplicate keys")
        return keys, dimension, digest
