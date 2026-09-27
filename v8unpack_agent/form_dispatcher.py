"""
Двухуровневая маршрутизация форм: FormRouter (точное совпадение) →
FormRagIndex (семантический fallback, issue #79).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v8unpack_agent.form_rag import FormRagIndex
    from v8unpack_agent.form_router import FormRouter, RouteResult


class FormDispatcher:
    """Оркестратор двухуровневой маршрутизации форм.

    Parameters
    ----------
    router:
        Первый уровень — точная строковая маршрутизация (:class:`FormRouter`).
        Всегда вызывается первым; стоимость — O(n) по числу форм в индексе.
    rag:
        Второй уровень — семантический поиск (:class:`FormRagIndex`).
        Вызывается только при промахе роутера. Если ``None``, промах
        возвращается как есть без обращения к RAG.

    Notes
    -----
    ``dispatch()`` не мутирует переданные объекты. Каждый :class:`RouteResult`
    в возвращаемом списке несёт поле ``source``: ``"router"`` для результата
    точного совпадения, ``"rag"`` для результатов семантического поиска.
    """

    def __init__(
        self,
        router: FormRouter,
        rag: FormRagIndex | None = None,
    ) -> None:
        self._router = router
        self._rag = rag

    def dispatch(self, query: str, top_k: int = 5) -> list[RouteResult]:
        """Выполнить двухуровневую маршрутизацию.

        Parameters
        ----------
        query:
            Строка запроса агента: имя формы, объекта или свободный текст.
        top_k:
            Максимальное число результатов из RAG-уровня (игнорируется при
            точном совпадении роутера).

        Returns
        -------
        list[RouteResult]
            * Точное совпадение — список из одного элемента с ``source="router"``.
            * Промах + RAG подключён — список до ``top_k`` элементов с
              ``source="rag"``.
            * Промах + RAG отсутствует — список из одного элемента с
              ``source="router"`` и пустым ``matched``.
        """
        result = self._router.route(query)

        if result.matched:
            # Точное совпадение: source уже «router» по дефолту RouteResult
            return [result]

        if self._rag is not None:
            rag_results = self._rag.query(query, top_k)
            for r in rag_results:
                r.source = "rag"
            return rag_results

        # RAG не подключён — возвращаем промах роутера без ошибки
        return [result]
