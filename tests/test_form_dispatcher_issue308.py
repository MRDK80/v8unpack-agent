"""Тесты FormDispatcher: маркировка source без мутации выдачи RAG (issue #308).

Покрываются acceptance criteria:
- exact hit не вызывает RAG и возвращает результат роутера с source="router";
- router miss + RAG: source="rag" без изменения объектов из rag.query();
- повторная выдача одного и того же объекта RAG не меняет его состояние;
- matched, confidence, warnings, порядок и количество сохраняются;
- пустая выдача RAG, rag=None и типизированные ошибки RAG — прежняя семантика;
- RouteResult без явного source остаётся "router".

Только синтетические данные: записи форм — простые объекты-маркеры.
"""
from unittest.mock import MagicMock

import pytest

from v8unpack_agent.form_dispatcher import FormDispatcher
from v8unpack_agent.form_rag import RagQueryError
from v8unpack_agent.form_router import RouteResult


class _Entry:
    """Синтетический маркер записи формы; сравнивается по идентичности."""

    def __init__(self, name: str) -> None:
        self.form_name = name


def _router_miss() -> MagicMock:
    router = MagicMock()
    router.route.return_value = RouteResult(
        matched=[], confidence=0.0, warnings=["No match for query: 'x'"],
    )
    return router


def _router_hit(entry: _Entry) -> MagicMock:
    router = MagicMock()
    router.route.return_value = RouteResult(matched=[entry], confidence=1.0)
    return router


def _rag_returning(results: list[RouteResult]) -> MagicMock:
    """Fake RAG, который на каждый вызов отдаёт одни и те же объекты."""
    rag = MagicMock()
    rag.query.side_effect = lambda text, top_k: list(results)
    return rag


def _snapshot(result: RouteResult) -> tuple:
    return (
        result.source,
        list(result.matched),
        result.confidence,
        list(result.warnings),
    )


# ---------------------------------------------------------------------------
# Регрессия #308: исходный объект RAG не мутируется
# ---------------------------------------------------------------------------


class TestRagResultNotMutated:
    def test_held_reference_unchanged_after_dispatch(self):
        entry = _Entry("ФормаСписка")
        original = RouteResult(
            matched=[entry], confidence=0.85, warnings=["w1"],
        )
        matched_list = original.matched
        warnings_list = original.warnings
        before = _snapshot(original)
        dispatcher = FormDispatcher(
            router=_router_miss(), rag=_rag_returning([original]),
        )

        results = dispatcher.dispatch("список документов")

        assert [r.source for r in results] == ["rag"]
        assert _snapshot(original) == before
        assert original.source == "router"
        assert original.matched is matched_list
        assert original.warnings is warnings_list
        assert results[0] is not original

    def test_repeated_dispatch_with_same_rag_object(self):
        entry = _Entry("ФормаСписка")
        original = RouteResult(matched=[entry], confidence=0.5)
        before = _snapshot(original)
        rag = _rag_returning([original])
        dispatcher = FormDispatcher(router=_router_miss(), rag=rag)

        first = dispatcher.dispatch("запрос")
        assert _snapshot(original) == before
        second = dispatcher.dispatch("запрос")

        assert rag.query.call_count == 2
        assert first[0].source == "rag"
        assert second[0].source == "rag"
        assert first[0] is not second[0]
        assert _snapshot(original) == before
        assert original.source == "router"

    def test_rag_object_with_explicit_source_rag_not_mutated(self):
        original = RouteResult(
            matched=[_Entry("Ф")], confidence=0.3, source="rag",
        )
        dispatcher = FormDispatcher(
            router=_router_miss(), rag=_rag_returning([original]),
        )

        results = dispatcher.dispatch("запрос")

        assert results[0].source == "rag"
        assert results[0] is not original
        assert original.source == "rag"

    def test_mutating_output_lists_does_not_touch_rag_object(self):
        original = RouteResult(
            matched=[_Entry("Ф")], confidence=0.7, warnings=["w"],
        )
        before = _snapshot(original)
        dispatcher = FormDispatcher(
            router=_router_miss(), rag=_rag_returning([original]),
        )

        results = dispatcher.dispatch("запрос")
        results[0].warnings.append("extra")
        results[0].matched.clear()

        assert _snapshot(original) == before

    def test_rag_returned_list_not_mutated(self):
        original = RouteResult(matched=[_Entry("Ф")], confidence=0.7)
        rag_list = [original]
        rag = MagicMock()
        rag.query.return_value = rag_list
        dispatcher = FormDispatcher(router=_router_miss(), rag=rag)

        results = dispatcher.dispatch("запрос")

        assert results is not rag_list
        assert rag_list == [original]
        assert rag_list[0] is original


# ---------------------------------------------------------------------------
# Сохранение полей, порядка и количества
# ---------------------------------------------------------------------------


class TestRagResultFieldsPreserved:
    def test_order_count_and_fields(self):
        entries = [_Entry("А"), _Entry("Б"), _Entry("В")]
        originals = [
            RouteResult(matched=[entries[0]], confidence=0.9),
            RouteResult(matched=[entries[1]], confidence=0.6, warnings=["w"]),
            RouteResult(matched=[entries[2]], confidence=0.6),
        ]
        dispatcher = FormDispatcher(
            router=_router_miss(), rag=_rag_returning(originals),
        )

        results = dispatcher.dispatch("запрос", top_k=3)

        assert len(results) == 3
        for got, src in zip(results, originals):
            assert got.source == "rag"
            assert got.matched == src.matched
            assert got.matched[0] is src.matched[0]
            assert got.confidence == src.confidence
            assert got.warnings == src.warnings
        assert all(r.source == "router" for r in originals)

    def test_empty_rag_output_stays_empty(self):
        rag = _rag_returning([])
        dispatcher = FormDispatcher(router=_router_miss(), rag=rag)

        assert dispatcher.dispatch("запрос") == []
        rag.query.assert_called_once_with("запрос", 5)


# ---------------------------------------------------------------------------
# Прежняя семантика остальных ветвей
# ---------------------------------------------------------------------------


class TestUnchangedBranches:
    def test_exact_hit_does_not_call_rag(self):
        entry = _Entry("ФормаОбъекта")
        router = _router_hit(entry)
        routed = router.route.return_value
        rag = _rag_returning([])
        dispatcher = FormDispatcher(router=router, rag=rag)

        results = dispatcher.dispatch("ФормаОбъекта")

        rag.query.assert_not_called()
        assert results == [routed]
        assert results[0] is routed
        assert results[0].source == "router"
        assert results[0].matched == [entry]

    def test_miss_without_rag_returns_router_result(self):
        router = _router_miss()
        routed = router.route.return_value
        dispatcher = FormDispatcher(router=router, rag=None)

        results = dispatcher.dispatch("x")

        assert results == [routed]
        assert results[0].source == "router"
        assert results[0].matched == []

    def test_typed_rag_error_propagates(self):
        rag = MagicMock()
        rag.query.side_effect = RagQueryError("top_k must be a positive int")
        dispatcher = FormDispatcher(router=_router_miss(), rag=rag)

        with pytest.raises(RagQueryError, match="top_k"):
            dispatcher.dispatch("запрос", top_k=0)

    def test_route_result_default_source_is_router(self):
        assert RouteResult(matched=[], confidence=0.0).source == "router"
