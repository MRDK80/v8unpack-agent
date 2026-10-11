"""Общий точный поиск и диспетчер смыслового fallback (#326).

Точный маршрут разрешает запрос по каталогу артефактов, который передаёт
вызывающая сторона, и не зависит от смыслового индекса. Смысловой маршрут
вызывает ``query_index`` (#325, #362) без собственного отбора по артефакту.
Эмбеддер вызывается только смысловым маршрутом; сети, загрузки модели и
пересборки индекса нет. Контракты ``search_contract/1``, ``search_corpus/1``,
``search_fragments/1`` и формат снимка не меняются; см. ``docs/search_dispatch.md``.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from v8unpack_agent.search_contract import (
    ArtifactRef,
    SearchDocumentSet,
    SearchHit,
    SearchQuery,
    SearchResult,
    identity_key,
)
from v8unpack_agent.search_corpus import SearchCorpus
from v8unpack_agent.search_fragments import FragmentationResult
from v8unpack_agent.search_index import (
    Embedder,
    IndexStatus,
    SearchIndex,
    query_index,
)

__all__ = [
    "DISPATCH_PATHS",
    "DispatchResult",
    "DispatchStep",
    "ExactCatalog",
    "ExactResolution",
    "SearchDispatcher",
]

DispatchStep = Literal["exact", "semantic"]
DISPATCH_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {("exact",), ("exact", "semantic"), ("semantic",)}
)


@dataclass(frozen=True)
class ExactResolution:
    """Итог точного разрешения: попадания после фильтров и число отсеянных."""

    hits: tuple[SearchHit, ...]
    filtered_out: int


@dataclass(frozen=True)
class ExactCatalog:
    """Неизменяемый каталог артефактов для точного разрешения.

    Повтор одного артефакта (по ``identity``) схлопывается; разные описания
    одного ``artifact_id`` отклоняются. Порядок не зависит от входа.
    """

    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        unique: dict[str, ArtifactRef] = {}
        for artifact in tuple(self.artifacts):
            if not isinstance(artifact, ArtifactRef):
                raise TypeError("ExactCatalog accepts only ArtifactRef")
            known = unique.setdefault(artifact.identity, artifact)
            if known != artifact:
                raise ValueError(
                    f"conflicting artifact description: {artifact.artifact_id!r}"
                )
        ordered = tuple(sorted(unique.values(), key=ArtifactRef.sort_key))
        object.__setattr__(self, "artifacts", ordered)

    @classmethod
    def from_artifacts(cls, artifacts: Iterable[ArtifactRef]) -> ExactCatalog:
        """Каталог из произвольной последовательности артефактов."""
        return cls(artifacts=tuple(artifacts))

    @classmethod
    def from_documents(cls, documents: SearchDocumentSet) -> ExactCatalog:
        """Артефакты набора документов."""
        if not isinstance(documents, SearchDocumentSet):
            raise TypeError("documents must be SearchDocumentSet")
        return cls(artifacts=documents.artifacts())

    @classmethod
    def from_corpus(cls, corpus: SearchCorpus) -> ExactCatalog:
        """Артефакты документов корпуса #323."""
        if not isinstance(corpus, SearchCorpus):
            raise TypeError("corpus must be SearchCorpus")
        return cls.from_documents(corpus.documents)

    @classmethod
    def from_fragmentation(cls, result: FragmentationResult) -> ExactCatalog:
        """Артефакты фрагментов #324."""
        if not isinstance(result, FragmentationResult):
            raise TypeError("result must be FragmentationResult")
        return cls.from_documents(result.documents)

    def __len__(self) -> int:
        return len(self.artifacts)

    def resolve(self, query: SearchQuery) -> ExactResolution:
        """Точное разрешение запроса без эмбеддера.

        Запрос целиком (после ``strip``) сравнивается по ``identity_key``.
        Сначала ``artifact_id``: совпадение даёт ``exact_id``, имена не
        проверяются. Иначе ``form_name`` формы, затем ``owner_name`` модуля или
        формы с установленным владельцем (``exact_name``). Карточка владельца
        находится только по ``artifact_id``. Подстрока и токены совпадением не
        считаются. Фильтры запроса применяются к найденным кандидатам.
        """
        if not isinstance(query, SearchQuery):
            raise TypeError("query must be SearchQuery")
        key = identity_key(query.text.strip())
        candidates = [
            SearchHit(artifact, "exact_id", matched_field="artifact_id")
            for artifact in self.artifacts
            if artifact.identity == key
        ]
        if not candidates:
            candidates = [
                hit for hit in (_name_hit(a, key) for a in self.artifacts) if hit
            ]
        passed = tuple(h for h in candidates if query.filters.matches(h.artifact))
        return ExactResolution(passed, len(candidates) - len(passed))


