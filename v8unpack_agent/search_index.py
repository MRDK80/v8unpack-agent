"""Общий смысловой индекс с версионированным снимком (#325).

Провайдер-независимый индекс над FragmentationResult (#324). Эмбеддер
передаётся явно; сеть и пересборка не выполняются. Исходный текст в снимке
не хранится: только document_id, координаты, три хэша и векторы.
Контракты search_contract/1, search_corpus/1, search_fragments/1 не меняются.
Выдача query_index содержит не более одного попадания на артефакт (#362).
Повторные запросы не пересчитывают сигнатуру свежести и, при установленном
numpy, ранжируют подготовленной матрицей с тем же результатом (#406).
Формат хранения ``search_index/2`` (#413): метаданные JSON и двоичный файл
векторов рядом; модель индекса в памяти та же, что у ``search_index/1``.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import sys
import tempfile
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from v8unpack_agent.search_contract import (
    ArtifactRef,
    SearchHit,
    SearchQuery,
    SearchResult,
    TextSpan,
)
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_fragments import FragmentationResult

__all__ = [
    "RRF_K",
    "SEARCH_INDEX_SCHEMA",
    "SEARCH_INDEX_SCHEMA_V2",
    "SNAPSHOT_SCHEMAS",
    "Compatibility",
    "Embedder",
    "EmbedderIdentity",
    "Freshness",
    "IndexEntry",
    "IndexStatus",
    "SearchIndex",
    "SearchIndexError",
    "build_index",
    "load_index",
    "object_level",
    "query_index",
    "query_two_level",
    "save_index",
]
SEARCH_INDEX_SCHEMA = "search_index/1"
# Формат хранения #413; в памяти индекс по-прежнему имеет схему search_index/1.
SEARCH_INDEX_SCHEMA_V2 = "search_index/2"
SNAPSHOT_SCHEMAS = (SEARCH_INDEX_SCHEMA, SEARCH_INDEX_SCHEMA_V2)
_V2_KEYS = frozenset({
    "schema", "corpus_schema", "representation_version", "embedder",
    "artifacts", "entries", "vectors", "checksum",
})
_V2_ENTRY_KEYS = frozenset({
    "document_id", "artifact", "procedure", "span", "text_sha256",
    "search_sha256", "source_sha256",
})
_V2_VECTOR_KEYS = frozenset({"file", "dtype", "byteorder", "shape", "sha256"})
_V2_DTYPES = {"float32": ("f", 4), "float64": ("d", 8)}
# Запас отбора кандидатов матрицей float32: заведомо больше её погрешности
# косинуса (~1e-6); окончательные оценки считаются прежним путём (#406).
_CANDIDATE_MARGIN = 1e-4


def _load_numpy() -> Any:
    """numpy — опциональная зависимость (extra ``fast``); без неё прежний путь."""
    try:
        return importlib.import_module("numpy")
    except ImportError:
        return None


_np: Any = _load_numpy()
Compatibility = Literal["compatible", "incompatible"]
Freshness = Literal["fresh", "stale", "unverified"]


class SearchIndexError(Exception):
    """Безопасная ошибка индекса: ``code`` фиксирован, текст не раскрывает данные."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EmbedderIdentity:
    """Идентичность эмбеддера; config_hash непрозрачен и задаётся вызывающей стороной."""

    name: str
    version: str
    dimension: int
    config_hash: str

    def __post_init__(self) -> None:
        for field_name in ("name", "version", "config_hash"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if isinstance(self.dimension, bool) or not isinstance(self.dimension, int):
            raise TypeError("dimension must be int")
        if self.dimension < 1:
            raise ValueError("dimension must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "dimension": self.dimension,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, data: object) -> EmbedderIdentity:
        if not isinstance(data, dict) or set(data) != {
            "name", "version", "dimension", "config_hash"
        }:
            raise SearchIndexError("corrupt_snapshot", "invalid embedder identity")
        try:
            return cls(**data)
        except (TypeError, ValueError):
            raise SearchIndexError("corrupt_snapshot", "invalid embedder identity") from None


class Embedder(Protocol):
    """Явно инъектируемый эмбеддер."""

    @property
    def identity(self) -> EmbedderIdentity: ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


def _valid_vector(vector: object, dimension: int) -> tuple[float, ...] | None:
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
        return None
    if len(vector) != dimension:
        return None
    out: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        out.append(number)
    if not any(out):
        return None
    return tuple(out)


@dataclass(frozen=True)
class IndexEntry:
    """Запись снимка без исходного текста."""

    document_id: str
    artifact: ArtifactRef
    procedure: str | None
    span: TextSpan | None
    text_sha256: str
    search_sha256: str
    source_sha256: str
    vector: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "artifact": self.artifact.to_dict(),
            "procedure": self.procedure,
            "span": None if self.span is None else self.span.to_dict(),
            "text_sha256": self.text_sha256,
            "search_sha256": self.search_sha256,
            "source_sha256": self.source_sha256,
            "vector": list(self.vector),
        }


_ENTRY_KEYS = frozenset({
    "document_id", "artifact", "procedure", "span", "text_sha256",
    "search_sha256", "source_sha256", "vector",
})


def _entry_from_dict(data: object, dimension: int) -> IndexEntry:
    try:
        if not isinstance(data, dict) or set(data) != _ENTRY_KEYS:
            raise ValueError("keys")
        for key in ("document_id", "text_sha256", "search_sha256", "source_sha256"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(key)
        procedure = data["procedure"]
        if procedure is not None and not isinstance(procedure, str):
            raise ValueError("procedure")
        vector = _valid_vector(data["vector"], dimension)
        if vector is None:
            raise ValueError("vector")
        span = None if data["span"] is None else TextSpan.from_dict(data["span"])
        return IndexEntry(
            data["document_id"], ArtifactRef.from_dict(data["artifact"]),
            procedure, span, data["text_sha256"], data["search_sha256"],
            data["source_sha256"], vector,
        )
    except (ValueError, TypeError, KeyError):
        raise SearchIndexError("corrupt_snapshot", "invalid snapshot entry") from None


@dataclass(frozen=True)
class IndexStatus:
    """Две независимые оси: совместимость и свежесть."""

    compatibility: Compatibility
    freshness: Freshness
    reasons: tuple[str, ...] = ()

    @property
    def queryable(self) -> bool:
        return self.compatibility == "compatible" and self.freshness != "stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatibility": self.compatibility,
            "freshness": self.freshness,
            "reasons": list(self.reasons),
        }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _signature(result: FragmentationResult) -> dict[str, tuple[str, str, str]]:
    """Сигнатура свежести; строится один раз на результат фрагментации (#406)."""
    return result.derived("search_index_signature", lambda: _compute_signature(result))


def _compute_signature(result: FragmentationResult) -> dict[str, tuple[str, str, str]]:
    sources = {i.artifact.identity: i.source_sha256 for i in result.items}
    return {
        f.document.document_id: (
            f.document.text_sha256,
            _hash_text(f.search_text),
            sources.get(f.document.artifact.identity, ""),
        )
        for f in result.fragments
    }


@dataclass(frozen=True)
class SearchIndex:
    """Неизменяемый индекс: запись на фрагмент, версии и идентичность эмбеддера."""

    corpus_schema: str
    representation_version: str
    embedder: EmbedderIdentity
    entries: tuple[IndexEntry, ...]
    schema: str = SEARCH_INDEX_SCHEMA
    # Производные значения (#406): не входят в равенство, repr и снимок.
    _cache: dict[str, Any] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        ids = [e.document_id for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate document_id in index")

    def assess(
        self,
        embedder: EmbedderIdentity,
        fragmentation: FragmentationResult | None = None,
    ) -> IndexStatus:
        """Совместимость и свежесть; без fragmentation свежесть — unverified.

        Индекс и результат фрагментации неизменяемы, поэтому итог для той же
        идентичности эмбеддера и того же объекта фрагментации переиспользуется
        (#406).
        """
        cached = self._cache.get("assess")
        if cached is not None and cached[0] == embedder and cached[1] is fragmentation:
            return cached[2]  # type: ignore[no-any-return]
        status = self._assess(embedder, fragmentation)
        self._cache["assess"] = (embedder, fragmentation, status)
        return status

    def _stored_signature(self) -> dict[str, tuple[str, str, str]]:
        stored = self._cache.get("signature")
        if stored is None:
            stored = {
                e.document_id: (e.text_sha256, e.search_sha256, e.source_sha256)
                for e in self.entries
            }
            self._cache["signature"] = stored
        return stored  # type: ignore[no-any-return]

    def _assess(
        self,
        embedder: EmbedderIdentity,
        fragmentation: FragmentationResult | None,
    ) -> IndexStatus:
        reasons: list[str] = []
        if self.schema != SEARCH_INDEX_SCHEMA:
            reasons.append("index_schema")
        if self.corpus_schema != SEARCH_CORPUS_SCHEMA:
            reasons.append("corpus_schema")
        if self.embedder != embedder:
            reasons.append("embedder")
        if fragmentation is not None and (
            fragmentation.representation_version != self.representation_version
        ):
            reasons.append("representation_version")
        compatibility: Compatibility = "incompatible" if reasons else "compatible"
        if fragmentation is None:
            return IndexStatus(compatibility, "unverified", tuple(reasons))
        current = _signature(fragmentation)
        stored = self._stored_signature()
        freshness: Freshness = "fresh" if current == stored else "stale"
        if freshness == "stale":
            reasons.append("corpus_changed")
        return IndexStatus(compatibility, freshness, tuple(reasons))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "corpus_schema": self.corpus_schema,
            "representation_version": self.representation_version,
            "embedder": self.embedder.to_dict(),
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self) -> str:
        body = self._body()
        body["checksum"] = _hash_text(_dump(body))
        return _dump(body)


def build_index(result: FragmentationResult, embedder: Embedder) -> SearchIndex:
    """Полная пересборка по явно переданному эмбеддеру."""
    identity = embedder.identity
    fragments = result.fragments
    try:
        raw = embedder.embed([f.search_text for f in fragments]) if fragments else []
    except Exception:  # noqa: BLE001 - сторонний эмбеддер, детали не раскрываем
        raise SearchIndexError("embedder_failed", "embedder failed") from None
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != len(fragments):
        raise SearchIndexError("invalid_vector", "embedder returned wrong vector count")
    sources = {i.artifact.identity: i.source_sha256 for i in result.items}
    entries: list[IndexEntry] = []
    for fragment, item in zip(fragments, raw, strict=True):
        vector = _valid_vector(item, identity.dimension)
        if vector is None:
            raise SearchIndexError("invalid_vector", "embedder returned invalid vector")
        doc = fragment.document
        entries.append(IndexEntry(
            doc.document_id, doc.artifact, doc.procedure, doc.span,
            doc.text_sha256, _hash_text(fragment.search_text),
            sources.get(doc.artifact.identity, ""), vector,
        ))
    return SearchIndex(SEARCH_CORPUS_SCHEMA, result.representation_version,
                       identity, tuple(entries))


def _write_atomic(target: Path, data: bytes) -> None:
    """Временный файл рядом, fsync и одна замена ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp",
                                    dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _vector_files(target: Path) -> list[Path]:
    """Файлы векторов ``search_index/2`` этого пути снимка."""
    pattern = re.compile(re.escape(target.name) + r"\.[0-9a-f]{16}\.vec")
    try:
        names = os.listdir(target.parent)
    except OSError:
        return []
    return [target.parent / n for n in names if pattern.fullmatch(n)]


def _remove_stale_vectors(target: Path, keep: str | None) -> None:
    for stale in _vector_files(target):
        if stale.name != keep:
            try:
                stale.unlink()
            except OSError:
                pass


def save_index(
    index: SearchIndex,
    path: str | os.PathLike[str],
    *,
    schema: str = SEARCH_INDEX_SCHEMA,
) -> str:
    """Атомарная публикация снимка; возвращает схему записанного снимка.

    ``search_index/1`` — один JSON-файл. ``search_index/2`` (#413) — файл
    векторов ``<имя>.<sha256[:16]>.vec`` рядом и метаданные по ``path``:
    сначала публикуются векторы, затем одной заменой метаданные, затем
    удаляются прежние файлы векторов этого пути. При сбое на любом шаге
    прежний снимок остаётся читаемым.
    """
    target = Path(path)
    if schema == SEARCH_INDEX_SCHEMA:
        _write_atomic(target, index.to_json().encode("utf-8"))
        _remove_stale_vectors(target, None)
        return schema
    if schema != SEARCH_INDEX_SCHEMA_V2:
        raise ValueError("unknown snapshot schema")
    dtype, payload = _encode_vectors(index)
    digest = hashlib.sha256(payload).hexdigest()
    vector_name = f"{target.name}.{digest[:16]}.vec"
    vector_path = target.parent / vector_name
    existed = vector_path.exists()
    _write_atomic(vector_path, payload)
    body = _v2_body(index, vector_name, dtype, digest)
    body["checksum"] = _hash_text(_dump(body))
    try:
        _write_atomic(target, _dump(body).encode("utf-8"))
    except BaseException:
        if not existed:
            try:
                vector_path.unlink()
            except OSError:
                pass
        raise
    _remove_stale_vectors(target, vector_name)
    return schema


def _encode_vectors(index: SearchIndex) -> tuple[str, bytes]:
    """Векторы без потерь: float32, если каждое значение точно представимо."""
    values = [v for e in index.entries for v in e.vector]
    np = _np
    if np is not None:
        wide = np.asarray(values, dtype=np.float64)
        narrow = wide.astype(np.float32)
        if np.array_equal(narrow.astype(np.float64), wide):
            return "float32", narrow.astype("<f4").tobytes()
        return "float64", wide.astype("<f8").tobytes()
    packed = array("f", values)
    if packed.tolist() == values:
        dtype = "float32"
    else:
        dtype, packed = "float64", array("d", values)
    if sys.byteorder != "little":
        packed.byteswap()
    return dtype, packed.tobytes()


def _v2_body(
    index: SearchIndex, vector_name: str, dtype: str, digest: str
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    numbers: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for entry in index.entries:
        artifact = entry.artifact.to_dict()
        key = _dump(artifact)
        if key not in numbers:
            numbers[key] = len(artifacts)
            artifacts.append(artifact)
        row = entry.to_dict()
        del row["vector"]
        row["artifact"] = numbers[key]
        rows.append(row)
    return {
        "schema": SEARCH_INDEX_SCHEMA_V2,
        "corpus_schema": index.corpus_schema,
        "representation_version": index.representation_version,
        "embedder": index.embedder.to_dict(),
        "artifacts": artifacts,
        "entries": rows,
        "vectors": {
            "file": vector_name,
            "dtype": dtype,
            "byteorder": "little",
            "shape": [len(index.entries), index.embedder.dimension],
            "sha256": digest,
        },
    }


def _corrupt(message: str) -> SearchIndexError:
    return SearchIndexError("corrupt_snapshot", message)


def load_index(path: str | os.PathLike[str]) -> SearchIndex:
    """Загрузка ``search_index/1`` или ``/2`` с проверкой сумм и каждого вектора."""
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise _corrupt("snapshot unreadable") from None
    if isinstance(data, dict) and data.get("schema") == SEARCH_INDEX_SCHEMA_V2:
        return _load_v2(Path(path), data)
    keys = {"schema", "corpus_schema", "representation_version",
            "embedder", "entries", "checksum"}
    if not isinstance(data, dict) or set(data) != keys:
        raise SearchIndexError("corrupt_snapshot", "invalid snapshot structure")
    checksum = data.pop("checksum")
    try:
        expected = _hash_text(_dump(data))
    except (TypeError, ValueError):
        raise SearchIndexError("corrupt_snapshot", "invalid snapshot structure") from None
    if checksum != expected:
        raise SearchIndexError("corrupt_snapshot", "checksum mismatch")
    for key in ("schema", "corpus_schema", "representation_version"):
        if not isinstance(data[key], str):
            raise SearchIndexError("corrupt_snapshot", "invalid snapshot structure")
    identity = EmbedderIdentity.from_dict(data["embedder"])
    if not isinstance(data["entries"], list):
        raise SearchIndexError("corrupt_snapshot", "invalid snapshot structure")
    entries = tuple(_entry_from_dict(e, identity.dimension) for e in data["entries"])
    try:
        return SearchIndex(data["corpus_schema"], data["representation_version"],
                           identity, entries, schema=data["schema"])
    except ValueError:
        raise SearchIndexError("corrupt_snapshot", "duplicate document_id") from None


def _load_v2(target: Path, data: dict[str, Any]) -> SearchIndex:
    if set(data) != _V2_KEYS:
        raise _corrupt("invalid snapshot structure")
    checksum = data.pop("checksum")
    try:
        expected = _hash_text(_dump(data))
    except (TypeError, ValueError):
        raise _corrupt("invalid snapshot structure") from None
    if checksum != expected:
        raise _corrupt("checksum mismatch")
    for key in ("corpus_schema", "representation_version"):
        if not isinstance(data[key], str):
            raise _corrupt("invalid snapshot structure")
    identity = EmbedderIdentity.from_dict(data["embedder"])
    artifacts_raw, rows, info = data["artifacts"], data["entries"], data["vectors"]
    if not isinstance(artifacts_raw, list) or not isinstance(rows, list):
        raise _corrupt("invalid snapshot structure")
    try:
        artifacts = [ArtifactRef.from_dict(a) for a in artifacts_raw]
    except (TypeError, ValueError, KeyError):
        raise _corrupt("invalid artifact") from None
    n, dim = len(rows), identity.dimension
    dtype = _check_vector_info(info, target, n, dim)
    try:
        payload = (target.parent / info["file"]).read_bytes()
    except OSError:
        raise _corrupt("vector file unreadable") from None
    if hashlib.sha256(payload).hexdigest() != info["sha256"]:
        raise _corrupt("vector checksum mismatch")
    code, size = _V2_DTYPES[dtype]
    if len(payload) != n * dim * size:
        raise _corrupt("vector file size mismatch")
    vectors, matrix = _decode_vectors(payload, code, dtype, n, dim)
    entries: list[IndexEntry] = []
    for row, vector in zip(rows, vectors, strict=True):
        entries.append(_v2_entry(row, artifacts, vector))
    try:
        index = SearchIndex(data["corpus_schema"], data["representation_version"],
                            identity, tuple(entries))
    except ValueError:
        raise _corrupt("duplicate document_id") from None
    if matrix is not None:
        _prepared(index, matrix)
    return index


def _check_vector_info(info: object, target: Path, n: int, dim: int) -> str:
    if not isinstance(info, dict) or set(info) != _V2_VECTOR_KEYS:
        raise _corrupt("invalid vector description")
    name, dtype = info["file"], info["dtype"]
    pattern = re.escape(target.name) + r"\.[0-9a-f]{16}\.vec"
    if not isinstance(name, str) or not re.fullmatch(pattern, name):
        raise _corrupt("invalid vector file name")
    if not isinstance(info["sha256"], str) or not name.endswith(
        "." + info["sha256"][:16] + ".vec"
    ):
        raise _corrupt("invalid vector file name")
    if dtype not in _V2_DTYPES or info["byteorder"] != "little":
        raise _corrupt("invalid vector dtype")
    if info["shape"] != [n, dim]:
        raise _corrupt("vector shape mismatch")
    return str(dtype)


def _decode_vectors(
    payload: bytes, code: str, dtype: str, n: int, dim: int
) -> tuple[list[tuple[float, ...]], Any]:
    """Векторы как кортежи float и, при numpy, матрица float32 для #406."""
    np = _np
    if np is not None:
        wide = np.frombuffer(payload, dtype="<f4" if dtype == "float32" else "<f8")
        wide = wide.reshape(n, dim).astype(np.float64)
        if n and (not np.isfinite(wide).all() or not wide.any(axis=1).all()):
            raise _corrupt("invalid vector")
        vectors = [tuple(row) for row in wide.tolist()]
        return vectors, wide.astype(np.float32)
    values = array(code)
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    flat = values.tolist()
    vectors = []
    for i in range(n):
        vector = _valid_vector(flat[i * dim:(i + 1) * dim], dim)
        if vector is None:
            raise _corrupt("invalid vector")
        vectors.append(vector)
    return vectors, None


def _v2_entry(
    row: object, artifacts: Sequence[ArtifactRef], vector: tuple[float, ...]
) -> IndexEntry:
    try:
        if not isinstance(row, dict) or set(row) != _V2_ENTRY_KEYS:
            raise ValueError("keys")
        number = row["artifact"]
        if isinstance(number, bool) or not isinstance(number, int):
            raise TypeError("artifact")
        if not 0 <= number < len(artifacts):
            raise ValueError("artifact")
        for key in ("document_id", "text_sha256", "search_sha256", "source_sha256"):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(key)
        procedure = row["procedure"]
        if procedure is not None and not isinstance(procedure, str):
            raise ValueError("procedure")
        span = None if row["span"] is None else TextSpan.from_dict(row["span"])
        return IndexEntry(
            row["document_id"], artifacts[number], procedure, span,
            row["text_sha256"], row["search_sha256"], row["source_sha256"], vector,
        )
    except (ValueError, TypeError, KeyError):
        raise _corrupt("invalid snapshot entry") from None


def _prepared(
    index: SearchIndex, matrix: Any = None
) -> tuple[Any, Any, Any, tuple[ArtifactRef, ...]]:
    """Матрица float32, нормы строк, номер артефакта строки и артефакты (#406).

    ``matrix`` — готовая матрица float32 из снимка ``search_index/2`` (#413):
    те же значения, что дало бы приведение кортежей векторов.
    """
    prepared = index._cache.get("matrix")
    if prepared is None:
        np = _np
        if matrix is None:
            matrix = np.asarray([e.vector for e in index.entries], dtype=np.float32)
        matrix = matrix.reshape(len(index.entries), index.embedder.dimension)
        numbers: dict[str, int] = {}
        artifacts: list[ArtifactRef] = []
        rows: list[int] = []
        for entry in index.entries:
            key = entry.artifact.identity
            if key not in numbers:
                numbers[key] = len(artifacts)
                artifacts.append(entry.artifact)
            rows.append(numbers[key])
        norms = np.linalg.norm(matrix, axis=1)
        prepared = (matrix, norms, np.asarray(rows, dtype=np.intp), tuple(artifacts))
        index._cache["matrix"] = prepared
    return prepared  # type: ignore[no-any-return]


def _candidates(
    index: SearchIndex, query: SearchQuery, vector: tuple[float, ...]
) -> Sequence[IndexEntry]:
    """Записи, среди которых заведомо есть вся выдача прежнего алгоритма.

    Матрица float32 даёт приближённые оценки с погрешностью меньше
    ``_CANDIDATE_MARGIN``. Порог — оценка ``top_k``-го артефакта по лучшему
    фрагменту минус запас: каждый фрагмент, который прежний алгоритм может
    выбрать (включая равенства по ``document_id``), проходит порог. Оценки и
    порядок затем считаются прежним путём, поэтому результат совпадает
    побитово. Без numpy или для пустого индекса — все записи.
    """
    np = _np
    if np is None or not index.entries:
        return index.entries
    matrix, norms, rows, artifacts = _prepared(index)
    allowed = np.fromiter(
        (query.filters.matches(a) for a in artifacts), dtype=bool, count=len(artifacts)
    )
    if not allowed.any():
        return ()
    q = np.asarray(vector, dtype=np.float32)
    scores = (matrix @ q) / (norms * np.linalg.norm(q))
    scores = np.where(allowed[rows], scores, -np.inf)
    best = np.full(len(artifacts), -np.inf, dtype=scores.dtype)
    np.maximum.at(best, rows, scores)
    best = best[allowed]
    k = min(query.top_k, best.size)
    threshold = np.partition(best, best.size - k)[best.size - k]
    chosen = np.flatnonzero(scores >= threshold - _CANDIDATE_MARGIN)
    entries = index.entries
    return [entries[i] for i in chosen.tolist()]


RRF_K = 60


def object_level(fragmentation: FragmentationResult) -> FragmentationResult:
    """Уровень объектов (#407): первый фрагмент каждого артефакта.

    Строится один раз на результат фрагментации; ``items`` и версия
    представления прежние, поэтому совместимость и свежесть индекса уровня
    объектов проверяются тем же ``assess``.
    """
    def build() -> FragmentationResult:
        seen: set[str] = set()
        first = []
        for fragment in fragmentation.fragments:
            key = fragment.document.artifact.identity
            if key not in seen:
                seen.add(key)
                first.append(fragment)
        return FragmentationResult(
            fragmentation.parameters, tuple(first), fragmentation.items,
            fragmentation.corpus_report, fragmentation.owner_links,
        )
    return fragmentation.derived("search_index_object_level", build)


class _OnceEmbedder:
    """Один вызов ``embed`` запроса на оба уровня."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._raw: Any = None

    @property
    def identity(self) -> EmbedderIdentity:
        return self._embedder.identity

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if self._raw is None:
            self._raw = self._embedder.embed(texts)
        return self._raw  # type: ignore[no-any-return]


def query_two_level(
    fragment_index: SearchIndex,
    object_index: SearchIndex,
    query: SearchQuery,
    embedder: Embedder,
    fragmentation: FragmentationResult | None = None,
) -> SearchResult:
    """Слияние рангов артефактов двух уровней (#407, решение владельца 2B).

    Каждый уровень ранжируется ``query_index`` (до ``max(5·top_k, 50)``
    артефактов). Оценка артефакта — сумма ``1/(RRF_K + ранг)`` по уровням, где
    он найден; это ``similarity`` попадания (шкала слияния, не косинус).
    Попадание указывает лучший фрагмент уровня фрагментов, а если артефакт
    найден только на уровне объектов — его фрагмент. Порядок — по убыванию
    оценки, при равенстве — порядок контракта ``search_contract/1``.
    Несовместимый или устаревший индекс любого уровня блокирует выдачу.
    """
    if query.mode == "exact":
        return SearchResult.empty(query)
    depth = max(query.top_k * 5, 50)
    wide = SearchQuery(query.text, mode="semantic", top_k=depth, filters=query.filters)
    once = _OnceEmbedder(embedder)
    objects = None if fragmentation is None else object_level(fragmentation)
    levels = (
        query_index(fragment_index, wide, once, fragmentation),
        query_index(object_index, wide, once, objects),
    )
    for result in levels:
        if result.status == "error" and result.error is not None:
            return SearchResult.failed(query, result.error.code)
    scores: dict[str, float] = {}
    chosen: dict[str, SearchHit] = {}
    for result in levels:
        for rank, hit in enumerate(result.hits, start=1):
            key = hit.artifact.identity
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            chosen.setdefault(key, hit)
    ranked = sorted(scores, key=lambda key: (-scores[key], key))[: query.top_k]
    hits = [
        SearchHit(chosen[key].artifact, "semantic",
                  document_id=chosen[key].document_id, similarity=scores[key])
        for key in ranked
    ]
    return SearchResult.semantic(query, hits) if hits else SearchResult.empty(query)


def _rank(row: tuple[float, str, IndexEntry]) -> tuple[float, str]:
    """Ключ порядка: убывание оценки, при равенстве — ``document_id``."""
    return (-row[0], row[1])


def query_index(
    index: SearchIndex,
    query: SearchQuery,
    embedder: Embedder,
    fragmentation: FragmentationResult | None = None,
) -> SearchResult:
    """Запрос; несовместимый или устаревший индекс блокирует выдачу.

    Фильтры применяются к каждой записи; для каждого артефакта остаётся его
    лучший фрагмент (наибольшая оценка, при равенстве — меньший
    ``document_id``). Затем отбираются до ``top_k`` разных артефактов по
    убыванию оценки, при равенстве — по ``document_id`` лучшего фрагмента
    (#362). При установленном numpy кандидаты отбираются подготовленной
    матрицей, а оценки считаются тем же кодом; результат не меняется (#406).
    """
    status = index.assess(embedder.identity, fragmentation)
    if not status.queryable:
        return SearchResult.failed(query, "incompatible_index")
    if query.mode == "exact":
        return SearchResult.empty(query)
    try:
        raw = embedder.embed([query.text])
    except Exception:  # noqa: BLE001 - сторонний эмбеддер, детали не раскрываем
        return SearchResult.failed(query, "embedder_failed")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != 1:
        return SearchResult.failed(query, "invalid_vector")
    vector = _valid_vector(raw[0], index.embedder.dimension)
    if vector is None:
        return SearchResult.failed(query, "invalid_vector")
    norm = math.sqrt(sum(v * v for v in vector))
    best: dict[str, tuple[float, str, IndexEntry]] = {}
    for entry in _candidates(index, query, vector):
        if not query.filters.matches(entry.artifact):
            continue
        entry_norm = math.sqrt(sum(v * v for v in entry.vector))
        dot = sum(a * b for a, b in zip(vector, entry.vector, strict=True))
        row = (dot / (norm * entry_norm), entry.document_id, entry)
        key = entry.artifact.identity
        current = best.get(key)
        if current is None or _rank(row) < _rank(current):
            best[key] = row
    ranked = sorted(best.values(), key=_rank)
    hits = [
        SearchHit(e.artifact, "semantic", document_id=e.document_id, similarity=s)
        for s, _, e in ranked[: query.top_k]
    ]
    if not hits:
        return SearchResult.empty(query)
    return SearchResult.semantic(query, hits)
