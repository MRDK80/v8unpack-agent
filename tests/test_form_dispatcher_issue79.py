"""Тесты FormDispatcher — двухуровневая маршрутизация (issue #79).

Покрываются acceptance criteria:
- точное попадание роутера (source="router");
- промах роутера → RAG-hit (source="rag");
- rag=None, промах роутера — возврат без ошибки (source="router");
- RouteResult.source: обратная совместимость (дефолт "router").
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from v8unpack_agent.form_dispatcher import FormDispatcher
from v8unpack_agent.form_router import RouteResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(**kwargs):
    """Минимальная заглушка FormEntry через MagicMock."""
    entry = MagicMock()
    for k, v in kwargs.items():
        setattr(entry, k, v)
    return entry


def _router_hit(entry) -> MagicMock:
    """Роутер, возвращающий точное совпадение."""
    router = MagicMock()
    router.route.return_value = RouteResult(
        matched=[entry], confidence=1.0, source="router",
    )
    return router


def _router_miss() -> MagicMock:
    """Роутер, возвращающий промах."""
    router = MagicMock()
    router.route.return_value = RouteResult(
        matched=[], confidence=0.0,
        warnings=["No match for query: 'unknown'"],
        source="router",
    )
    return router


def _rag_hit(entry) -> MagicMock:
    """RAG, возвращающий один результат с confidence=0.85."""
    rag = MagicMock()
    rag.query.return_value = [
        RouteResult(matched=[entry], confidence=0.85, source="router"),
    ]
    return rag


# ---------------------------------------------------------------------------
# Test 1: точное попадание роутера
# ---------------------------------------------------------------------------


class TestRouterHit:
    def test_returns_single_result(self):
        entry = _make_entry(form_name="ФормаОбъекта")
        dispatcher = FormDispatcher(router=_router_hit(entry))
        results = dispatcher.dispatch("ФормаОбъекта")
        assert len(results) == 1

    def test_source_is_router(self):
        entry = _make_entry(form_name="ФормаОбъекта")
        dispatcher = FormDispatcher(router=_router_hit(entry))
        results = dispatcher.dispatch("ФормаОбъекта")
        assert results[0].source == "router"

    def test_matched_entry_preserved(self):
        entry = _make_entry(form_name="ФормаОбъекта")
        dispatcher = FormDispatcher(router=_router_hit(entry))
        results = dispatcher.dispatch("ФормаОбъекта")
        assert results[0].matched == [entry]

    def test_rag_not_called_on_hit(self):
        entry = _make_entry(form_name="ФормаОбъекта")
        rag = MagicMock()
        dispatcher = FormDispatcher(router=_router_hit(entry), rag=rag)
        dispatcher.dispatch("ФормаОбъекта")
        rag.query.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: промах роутера → RAG-hit
# ---------------------------------------------------------------------------


class TestRagFallback:
    def test_returns_rag_results(self):
        entry = _make_entry(form_name="ФормаСписка")
        dispatcher = FormDispatcher(router=_router_miss(), rag=_rag_hit(entry))
        results = dispatcher.dispatch("список документов")
        assert len(results) == 1

    def test_source_is_rag(self):
        entry = _make_entry(form_name="ФормаСписка")
        dispatcher = FormDispatcher(router=_router_miss(), rag=_rag_hit(entry))
        results = dispatcher.dispatch("список документов")
        assert results[0].source == "rag"

    def test_confidence_preserved(self):
        entry = _make_entry(form_name="ФормаСписка")
        dispatcher = FormDispatcher(router=_router_miss(), rag=_rag_hit(entry))
        results = dispatcher.dispatch("список документов")
        assert results[0].confidence == pytest.approx(0.85)

    def test_top_k_passed_to_rag(self):
        entry = _make_entry(form_name="ФормаСписка")
        rag = _rag_hit(entry)
        dispatcher = FormDispatcher(router=_router_miss(), rag=rag)
        dispatcher.dispatch("список", top_k=3)
        rag.query.assert_called_once_with("список", 3)


# ---------------------------------------------------------------------------
# Test 3: rag=None, промах роутера
# ---------------------------------------------------------------------------


class TestNoRag:
    def test_returns_single_result(self):
        dispatcher = FormDispatcher(router=_router_miss(), rag=None)
        results = dispatcher.dispatch("unknown")
        assert len(results) == 1

    def test_source_is_router(self):
        dispatcher = FormDispatcher(router=_router_miss(), rag=None)
        results = dispatcher.dispatch("unknown")
        assert results[0].source == "router"

    def test_matched_is_empty(self):
        dispatcher = FormDispatcher(router=_router_miss(), rag=None)
        results = dispatcher.dispatch("unknown")
        assert results[0].matched == []

    def test_no_exception_raised(self):
        dispatcher = FormDispatcher(router=_router_miss(), rag=None)
        results = dispatcher.dispatch("unknown")
        assert results is not None


# ---------------------------------------------------------------------------
# Test 4: RouteResult.source обратная совместимость
# ---------------------------------------------------------------------------


class TestRouteResultBackwardCompat:
    def test_default_source_is_router(self):
        """Код, не знающий о source, получает дефолт и не падает."""
        result = RouteResult(matched=[], confidence=0.0)
        assert result.source == "router"

    def test_positional_args_still_work(self):
        """Первые три позиционных аргумента не изменились."""
        entry = _make_entry()
        result = RouteResult([entry], 0.9, ["warn"])
        assert result.matched == [entry]
        assert result.confidence == pytest.approx(0.9)
        assert result.warnings == ["warn"]
        assert result.source == "router"