def _name_hit(artifact: ArtifactRef, key: str) -> SearchHit | None:
    if artifact.artifact_kind == "owner":
        return None
    form_key = artifact.form_key
    if form_key is not None and identity_key(form_key[3]) == key:
        return SearchHit(artifact, "exact_name", matched_field="form_name")
    owner = artifact.owner
    if owner is not None and owner.owner_name is not None and (
        identity_key(owner.owner_name) == key
    ):
        return SearchHit(artifact, "exact_name", matched_field="owner_name")
    return None


@dataclass(frozen=True)
class DispatchResult:
    """Результат диспетчера: ``SearchResult`` и сведения о маршруте.

    ``path`` — выполненные шаги. ``index_status`` задан, если смысловой шаг
    дошёл до индекса; ``None`` — индекс не использовался или не передан.
    ``exact_filtered_out`` — точные кандидаты, отсеянные фильтрами запроса.
    """

    result: SearchResult
    path: tuple[DispatchStep, ...]
    index_status: IndexStatus | None = None
    exact_filtered_out: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.result, SearchResult):
            raise TypeError("result must be SearchResult")
        path = tuple(self.path)
        if path not in DISPATCH_PATHS:
            raise ValueError(f"unknown dispatch path: {path!r}")
        object.__setattr__(self, "path", path)
        if self.index_status is not None and not isinstance(
            self.index_status, IndexStatus
        ):
            raise TypeError("index_status must be IndexStatus or None")
        count = self.exact_filtered_out
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("exact_filtered_out must be an int >= 0")
        status = self.result.status
        if status in ("exact", "ambiguous") and path != ("exact",):
            raise ValueError("exact results come from the exact step only")
        if status == "semantic" and "semantic" not in path:
            raise ValueError("semantic results require the semantic step")
        if self.index_status is not None and "semantic" not in path:
            raise ValueError("index_status requires the semantic step")
        if path == ("semantic",) and count:
            raise ValueError("semantic-only path has no exact candidates")

    @property
    def semantic_used(self) -> bool:
        """Выполнялся ли смысловой шаг."""
        return "semantic" in self.path

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без исходного текста."""
        return {
            "result": self.result.to_dict(),
            "path": list(self.path),
            "index_status": (
                None if self.index_status is None else self.index_status.to_dict()
            ),
            "exact_filtered_out": self.exact_filtered_out,
        }


@dataclass(frozen=True)
class SearchDispatcher:
    """Единый маршрут поиска: точное разрешение, затем смысловой fallback.

    * ``auto`` — ``exact``/``ambiguous`` возвращаются без эмбеддера; при
      промахе (в том числе из-за фильтров) — ``query_index`` с тем же запросом.
    * ``exact`` — только каталог; индекс и эмбеддер не нужны.
    * ``semantic`` — только ``query_index``.

    Без индекса или эмбеддера смысловой шаг даёт ``error/index_not_ready``;
    ``stale``/``incompatible`` дают ``error/incompatible_index`` из
    ``query_index``. Входы и результаты не изменяются.
    """

    catalog: ExactCatalog
    index: SearchIndex | None = None
    embedder: Embedder | None = None
    fragmentation: FragmentationResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, ExactCatalog):
            raise TypeError("catalog must be ExactCatalog")
        if self.index is not None and not isinstance(self.index, SearchIndex):
            raise TypeError("index must be SearchIndex or None")
        if self.fragmentation is not None and not isinstance(
            self.fragmentation, FragmentationResult
        ):
            raise TypeError("fragmentation must be FragmentationResult or None")

    def search(self, query: SearchQuery) -> DispatchResult:
        """Выполнить запрос по режиму ``query.mode``."""
        if not isinstance(query, SearchQuery):
            raise TypeError("query must be SearchQuery")
        if query.mode == "semantic":
            return self._semantic(query, ("semantic",), 0)
        resolution = self.catalog.resolve(query)
        hits = resolution.hits
        if len(hits) == 1:
            result = SearchResult.exact(query, hits[0])
        elif hits:
            result = SearchResult.ambiguous(query, hits)
        elif query.mode == "exact":
            result = SearchResult.empty(query)
        else:
            return self._semantic(
                query, ("exact", "semantic"), resolution.filtered_out
            )
        return DispatchResult(result, ("exact",), None, resolution.filtered_out)

    def _semantic(
        self,
        query: SearchQuery,
        path: tuple[DispatchStep, ...],
        filtered_out: int,
    ) -> DispatchResult:
        if self.index is None or self.embedder is None:
            failed = SearchResult.failed(query, "index_not_ready")
            return DispatchResult(failed, path, None, filtered_out)
        status = self.index.assess(self.embedder.identity, self.fragmentation)
        result = query_index(self.index, query, self.embedder, self.fragmentation)
        return DispatchResult(result, path, status, filtered_out)
