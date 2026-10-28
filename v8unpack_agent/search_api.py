"""Библиотечный вход общего поиска (#328, часть A — #366).

Тонкая композиция существующих слоёв: корпус (#323) → фрагментация (#324) →
индекс (#325) → диспетчер (#326) → контекст (#327). Логика слоёв здесь не
повторяется. Эмбеддер передаётся только явно: модуль не выбирает провайдера,
не загружает модели и не обращается к сети.

Корпус собирается теми же вызовами, что и runner: формы через ``scan_forms`` и
``build_form_context``, общие модули через ``scan_common_modules``, индекс
модулей через ``runner._module_scanners`` (единый источник групп #346), в
режиме ``external`` дополнительно ``scan_external_object_modules`` (#351).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from v8unpack_agent import runner
from v8unpack_agent.common_modules import CommonModuleIndex, scan_common_modules
from v8unpack_agent.external_object_modules import scan_external_object_modules
from v8unpack_agent.modules import ModuleEntry, ModuleIndex
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_context import SearchContext, build_search_context
from v8unpack_agent.search_contract import QueryMode, SearchFilters, SearchQuery
from v8unpack_agent.search_corpus import (
    SearchCorpus,
    build_form_inputs,
    build_search_corpus,
)
from v8unpack_agent.search_dispatch import ExactCatalog, SearchDispatcher
from v8unpack_agent.search_fragments import FragmentationResult
from v8unpack_agent.search_index import (
    SEARCH_INDEX_SCHEMA,
    Embedder,
    EmbedderIdentity,
    SearchIndex,
    build_index,
    save_index,
)

__all__ = [
    "DEMO_EMBEDDER_NAME",
    "HashedBagOfWordsDemoEmbedder",
    "SearchCorpusError",
    "build_search_snapshot",
    "collect_search_corpus",
    "search_with_context",
]

DEMO_EMBEDDER_NAME = "hashed-bow-demo"

_MODES = ("config", "external")
_DEMO_IDENTITY_NAME = "v8unpack-agent-hashed-bow-demo"
_DEMO_VERSION = "1"
_DEMO_DIMENSION = 256
_DEMO_TOKENIZATION_VERSION = "1"
_DEMO_EMPTY_TOKEN = "\x00empty"
_WORD_RE = re.compile(r"\w+")


class SearchCorpusError(Exception):
    """Сбой стадии сбора корпуса; ``code`` — константа runner.

    Коды: ``scan_failed`` (формы), ``common_modules_failed``,
    ``modules_failed``. Текст фиксирован и не содержит путей и исходного
    исключения; исходное исключение доступно как ``__cause__``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(f"search corpus stage failed: {code}")
        self.code = code


def collect_search_corpus(
    export_root: str | os.PathLike[str],
    *,
    mode: Literal["config", "external"] = "config",
    include_common_modules: bool = True,
    module_groups: tuple[str, ...] | None = None,
) -> SearchCorpus:
    """Собрать поисковый корпус выгрузки теми же сканерами, что и runner.

    ``module_groups`` проверяется и нормализуется по правилам
    ``RunOptions`` (#346): ``None`` — все группы. Ошибка значения даёт
    ``ValueError`` без повтора введённых данных. Сбой стадии обнаружения —
    :class:`SearchCorpusError`.
    """
    if mode not in _MODES:
        raise ValueError("mode: ожидается 'config' или 'external'")
    root = Path(export_root)
    groups: tuple[str, ...] | None = None
    if module_groups is not None:
        groups = runner.RunOptions(
            export_root=root,
            include_module_index=True,
            module_groups=module_groups,
        ).module_groups

    try:
        form_index = scan_forms(root, mode=mode, include_elem_only=True)
        forms = build_form_inputs(
            form_index.forms,
            root,
            type_resolver=form_index.resolve_reference_type,
        )
    except Exception as exc:
        raise SearchCorpusError(runner.FATAL_SCAN_FAILED) from exc

    common_modules: CommonModuleIndex | None = None
    if include_common_modules:
        try:
            common_modules = scan_common_modules(root)
        except Exception as exc:
            raise SearchCorpusError(runner.FATAL_COMMON_MODULES_FAILED) from exc

    try:
        entries: list[ModuleEntry] = []
        for scanner in runner._module_scanners(groups):
            entries.extend(scanner(root))
        if mode == "external":
            entries.extend(scan_external_object_modules(root))
        module_index = ModuleIndex.from_entries(entries)
    except Exception as exc:
        raise SearchCorpusError(runner.FATAL_MODULES_FAILED) from exc

    return build_search_corpus(
        root,
        module_index=module_index,
        common_modules=common_modules,
        forms=forms,
    )


