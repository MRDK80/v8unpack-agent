"""Сигнал «низкая уверенность» (#408): порог не меняет выдачу."""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_cli
from v8unpack_agent.search_contract import ArtifactRef, OwnerRef, SearchFilters
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_index import EmbedderIdentity, IndexEntry, SearchIndex
from v8unpack_agent.search_relevance import (
    MemoEmbedder,
    RelevanceSignal,
    check_min_score,
    relevance_signal,
)

IDENTITY = EmbedderIdentity("fake408", "1", 3, "c408")
OTHER = EmbedderIdentity("other408", "1", 3, "o408")
H = "0" * 64


class FixedEmbedder:
    identity = IDENTITY

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [list(self.vector) for _ in texts]


def _art(i: int) -> ArtifactRef:
    return ArtifactRef("module", OwnerRef("metadata_object", ("Catalog", "Document")[i % 2],
                                          f"Объект{i}"), "object")


def _index(spec: dict[int, list[tuple[float, float, float]]],
           identity: EmbedderIdentity = IDENTITY) -> SearchIndex:
    entries = []
    for i, vectors in spec.items():
        for p, vector in enumerate(vectors):
            entries.append(IndexEntry(f"{_art(i).artifact_id}#{p}", _art(i), f"П{p}",
                                      None, H, H, H, vector))
    return SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", identity, tuple(entries))


FRAG = _index({1: [(1, 0, 0), (0, 1, 0)], 2: [(0.6, 0.8, 0)]})
OBJ = _index({1: [(0, 1, 0)], 2: [(0.6, 0.8, 0)]})


def test_fragment_level_threshold() -> None:
    embedder = FixedEmbedder([1, 0, 0])
    high = relevance_signal("q", min_score=0.9, index=FRAG, embedder=embedder)
    assert high == RelevanceSignal("fragments", 0.9, 1.0, False)
    low = relevance_signal("q", min_score=1.0, index=FRAG, embedder=embedder)
    assert low.max_similarity == pytest.approx(1.0) and low.low_confidence is False
    assert relevance_signal("q", min_score=0.5,
                            index=FRAG, embedder=FixedEmbedder([0, 0, 1])).low_confidence is True


def test_object_level_preferred() -> None:
    signal = relevance_signal("q", min_score=0.7, index=FRAG, object_index=OBJ,
                              embedder=FixedEmbedder([1, 0, 0]))
    assert signal.level == "objects"
    assert signal.max_similarity == pytest.approx(0.6)
    assert signal.low_confidence is True
    assert signal.to_dict() == {"level": "objects", "min_score": 0.7,
                                "max_similarity": signal.max_similarity,
                                "low_confidence": True}


def test_filters_and_empty() -> None:
    filters = SearchFilters(metadata_types=("Report",))
    signal = relevance_signal("q", min_score=0.1, index=FRAG, filters=filters,
                              embedder=FixedEmbedder([1, 0, 0]))
    assert signal == RelevanceSignal("fragments", 0.1, None, True)
    only_doc = SearchFilters(metadata_types=("Catalog",))
    signal = relevance_signal("q", min_score=0.1, index=FRAG, filters=only_doc,
                              embedder=FixedEmbedder([1, 0, 0]))
    assert signal.max_similarity == pytest.approx(0.6)


@pytest.mark.parametrize("kwargs", [
    {"index": None},
    {"embedder": None},
    {"search_mode": "exact"},
    {"index": _index({1: [(1, 0, 0)]}, OTHER)},
])
def test_unavailable(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {"index": FRAG, "embedder": FixedEmbedder([1, 0, 0])}
    args.update(kwargs)
    signal = relevance_signal("q", min_score=0.5, **args)
    assert signal.max_similarity is None and signal.low_confidence is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -1.5, 1.01, True, "0.5"])
def test_bad_min_score(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        check_min_score(value)
    assert check_min_score(-1) == -1.0 and check_min_score(1) == 1.0


def test_memo_embedder() -> None:
    inner = FixedEmbedder([1, 0, 0])
    memo = MemoEmbedder(inner)
    assert memo.identity == IDENTITY
    assert memo.embed(["a"]) == memo.embed(["a"])
    memo.embed(["b"])
    assert inner.calls == 2


FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n\n"
        "Процедура ПриЗаписи()\n  Остаток = 2;\nКонецПроцедуры\n"
    ),
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура Первая() Экспорт\nКонецПроцедуры\n\nПроцедура Вторая() Экспорт\n"
        "  Сумма = 0;\nКонецПроцедуры\n"
    ),
}


@pytest.fixture
def export(tmp_path: Path) -> Path:
    root = tmp_path / "export-408"
    for relative, text in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return root


def _cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = search_cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out else None


def test_cli_min_score(export: Path, tmp_path: Path,
                       capsys: pytest.CaptureFixture[str]) -> None:
    snap = tmp_path / "s.json"
    demo = ("--embedder", "hashed-bow-demo")
    code, _ = _cli(capsys, "build", str(export), "--snapshot", str(snap), *demo)
    assert code == 0
    base = ("query", str(export), "--query", "Цена", "--search-mode", "semantic",
            "--snapshot", str(snap), *demo)
    code, plain = _cli(capsys, *base)
    assert code == 0 and "relevance" not in plain
    code, low = _cli(capsys, *base, "--min-score", "1")
    assert code == 0
    assert low["context"] == plain["context"]
    rel = low["relevance"]
    assert rel["level"] == "fragments" and rel["min_score"] == 1.0
    assert rel["max_similarity"] is not None and rel["low_confidence"] is True
    code, high = _cli(capsys, *base, "--min-score", "-1")
    assert high["relevance"]["low_confidence"] is False
    code, none = _cli(capsys, "query", str(export), "--query", "Цена", "--min-score", "0.5")
    assert none["relevance"]["max_similarity"] is None
    assert none["relevance"]["low_confidence"] is None


@pytest.mark.parametrize("value", ["nan", "2", "x"])
def test_cli_bad_min_score(value: str, export: Path,
                           capsys: pytest.CaptureFixture[str]) -> None:
    code = search_cli.main(["query", str(export), "--query", "q", "--min-score", value])
    capsys.readouterr()
    assert code == 2
