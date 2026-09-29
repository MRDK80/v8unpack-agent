"""Полный офлайн-цикл RAG-маршрутизации форм (issue #312).

Пример синтетический: реальная выгрузка, платформа 1С, сеть и API-ключи не
нужны. Все имена придуманы. Выгрузка и артефакты создаются во временном
каталоге и удаляются после завершения.

Цепочка на настоящих публичных классах пакета:

1. ``build_form_context()`` → ``FormContext`` для трёх синтетических форм;
2. ``FormScanIndex.save()`` → JSON-индекс для ``FormRouter``;
3. ``FormRagIndex.build(contexts, embedder)`` с явно переданным эмбеддером;
4. ``save(index_dir)`` → ``load(index_dir, embedder)`` на новом экземпляре;
5. ``FormDispatcher.dispatch()``: точное попадание роутера (``source=router``,
   RAG не вызывается), промах → RAG (``source=rag``), промах при ``rag=None``.

Учебным здесь является только ``ToyKeywordEmbedder``: он отмечает наличие
четырёх подстрок и не моделирует смысловую близость. Его ``confidence``
иллюстрирует интерфейс и не свидетельствует о качестве semantic search.
Реальный эмбеддер подключает вызывающий код.

``sections`` (#146) показан отдельно: ``FormRagIndex.build()`` не принимает
этот параметр и индексирует полный фрагмент ``to_llm_prompt_fragment(ctx)``.
Отрицательное знание (#141), граница диагностики (#142) и бюджет (#125)
остаются внутри ``to_llm_prompt_fragment`` и примером не переписываются.

Запуск из корня репозитория:

python examples/form_rag_dispatch.py

Категория: самодостаточный синтетический пример (RAG-индекс форм и двухуровневая маршрутизация).
Входные данные: не требуются; синтетическая выгрузка и артефакты индекса
создаются во временном каталоге и удаляются за собой.
Ожидаемый результат: детерминированный вывод в stdout и строка
«Самопроверка: OK», RC=0; при расхождении с ожидаемыми значениями — RC=1.
Поведение без данных: запускается без аргументов, установленная
платформа 1С, реальная выгрузка, сеть и секреты не нужны.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from v8unpack_agent import (
    FormContext,
    FormDispatcher,
    FormEntry,
    FormRagIndex,
    FormRouter,
    FormScanIndex,
    RagLoadError,
    RouteResult,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_context import SECTION_FORM, SECTION_SUMMARY

# ---------------------------------------------------------------------------
# Учебный эмбеддер
# ---------------------------------------------------------------------------


class ToyKeywordEmbedder:
    """УЧЕБНЫЙ детерминированный эмбеддер — не модель смыслового поиска.

    Вектор: по одной координате на подстроку из ``VOCABULARY`` (1.0, если
    подстрока встречается в тексте без учёта регистра) плюс постоянная
    координата 1.0, исключающая нулевую норму. Сети, ключей и состояния
    провайдера нет. Счётчик ``calls`` и список ``texts`` нужны только для
    наглядности: по ним видно, когда ``FormRagIndex`` обращался к эмбеддеру.
    """

    VOCABULARY = ("заказ", "контрагент", "склад", "печать")

    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    def __call__(self, text: str) -> list[float]:
        self.calls += 1
        self.texts.append(text)
        lowered = text.lower()
        return [1.0 if word in lowered else 0.0 for word in self.VOCABULARY] + [1.0]


# ---------------------------------------------------------------------------
# Синтетическая выгрузка
# ---------------------------------------------------------------------------

#: (object_type, container_name, object_name, form_name) — имена придуманы.
FORMS = (
    ("Document", "DocumentForm", "ЗаказКлиента", "ФормаЗаказа"),
    ("Catalog", "CatalogForm", "Контрагенты", "ФормаКонтрагента"),
    ("Catalog", "CatalogForm", "Склады", "ФормаСклада"),
)

BSL_TEXT = "&НаСервере\nПроцедура ПриСозданииНаСервере()\nКонецПроцедуры\n"

ELEM_PAYLOAD = {
    "tree": [{"name": "Таблица", "type": "Table", "ПутьКДанным": "Объект.Строки"}],
    "data": {"-pages-": ["Страница1"], "Страница1/Таблица": {"id": 1}},
    "props": [{"name": "Реквизит", "type": "String"}],
}

EXACT_QUERY = "ФормаЗаказа"
RAG_QUERY = "печать списка заказов"
UNRELATED_QUERY = "совершенно другой запрос"

#: Значения, которые обязан дать учебный эмбеддер (cosine, round 6):
#: 2/sqrt(6), 1/sqrt(6) и 1/sqrt(2). Сверяются самопроверкой.
EXPECTED_RAG = (
    ("Document/ЗаказКлиента/ФормаЗаказа", 0.816497),
    ("Catalog/Контрагенты/ФормаКонтрагента", 0.408248),
    ("Catalog/Склады/ФормаСклада", 0.408248),
)
EXPECTED_UNRELATED = (
    ("Catalog/Контрагенты/ФормаКонтрагента", 0.707107),
    ("Catalog/Склады/ФормаСклада", 0.707107),
    ("Document/ЗаказКлиента/ФормаЗаказа", 0.707107),
)

_FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    """Зафиксировать нарушение ожидаемого поведения для итогового RC."""
    if not condition:
        _FAILURES.append(label)


def make_form(
    root: Path, object_type: str, container: str, object_name: str, form_name: str
) -> FormEntry:
    """Создать каталог формы с BSL и elem.json и вернуть карточку указателей."""
    relative = Path(object_type) / object_name / container / form_name
    form_dir = root / relative
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{container}.obj.bsl").write_text(BSL_TEXT, encoding="utf-8")
    (form_dir / f"{container}.elem.json").write_text(
        json.dumps(ELEM_PAYLOAD, ensure_ascii=False), encoding="utf-8"
    )
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container,
        form_name=form_name,
        form_path=form_dir,
        bsl_path=form_dir / f"{container}.obj.bsl",
        json_path=form_dir / f"{container}.json",
        bsl_sha256="a" * 64,
        elem_sha256="b" * 64,
        elem_json_path=relative / f"{container}.elem.json",
    )


def form_ref(entry: FormEntry) -> str:
    return f"{entry.object_type}/{entry.object_name}/{entry.form_name}"


def describe(results: list[RouteResult]) -> list[tuple[str, float]]:
    return [(form_ref(r.matched[0]), r.confidence) for r in results if r.matched]


def print_results(results: list[RouteResult]) -> None:
    print(f"  результатов             : {len(results)}")
    for position, result in enumerate(results, start=1):
        matched = ", ".join(form_ref(e) for e in result.matched) or "—"
        print(
            f"  #{position} source={result.source:<6} "
            f"confidence={result.confidence:.6f} matched=[{matched}]"
        )
        for warning in result.warnings:
            print(f"     warning: {warning}")


# ---------------------------------------------------------------------------
# Сценарий
# ---------------------------------------------------------------------------


def run(base: Path) -> None:
    export_root = base / "export"
    artifacts = base / "artifacts"
    export_root.mkdir()
    artifacts.mkdir()

    entries = [make_form(export_root, *spec) for spec in FORMS]
    contexts: list[FormContext] = [build_form_context(e, export_root) for e in entries]
    scan_index = FormScanIndex(
        forms=entries, total=len(entries), scanned_at="synthetic", scan_root=export_root
    )
    router_index = scan_index.save(artifacts / "forms_scan_index.json")

    print("1. FormContext → FormRagIndex.build(contexts, embedder)")
    print("-" * 72)
    embedder = ToyKeywordEmbedder()
    index = FormRagIndex(scan_index)
    index.build(contexts, embedder)
    build_texts = list(embedder.texts)
    full = [to_llm_prompt_fragment(c) for c in contexts]
    short = [
        to_llm_prompt_fragment(c, sections=(SECTION_FORM, SECTION_SUMMARY))
        for c in contexts
    ]
    no_abs = all(
        str(export_root) not in t and export_root.as_posix() not in t
        for t in build_texts
    )
    print(f"  форм в FormScanIndex    : {len(scan_index.forms)}")
    print(f"  вызовов эмбеддера       : {embedder.calls}")
    print(f"  build() получил полный фрагмент to_llm_prompt_fragment(ctx): {build_texts == full}")
    print(f"  в текстах нет абсолютного пути выгрузки (#142): {no_abs}")
    check(embedder.calls == len(FORMS), "build: число вызовов эмбеддера")
    check(build_texts == full, "build: полный фрагмент")
    check(no_abs, "build: абсолютный путь в тексте")

    print("\n2. Сокращённый фрагмент form + summary (#146) — отдельно от build()")
    print("-" * 72)
    no_bsl = all("## BSL" not in s.splitlines() for s in short)
    print(f"  начинается с # FORM     : {all(s.startswith('# FORM ') for s in short)}")
    print(f"  без блока ## BSL        : {no_bsl}")
    print(f"  короче полного          : {all(len(s) < len(f) for s, f in zip(short, full))}")
    print(f"  build() индексировал его: {build_texts == short}")
    print("  build() не принимает sections; переход на сокращённый состав —")
    print("  отдельное решение о политике пересборки индекса.")
    check(no_bsl and build_texts != short, "sections: отличие от build()")

    budget = ToyKeywordEmbedder()
    FormRagIndex(scan_index).build(contexts, budget, max_chars=200)
    in_budget = all(len(t) <= 200 for t in budget.texts)
    print(f"  build(max_chars=200) передал бюджет #125: {in_budget}")
    check(in_budget, "budget: max_chars")

    print("\n3. save(index_dir) → load(index_dir, embedder)")
    print("-" * 72)
    rag_dir = artifacts / "rag"
    index.save(rag_dir)
    meta_text = (rag_dir / "rag_meta.json").read_text(encoding="utf-8")
    meta = json.loads(meta_text)
    restored = FormRagIndex(scan_index)
    calls_before_load = embedder.calls
    restored.load(rag_dir, embedder)
    load_called = embedder.calls != calls_before_load
    same = describe(restored.query(RAG_QUERY, top_k=3)) == describe(
        index.query(RAG_QUERY, top_k=3)
    )
    outside = not rag_dir.resolve().is_relative_to(export_root.resolve())
    print(f"  файлы                   : {sorted(p.name for p in rag_dir.iterdir())}")
    print(f"  поля meta               : {sorted(meta)}")
    print(f"  schema/count/dimension  : {meta['schema_version']}/{meta['count']}/{meta['dimension']}")
    print(f"  путей в meta нет        : {str(base) not in meta_text and base.as_posix() not in meta_text}")
    print(f"  артефакты вне выгрузки  : {outside}")
    print(f"  load() вызвал эмбеддер  : {load_called}")
    print(f"  выдача совпала после load: {same}")
    try:
        FormRagIndex(scan_index).load(rag_dir, cast(Any, None))
        refused = "нет"
    except RagLoadError as exc:
        refused = f"{type(exc).__name__}: {exc}"
    print(f"  load() без эмбеддера    : {refused}")
    check(same and outside, "persistence: round-trip")
    check(not load_called, "persistence: load() не вызывает эмбеддер")
    check(refused.startswith("RagLoadError"), "persistence: load без эмбеддера")

    router = FormRouter(router_index)
    dispatcher = FormDispatcher(router, restored)
    no_rag = FormDispatcher(router, rag=None)

    print(f"\n4. Точное попадание роутера: {EXACT_QUERY!r}")
    print("-" * 72)
    calls = embedder.calls
    exact = dispatcher.dispatch(EXACT_QUERY)
    print_results(exact)
    print(f"  RAG вызывался           : {embedder.calls != calls}")
    check(
        [r.source for r in exact] == ["router"]
        and describe(exact) == [("Document/ЗаказКлиента/ФормаЗаказа", 1.0)]
        and embedder.calls == calls,
        "dispatch: router hit",
    )

    print(f"\n5. Промах роутера → RAG: {RAG_QUERY!r}")
    print("-" * 72)
    calls = embedder.calls
    rag_hit = dispatcher.dispatch(RAG_QUERY, top_k=3)
    print_results(rag_hit)
    print(f"  вызовов эмбеддера       : {embedder.calls - calls}")
    check(
        all(r.source == "rag" for r in rag_hit)
        and describe(rag_hit) == list(EXPECTED_RAG)
        and embedder.calls - calls == 1,
        "dispatch: rag hit",
    )

    print(f"\n6. Тот же промах при rag=None: {RAG_QUERY!r}")
    print("-" * 72)
    miss = no_rag.dispatch(RAG_QUERY)
    print_results(miss)
    check(
        len(miss) == 1
        and miss[0].source == "router"
        and miss[0].matched == []
        and miss[0].confidence == 0.0,
        "dispatch: rag=None",
    )

    print(f"\n7. Ограничение: RAG всегда возвращает ближайших: {UNRELATED_QUERY!r}")
    print("-" * 72)
    unrelated = dispatcher.dispatch(UNRELATED_QUERY, top_k=3)
    print_results(unrelated)
    print("  одинаковый confidence — признак отсутствия сигнала у эмбеддера,")
    print("  а не найденное совпадение; порог отсечения выбирает потребитель.")
    check(describe(unrelated) == list(EXPECTED_UNRELATED), "limit: unrelated")


def main() -> int:
    print("Учебный пример #312: FormContext → FormRagIndex → FormDispatcher")
    print("ToyKeywordEmbedder — учебный эмбеддер, а не модель смыслового поиска;")
    print("его confidence не говорит о качестве настоящего semantic search.")
    print("=" * 72)
    with tempfile.TemporaryDirectory(prefix="form_rag_dispatch_example_") as tmp:
        base = Path(tmp).resolve()
        run(base)
    print(f"\nВременный каталог удалён: {not base.exists()}")
    check(not base.exists(), "cleanup")
    if _FAILURES:
        print("Самопроверка: FAIL — " + "; ".join(_FAILURES))
        return 1
    print("Самопроверка: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
