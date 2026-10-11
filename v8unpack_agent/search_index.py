"""Общий смысловой индекс с версионированным снимком (#325).

Провайдер-независимый индекс над FragmentationResult (#324). Эмбеддер
передаётся явно; сеть и пересборка не выполняются. Исходный текст в снимке
не хранится: только document_id, координаты, три хэша и векторы.
Контракты search_contract/1, search_corpus/1, search_fragments/1 не меняются.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
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
    "SEARCH_INDEX_SCHEMA",
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
    "query_index",
    "save_index",
]
SEARCH_INDEX_SCHEMA = "search_index/1"
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

    def __post_init__(self) -> None:
        ids = [e.document_id for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate document_id in index")

    def assess(
        self,
        embedder: EmbedderIdentity,
        fragmentation: FragmentationResult | None = None,
    ) -> IndexStatus:
        """Совместимость и свежесть; без fragmentation свежесть — unverified."""
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
        stored = {
            e.document_id: (e.text_sha256, e.search_sha256, e.source_sha256)
            for e in self.entries
        }
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


def save_index(index: SearchIndex, path: str | os.PathLike[str]) -> None:
    """Атомарная публикация: временный файл рядом и одна замена."""
    target = Path(path)
    data = index.to_json().encode("utf-8")
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


def load_index(path: str | os.PathLike[str]) -> SearchIndex:
    """Загрузка с проверкой контрольной суммы и каждого вектора."""
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise SearchIndexError("corrupt_snapshot", "snapshot unreadable") from None
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


def query_index(
    index: SearchIndex,
    query: SearchQuery,
    embedder: Embedder,
    fragmentation: FragmentationResult | None = None,
) -> SearchResult:
    """Запрос; несовместимый или устаревший индекс блокирует выдачу."""
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
    scored: list[tuple[float, str, IndexEntry]] = []
    for entry in index.entries:
        if not query.filters.matches(entry.artifact):
            continue
        entry_norm = math.sqrt(sum(v * v for v in entry.vector))
        dot = sum(a * b for a, b in zip(vector, entry.vector, strict=True))
        scored.append((dot / (norm * entry_norm), entry.document_id, entry))
    scored.sort(key=lambda row: (-row[0], row[1]))
    hits = [
        SearchHit(e.artifact, "semantic", document_id=e.document_id, similarity=s)
        for s, _, e in scored[: query.top_k]
    ]
    if not hits:
        return SearchResult.empty(query)
    return SearchResult.semantic(query, hits)
