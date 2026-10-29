"""Слияние рангов двух уровней только по явному флагу (#423)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_cli
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    build_object_snapshot,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchQuery
from v8unpack_agent.search_dispatch import ExactCatalog, SearchDispatcher
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import query_index, query_two_level

FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n\n"
        "Процедура ПриЗаписи()\n  Остаток = 2;\nКонецПроцедуры\n"
    ),
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура Первая() Экспорт\nКонецПроцедуры\n\nПроцедура Вторая() Экспорт\n"
        "  Сумма = 0;\n  Цена = Сумма;\nКонецПроцедуры\n"
    ),
}


@pytest.fixture
def export(tmp_path: Path) -> Path:
    root = tmp_path / "export-423"
    for relative, text in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return root


@pytest.fixture
def indexes(export: Path, tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    fragmentation = fragment_corpus(collect_search_corpus(export))
    embedder = HashedBagOfWordsDemoEmbedder()
    frag = build_search_snapshot(fragmentation, embedder, tmp_path / "f.json")
    obj = build_object_snapshot(fragmentation, embedder, tmp_path / "o.json")
    return fragmentation, embedder, frag, obj


def test_dispatcher_default_is_fragments(indexes: tuple[Any, Any, Any, Any]) -> None:
    fragmentation, embedder, frag, obj = indexes
    query = SearchQuery("Цена Сумма", mode="semantic", top_k=3)
    catalog = ExactCatalog.from_fragmentation(fragmentation)
    plain = SearchDispatcher(catalog, frag, embedder, fragmentation, obj).search(query)
    assert plain.result == query_index(frag, query, embedder, fragmentation)
    fused = SearchDispatcher(catalog, frag, embedder, fragmentation, obj, fusion=True).search(query)
    assert fused.result == query_two_level(frag, obj, query, embedder, fragmentation)
    assert fused.index_status == plain.index_status


def test_dispatcher_fusion_validation(indexes: tuple[Any, Any, Any, Any]) -> None:
    fragmentation, embedder, frag, _ = indexes
    catalog = ExactCatalog.from_fragmentation(fragmentation)
    with pytest.raises(ValueError):
        SearchDispatcher(catalog, frag, embedder, fragmentation, None, fusion=True)
    with pytest.raises(TypeError):
        SearchDispatcher(catalog, frag, embedder, fragmentation, None, fusion=1)  # type: ignore[arg-type]


def test_search_with_context_fusion(indexes: tuple[Any, Any, Any, Any]) -> None:
    fragmentation, embedder, frag, obj = indexes
    query = SearchQuery("Цена", mode="semantic", top_k=2)
    args: dict[str, Any] = {"search_mode": "semantic", "top_k": 2, "index": frag,
                            "embedder": embedder, "object_index": obj, "max_chars": -1}
    plain = search_with_context(fragmentation, "Цена", **args)
    fused = search_with_context(fragmentation, "Цена", fusion=True, **args)
    assert plain.dispatch.result == query_index(frag, query, embedder, fragmentation)
    assert fused.dispatch.result == query_two_level(frag, obj, query, embedder, fragmentation)


def _cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = search_cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out else None


def test_cli_rank_fusion(export: Path, tmp_path: Path,
                         capsys: pytest.CaptureFixture[str]) -> None:
    snap, obj = tmp_path / "s.json", tmp_path / "o.json"
    demo = ("--embedder", "hashed-bow-demo")
    code, _ = _cli(capsys, "build", str(export), "--snapshot", str(snap),
                   "--object-snapshot", str(obj), *demo)
    assert code == 0
    base = ("query", str(export), "--query", "Цена Сумма", "--search-mode", "semantic",
            "--snapshot", str(snap), *demo)
    code, only = _cli(capsys, *base)
    code2, plain = _cli(capsys, *base, "--object-snapshot", str(obj))
    code3, fused = _cli(capsys, *base, "--object-snapshot", str(obj), "--rank-fusion")
    assert code == code2 == code3 == 0
    def hits(d: Any) -> Any:
        return d["context"]["dispatch"]["result"]["hits"]

    assert hits(plain) == hits(only)
    assert plain["object_snapshot"]["loaded"] is True
    sims = [h["similarity"] for h in hits(fused)]
    assert sims and all(s < 0.1 for s in sims)  # шкала RRF, не косинус
    code, data = _cli(capsys, *base, "--rank-fusion")
    assert code == 2 and data is None
