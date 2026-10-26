"""Повторные запросы не пересобирают каталог и индексы фрагментации (#397).

Проверяются эквивалентность результатов (кэшированный результат
фрагментации даёт тот же контекст, что свежий) и отсутствие повторной
подготовки: после первого запроса набор документов не пересоздаётся и
фрагменты корпуса больше не перебираются. Жёстких таймингов нет.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent import search_contract
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchFilters
from v8unpack_agent.search_dispatch import ExactCatalog
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import build_index

FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n"
        "Процедура ПриЗаписи()\n  Остаток = 2;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриОткрытии()\n  ЦенаТовара = 1;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.json": "{}",
    "Document/Заказ/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриСозданииНаСервере()\n  СуммаЗаказа = 0;\nКонецПроцедуры\n"
    ),
    "Document/Заказ/Form/ФормаЭлемента/Form.json": "{}",
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура ОбщаяПроцедура() Экспорт\nКонецПроцедуры\n"
    ),
    "Constant/Rate/Constant.obj.bsl": "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n",
    "HTTPService/Rate/HTTPService.obj.bsl": "Функция Получить(Запрос)\nКонецФункции\n",
}


class _CountingTuple(tuple):  # type: ignore[type-arg]
    iterations = 0

    def __iter__(self) -> Any:
        type(self).iterations += 1
        return super().__iter__()


@pytest.fixture
def export(tmp_path: Path) -> Path:
    root = tmp_path / "export-397"
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _queries(fragmentation: FragmentationResult) -> list[tuple[str, dict[str, Any]]]:
    ids = sorted({f.document.artifact.artifact_id for f in fragmentation.fragments})
    cases: list[tuple[str, dict[str, Any]]] = []
    for artifact_id in ids:
        cases.append((artifact_id, {"search_mode": "exact"}))
        cases.append((artifact_id, {"search_mode": "auto", "max_chars": 120}))
    cases += [
        ("ФормаЭлемента", {"search_mode": "exact"}),
        ("Rate", {"search_mode": "auto"}),
        ("нет такого объекта", {"search_mode": "exact"}),
        ("цена товара", {"search_mode": "semantic", "top_k": 3}),
        ("цена товара", {"search_mode": "semantic", "top_k": 2,
                         "filters": SearchFilters(artifact_kinds=("form",))}),
        ("сумма заказа", {"search_mode": "auto", "top_k": 1}),
    ]
    return cases


def _run(fragmentation: FragmentationResult, text: str, kwargs: dict[str, Any]) -> Any:
    params = dict(kwargs)
    params.setdefault("max_chars", -1)
    index = build_index(fragmentation, HashedBagOfWordsDemoEmbedder())
    return search_with_context(
        fragmentation,
        text,
        index=index,
        embedder=HashedBagOfWordsDemoEmbedder(),
        **params,
    ).to_dict(include_text=True)


def test_cached_results_equal_fresh_fragmentation(export: Path) -> None:
    corpus = collect_search_corpus(export)
    shared = fragment_corpus(corpus)
    for text, kwargs in _queries(shared):
        fresh = fragment_corpus(corpus)
        assert _run(shared, text, kwargs) == _run(fresh, text, kwargs), (text, kwargs)


def test_cache_is_invisible_to_equality_and_serialization(export: Path) -> None:
    corpus = collect_search_corpus(export)
    warm = fragment_corpus(corpus)
    cold = fragment_corpus(corpus)
    search_with_context(warm, "ФормаЭлемента", search_mode="exact", max_chars=-1)
    assert warm._derived and not cold._derived
    assert warm == cold
    assert hash(warm.parameters) == hash(cold.parameters)
    assert warm.to_json(include_text=True) == cold.to_json(include_text=True)
    assert "_derived" not in repr(warm)


def test_repeated_queries_do_not_rebuild_or_rescan(
    export: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fragmentation = fragment_corpus(collect_search_corpus(export))
    ids = sorted({f.document.artifact.artifact_id for f in fragmentation.fragments})
    built = {"document_sets": 0}
    original = search_contract.SearchDocumentSet.__post_init__

    def counting(self: Any) -> None:
        built["document_sets"] += 1
        original(self)

    monkeypatch.setattr(search_contract.SearchDocumentSet, "__post_init__", counting)
    counted = _CountingTuple(fragmentation.fragments)
    object.__setattr__(fragmentation, "fragments", counted)

    search_with_context(fragmentation, ids[0], search_mode="exact", max_chars=-1)
    first_sets = built["document_sets"]
    first_scans = _CountingTuple.iterations
    catalog = ExactCatalog.from_fragmentation(fragmentation)
    assert first_sets >= 1
    assert first_scans >= 1

    for artifact_id in ids * 5:
        context = search_with_context(
            fragmentation, artifact_id, search_mode="exact", max_chars=-1
        )
        assert context.dispatch.result.status == "exact"
        assert [h.artifact.artifact_id for h in context.dispatch.result.hits] == [artifact_id]
    assert built["document_sets"] == first_sets
    assert _CountingTuple.iterations == first_scans
    assert ExactCatalog.from_fragmentation(fragmentation) is catalog