def build_search_snapshot(
    fragmentation: FragmentationResult,
    embedder: Embedder,
    path: str | os.PathLike[str],
    *,
    schema: str = SEARCH_INDEX_SCHEMA,
) -> SearchIndex:
    """Построить индекс по явно переданному эмбеддеру и атомарно сохранить.

    ``schema`` — формат снимка: ``search_index/1`` (по умолчанию) или
    ``search_index/2`` (#413).
    """
    index = build_index(fragmentation, embedder)
    save_index(index, path, schema=schema)
    return index


def search_with_context(
    fragmentation: FragmentationResult,
    text: str,
    *,
    search_mode: QueryMode = "auto",
    top_k: int = 5,
    filters: SearchFilters | None = None,
    index: SearchIndex | None = None,
    embedder: Embedder | None = None,
    max_chars: int,
    max_tokens: int | None = None,
    count_tokens: Callable[[str], int] | None = None,
) -> SearchContext:
    """Выполнить запрос через общий диспетчер и собрать контекст с бюджетом.

    Без ``index`` или ``embedder`` смысловой шаг даёт ``index_not_ready``;
    точный поиск работает без эмбеддера.
    """
    query = SearchQuery(
        text,
        mode=search_mode,
        top_k=top_k,
        filters=filters if filters is not None else SearchFilters(),
    )
    dispatcher = SearchDispatcher(
        ExactCatalog.from_fragmentation(fragmentation),
        index,
        embedder,
        fragmentation,
    )
    return build_search_context(
        dispatcher.search(query),
        fragmentation,
        max_chars=max_chars,
        max_tokens=max_tokens,
        count_tokens=count_tokens,
    )


def _demo_config_hash() -> str:
    params = {
        "dimension": _DEMO_DIMENSION,
        "tokenization_version": _DEMO_TOKENIZATION_VERSION,
    }
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _demo_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    return _WORD_RE.findall(normalized) or [_DEMO_EMPTY_TOKEN]


def _demo_bucket(token: str) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _DEMO_DIMENSION


class HashedBagOfWordsDemoEmbedder:
    """Демонстрационный эмбеддер ``hashed-bow-demo``: механика, не качество.

    Детерминированный хэшированный мешок слов размерности 256: NFC +
    ``casefold``, слова — последовательности буквенно-цифровых символов
    и ``_``, корзина — SHA-256 слова по модулю
    размерности, вектор нормирован по L2. Текст без слов даёт вектор
    служебного маркера. Предназначен для проверки цепочки построения и
    запроса индекса; оценкой качества смыслового поиска не является.
    Сети, загрузки весов и провайдеров нет.
    """

    @property
    def identity(self) -> EmbedderIdentity:
        """Идентичность: имя, версия, размерность и хэш параметров."""
        return EmbedderIdentity(
            _DEMO_IDENTITY_NAME,
            _DEMO_VERSION,
            _DEMO_DIMENSION,
            _demo_config_hash(),
        )

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Векторы текстов в исходном порядке."""
        return tuple(self._vector(text) for text in texts)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        counts = [0.0] * _DEMO_DIMENSION
        for token in _demo_tokens(text):
            counts[_demo_bucket(token)] += 1.0
        norm = math.sqrt(math.fsum(value * value for value in counts))
        return tuple(value / norm for value in counts)
