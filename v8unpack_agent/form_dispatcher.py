"""
Двухуровневая маршрутизация форм: FormRouter (точное совпадение) →
FormRagIndex (семантический fallback, issue #79).
"""
from __future__ import annotations

from dataclasses import replace
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
    ``dispatch()`` не мутирует ни результат роутера, ни объекты, которые
    вернул ``rag.query()`` (issue #308). Каждый :class:`RouteResult`
    в возвращаемом списке несёт поле ``source``: ``"router"`` для результата
    роутера, ``"rag"`` для результатов семантического поиска.

    Результат роутера возвращается тем же объектом, что вернул
    ``router.route()``. Результаты RAG возвращаются новыми объектами
    :class:`RouteResult` с ``source="rag"``: ``matched``, ``confidence``,
    ``warnings``, порядок и количество совпадают с выдачей ``rag.query()``;
    списки ``matched`` и ``warnings`` копируются поверхностно, элементы
    :class:`FormEntry` в них общие с исходными результатами.
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
            * Промах + RAG подключён — список до ``top_k`` новых элементов
              с ``source="rag"`` в порядке выдачи RAG; пустая выдача RAG
              остаётся пустым списком. Исходные объекты RAG не изменяются.
            * Промах + RAG отсутствует — список из одного элемента с
              ``source="router"`` и пустым ``matched``.

        Raises
        ------
        RagQueryError
            Пробрасывается из ``rag.query()`` без перехвата.
        """
        result = self._router.route(query)

        if result.matched:
            # Точное совпадение: source уже «router» по дефолту RouteResult
            return [result]

        if self._rag is not None:
            # Новые объекты вместо присваивания r.source: выдача RAG
            # может переиспользоваться вызывающей стороной (issue #308).
            return [
                replace(
                    r,
                    matched=list(r.matched),
                    warnings=list(r.warnings),
                    source="rag",
                )
                for r in self._rag.query(query, top_k)
            ]

        # RAG не подключён — возвращаем промах роутера без ошибки
        return [result]
