"""Синтетический офлайн-пример общего поиска (#368).

Категория: самодостаточный, без аргументов.
Входные данные: две синтетические формы и четыре вида BSL-модулей в памяти.
Ожидаемый результат: детерминированная сводка, Самопроверка: OK, RC=0.
Поведение без данных: внешние данные не нужны, временные снимки удаляются.
Корпус — публичные типы в памяти, не scanner и не проверка layout выгрузки.
Эмбеддер показывает механику, не качество поиска; сети и загрузки модели нет.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from v8unpack_agent.modules import ModuleEntry, ModuleKind, OwnerKind
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    build_search_snapshot,
    search_with_context,
)
from v8unpack_agent.search_contract import (
    ArtifactRef,
    LinkBasis,
    OwnerLink,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    SearchFilters,
)
from v8unpack_agent.search_corpus import (
    CorpusItem,
    CorpusReport,
    CorpusSource,
    SearchCorpus,
)
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import SearchIndexError, load_index


class _CheckFailed(Exception):
    """Ошибка самопроверки."""


def _check(condition: bool, label: str) -> None:
    if not condition:
        raise _CheckFailed(label)


def _corpus() -> tuple[SearchCorpus, dict[str, ArtifactRef]]:
    documents: list[SearchDocument] = []
    links: list[OwnerLink] = []
    items: list[CorpusItem] = []
    selected: dict[str, ArtifactRef] = {}

    def add(artifact: ArtifactRef, text: str, source: CorpusSource) -> None:
        documents.append(SearchDocument(artifact, text))
        owner = artifact.owner
        basis: LinkBasis = (
            "form_key" if artifact.artifact_kind == "form" else "module_entry"
        )
        if owner is not None:
            links.append(OwnerLink(owner, artifact, basis=basis))
        items.append(
            CorpusItem(
                artifact_id=artifact.artifact_id,
                artifact_kind=artifact.artifact_kind,
                source=source,
                source_path=artifact.source_path,
                included=True,
                owner_id=owner.owner_id if owner is not None else None,
                owner_basis=basis if owner is not None else None,
            )
        )

    for object_type, object_name, container, description in (
        ("Catalog", "Товары", "CatalogForm", "карточка товара"),
        ("Document", "Заказ", "DocumentForm", "карточка заказа"),
    ):
        artifact = ArtifactRef.form(
            object_type, object_name, container, "ФормаКарточки",
            owner=OwnerRef("metadata_object", object_type, object_name),
            source_path=(
                f"{object_type}/{object_name}/{container}/"
                "ФормаКарточки/Form.obj.bsl"
            ),
        )
        text = (
            "Процедура ПечатьКарточки()\n"
            f'    Сообщить("печать {description}");\n'
            "КонецПроцедуры\n"
        )
        add(artifact, text, "forms")
        if object_type == "Catalog":
            selected["form"] = artifact

    specs: tuple[
        tuple[ModuleKind, OwnerKind, str | None, str, str, str], ...
    ] = (
        ("object", "metadata_object", "Catalog", "Товары",
         "modules/goods_object.bsl", "сохранение карточки товара"),
        ("manager", "metadata_object", "Catalog", "Товары",
         "modules/goods_manager.bsl", "поиск товара по коду"),
        ("record_set", "metadata_object", "InformationRegister", "Остатки",
         "modules/stock_record_set.bsl", "запись остатков товара"),
        ("common_module", "common_module", "CommonModule", "ОбменДанными",
         "modules/exchange_common.bsl", "обмен данными заказов"),
    )
    for kind, owner_kind, metadata_type, owner_name, source_path, description in specs:
        text = (
            "Процедура Выполнить()\n"
            f'    Сообщить("{description}");\n'
            "КонецПроцедуры\n"
        )
        encoded = text.encode("utf-8")
        entry = ModuleEntry(
            module_kind=kind, owner_kind=owner_kind,
            metadata_type=metadata_type, owner_name=owner_name,
            relative_path=source_path, read_status="ok",
            size_bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(),
        )
        artifact = ArtifactRef.from_module_entry(entry)
        add(artifact, text, "module_index")
        selected[kind] = artifact
    return SearchCorpus(
        documents=SearchDocumentSet.from_documents(documents),
        owner_links=tuple(sorted(links, key=lambda link: link.artifact.sort_key())),
        report=CorpusReport(tuple(sorted(items, key=lambda item: item.sort_key()))),
    ), selected


def _run() -> None:
    corpus, selected = _corpus()
    fragmentation = fragment_corpus(corpus)
    embedder = HashedBagOfWordsDemoEmbedder()
    _check(len(corpus.documents) == 6, "artifact_count")
    print("Эмбеддер: hashed-bow-demo — механика, не качество поиска")
    print("Корпус: формы=2; модули=4; виды=common_module,manager,object,record_set")
    with TemporaryDirectory(prefix="unified-search-example-") as temporary:
        snapshot = Path(temporary) / "snapshot.json"
        built = build_search_snapshot(fragmentation, embedder, snapshot)
        index = load_index(snapshot)
        _check(built.to_json() == index.to_json(), "snapshot_load")
        assessment = index.assess(embedder.identity, fragmentation)
        _check(assessment.compatibility == "compatible", "compatibility")
        _check(assessment.freshness == "fresh", "freshness")
        print(
            f"Снимок: schema={index.schema}; entries={len(index.entries)}; "
            f"freshness={assessment.freshness}"
        )
        exact = search_with_context(
            fragmentation, selected["form"].artifact_id,
            search_mode="exact", max_chars=-1,
        )
        _check(exact.search_kind == "exact" and bool(exact.text), "exact_form")
        _check(exact.to_dict() != exact.to_dict(include_text=True), "include_text")
        print(f"Точный id формы: search={exact.search_kind}; context={exact.status}")
        ambiguous = search_with_context(
            fragmentation, "ФормаКарточки", max_chars=-1,
        )
        _check(ambiguous.search_kind == "ambiguous", "ambiguous_name")
        _check(len(ambiguous.dispatch.result.hits) == 2, "alternatives")
        print("Имя формы: search=ambiguous; alternatives=2")
        filtered = search_with_context(
            fragmentation, selected["manager"].artifact_id,
            search_mode="exact", filters=SearchFilters(module_kinds=("manager",)),
            max_chars=-1,
        )
        _check(filtered.search_kind == "exact", "module_filter")
        _check(filtered.dispatch.result.hits[0].artifact.module_kind == "manager", "manager_kind")
        print("Фильтр module_kind=manager: search=exact")
        empty = search_with_context(
            fragmentation, "несуществующий точный идентификатор",
            search_mode="exact", max_chars=-1,
        )
        _check(empty.search_kind == "empty", "exact_miss")
        print("Точный промах: search=empty")
        semantic = search_with_context(
            fragmentation, "печать карточка товара", search_mode="semantic",
            top_k=3, index=index, embedder=embedder, max_chars=-1,
        )
        _check(semantic.search_kind == "semantic", "semantic_query")
        hits = semantic.dispatch.result.hits
        _check(0 < len(hits) <= 3, "top_k")
        _check(len({hit.artifact.artifact_id for hit in hits}) == len(hits), "unique_artifacts")
        print(f"Смысловой запрос: search=semantic; candidates={len(hits)}")
        unavailable = search_with_context(
            fragmentation, "печать карточка товара", search_mode="semantic",
            index=index, max_chars=-1,
        )
        error = unavailable.dispatch.result.error
        _check(error is not None and error.code == "index_not_ready", "missing_embedder")
        print("Без эмбеддера: search=error; code=index_not_ready")
        limited = search_with_context(
            fragmentation, selected["form"].artifact_id,
            search_mode="exact", max_chars=0,
        )
        _check(limited.status == "insufficient_budget", "zero_budget")
        _check(not limited.text, "empty_context")
        print("Бюджет max_chars=0: search=exact; context=insufficient_budget")
    print("Самопроверка: OK")


def main() -> int:
    """Запустить пример и вернуть код завершения."""
    try:
        _run()
    except _CheckFailed as exc:
        print(f"Самопроверка: FAIL ({exc})")
        return 1
    except (OSError, ValueError, SearchIndexError) as exc:
        print(f"Самопроверка: FAIL ({type(exc).__name__})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
