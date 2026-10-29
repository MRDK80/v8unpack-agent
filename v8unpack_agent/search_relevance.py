"""Сигнал «низкая уверенность» смыслового поиска (#408, решение владельца A).

Сигнал не меняет выдачу, её порядок и оценки. Он сравнивает наибольший
косинус запроса с артефактами одного уровня снимка (уровень объектов, если
он задан, иначе уровень фрагментов) с порогом ``min_score``, который задаёт
вызывающий. Порог зависит от модели и корпуса, поэтому значения по умолчанию
нет; исследованные значения для BAAI/bge-m3 — в
``docs/research/relevance_signal_issue408.md``.

Оценка двухуровневой выдачи (``query_two_level``) — шкала слияния рангов, не
косинус, поэтому сигнал считается отдельным запросом к одному уровню с теми же
фильтрами.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from v8unpack_agent.search_contract import SearchFilters, SearchQuery
from v8unpack_agent.search_fragments import FragmentationResult
from v8unpack_agent.search_index import Embedder, SearchIndex, object_level, query_index

__all__ = [
    "MemoEmbedder",
    "RelevanceLevel",
    "RelevanceSignal",
    "check_min_score",
    "relevance_signal",
]

RelevanceLevel = Literal["objects", "fragments"]


@dataclass(frozen=True)
class RelevanceSignal:
    """Результат сигнала.

    ``max_similarity`` и ``low_confidence`` равны ``None``, если сигнал
    посчитать нельзя (нет индекса или эмбеддера, индекс несовместим или
    устарел, запрос только точный). Пустая выдача уровня — ``low_confidence``.
    """

    level: RelevanceLevel
    min_score: float
    max_similarity: float | None
    low_confidence: bool | None

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            "level": self.level,
            "min_score": self.min_score,
            "max_similarity": self.max_similarity,
            "low_confidence": self.low_confidence,
        }


def check_min_score(value: float) -> float:
    """Порог — конечное число в ``[-1, 1]`` (шкала косинуса)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("min_score must be a number")
    value = float(value)
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError("min_score must be finite and within [-1, 1]")
    return value


def relevance_signal(
    text: str,
    *,
    min_score: float,
    index: SearchIndex | None,
    embedder: Embedder | None,
    fragmentation: FragmentationResult | None = None,
    object_index: SearchIndex | None = None,
    filters: SearchFilters | None = None,
    search_mode: str = "auto",
) -> RelevanceSignal:
    """Наибольший косинус уровня против порога ``min_score``."""
    min_score = check_min_score(min_score)
    level: RelevanceLevel = "objects" if object_index is not None else "fragments"
    chosen = object_index if object_index is not None else index
    unavailable = RelevanceSignal(level, min_score, None, None)
    if chosen is None or embedder is None or search_mode == "exact":
        return unavailable
    query = SearchQuery(text, mode="semantic", top_k=1,
                        filters=filters if filters is not None else SearchFilters())
    target = fragmentation
    if object_index is not None and fragmentation is not None:
        target = object_level(fragmentation)
    result = query_index(chosen, query, embedder, target)
    if result.status == "error":
        return unavailable
    if not result.hits:
        return RelevanceSignal(level, min_score, None, True)
    best = result.hits[0].similarity
    assert best is not None  # смысловое попадание всегда с оценкой
    return RelevanceSignal(level, min_score, best, best < min_score)


class MemoEmbedder:
    """Повторный ``embed`` тех же текстов без повторного вызова модели."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._memo: dict[tuple[str, ...], Sequence[Sequence[float]]] = {}

    @property
    def identity(self) -> Any:
        return self._embedder.identity

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        key = tuple(texts)
        if key not in self._memo:
            self._memo[key] = self._embedder.embed(texts)
        return self._memo[key]
