"""Общий поиск по формам и модулям реальной выгрузки (issue #404).

Показывает путь, который проходит пользователь после установки пакета:
каталог выгрузки → поисковый корпус (формы, модули #201, карточки
владельцев) → фрагменты → смысловой снимок → точный и смысловой поиск →
контекст под бюджет. Печатает только обезличенные агрегаты: имена
объектов, пути, идентификаторы и BSL-текст не выводятся.

Запуск:

python examples/search_export.py /path/to/cf_export
python examples/search_export.py /path/to/external_export --mode external

Эмбеддер — встроенный демонстрационный ``hashed-bow-demo``: пример
проверяет механику поиска, а не качество ранжирования. Снимок строится во
временном каталоге вне выгрузки и удаляется. Выгрузка только читается:
до и после прогона сравниваются размер и время изменения каждого файла.

Категория: пример на реальной выгрузке.
Входные данные: EXPORT_ROOT — корень уже распакованной выгрузки
(конфигурация или, с ``--mode external``, внешние обработки и отчёты).
Ожидаемый результат: обезличенный агрегат в stdout и RC=0; RC=1 —
нарушена проверка механики поиска или выгрузка изменилась.
Поведение без данных: штатная ошибка argparse (RC=2) — это ожидаемое
поведение, а не дефект; в автоматический прогон файл не входит.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import SearchFilters
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import load_index

SAMPLE = 10
TOP_K = 5
BUDGET = 400


def _state(root: Path) -> dict[str, tuple[int, int]]:
    """Размер и время изменения каждого файла выгрузки (без чтения содержимого)."""
    state: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            path = Path(dirpath) / name
            info = path.lstat()
            state[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
    return state


def _sample(fragmentation: FragmentationResult) -> list[Any]:
    """Детерминированная выборка артефактов: порядок по sha256 идентификатора."""
    seen: dict[str, Any] = {}
    for fragment in fragmentation.fragments:
        seen.setdefault(fragment.document.artifact.artifact_id, fragment.document)
    ordered = sorted(
        seen.values(),
        key=lambda doc: hashlib.sha256(doc.artifact.artifact_id.encode("utf-8")).hexdigest(),
    )
    return ordered[:SAMPLE]


def _name_of(artifact: Any) -> str | None:
    if artifact.form_key is not None:
        return str(artifact.form_key[3])
    if artifact.owner is not None and artifact.owner.owner_name:
        return str(artifact.owner.owner_name)
    return None


def build_report(root: Path, mode: str) -> dict[str, Any]:
    """Построить обезличенный отчёт одного прогона."""
    corpus = collect_search_corpus(root, mode=mode)  # type: ignore[arg-type]
    items = corpus.report.items
    corpus_counts = Counter(
        f"{item.artifact_kind}:{'in' if item.included else 'out'}" for item in items
    )
    excluded = Counter(str(item.reason) for item in items if not item.included)
    module_kinds = Counter(
        str(item.artifact_id).rsplit(":", 1)[-1]
        for item in items
        if item.artifact_kind == "module" and item.included and item.artifact_id
    )
    fragmentation = fragment_corpus(corpus)
    embedder = HashedBagOfWordsDemoEmbedder()

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.json"
        build_search_snapshot(fragmentation, embedder, snapshot)
        snapshot_bytes = snapshot.stat().st_size
        index = load_index(snapshot)

    exact_id = Counter({"ok": 0, "other": 0})
    exact_name = Counter({"exact": 0, "ambiguous_with_target": 0, "other": 0})
    semantic = Counter({"ok": 0, "unique": 0, "within_top_k": 0, "forms_only_ok": 0, "other": 0})
    budget: Counter[str] = Counter()
    sample = _sample(fragmentation)
    for document in sample:
        artifact = document.artifact
        context = search_with_context(
            fragmentation, artifact.artifact_id, search_mode="exact", max_chars=-1
        )
        ids = [hit.artifact.artifact_id for hit in context.dispatch.result.hits]
        exact_id["ok" if ids == [artifact.artifact_id] else "other"] += 1

        name = _name_of(artifact)
        if name and artifact.artifact_kind != "owner":
            context = search_with_context(fragmentation, name, search_mode="exact", max_chars=-1)
            ids = [hit.artifact.artifact_id for hit in context.dispatch.result.hits]
            status = context.dispatch.result.status
            if status == "exact" and artifact.artifact_id in ids:
                exact_name["exact"] += 1
            elif status == "ambiguous" and artifact.artifact_id in ids:
                exact_name["ambiguous_with_target"] += 1
            else:
                exact_name["other"] += 1

        words = [word for word in document.text.split() if len(word) > 3][:6]
        if words:
            context = search_with_context(
                fragmentation, " ".join(words), search_mode="semantic",
                index=index, embedder=embedder, top_k=TOP_K, max_chars=-1,
            )
            ids = [hit.artifact.artifact_id for hit in context.dispatch.result.hits]
            if context.dispatch.result.status == "semantic":
                semantic["ok"] += 1
                semantic["unique"] += int(len(ids) == len(set(ids)))
                semantic["within_top_k"] += int(0 < len(ids) <= TOP_K)
            else:
                semantic["other"] += 1
            forms = search_with_context(
                fragmentation, " ".join(words), search_mode="semantic",
                index=index, embedder=embedder, top_k=TOP_K, max_chars=-1,
                filters=SearchFilters(artifact_kinds=("form",)),
            )
            kinds = {hit.artifact.artifact_kind for hit in forms.dispatch.result.hits}
            semantic["forms_only_ok"] += int(kinds <= {"form"})

        limited = search_with_context(
            fragmentation, artifact.artifact_id, search_mode="exact", max_chars=BUDGET
        )
        budget[limited.status] += 1
        budget["within_budget"] += int(len(limited.to_dict(include_text=True)["text"]) <= BUDGET)

    return {
        "mode": mode,
        "corpus": dict(sorted(corpus_counts.items())),
        "excluded": dict(sorted(excluded.items())),
        "module_kinds": dict(sorted(module_kinds.items())),
        "links": {
            "confirmed": sum(link.confirmed for link in corpus.owner_links),
            "unconfirmed": sum(not link.confirmed for link in corpus.owner_links),
        },
        "fragments": len(fragmentation.fragments),
        "snapshot": {"entries": len(index.entries), "bytes": snapshot_bytes},
        "sample": len(sample),
        "exact_by_id": dict(exact_id),
        "exact_by_name": dict(exact_name),
        "semantic_demo": dict(semantic),
        "context_budget": {"max_chars": BUDGET, **dict(sorted(budget.items()))},
    }


def _checks_ok(report: dict[str, Any]) -> bool:
    semantic = report["semantic_demo"]
    return (
        report["exact_by_id"]["other"] == 0
        and semantic["other"] == 0
        and semantic["unique"] == semantic["ok"]
        and semantic["within_top_k"] == semantic["ok"]
        and semantic["forms_only_ok"] == semantic["ok"]
        and report["context_budget"]["within_budget"] == report["sample"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Обезличенный прогон общего поиска по выгрузке (#404)",
    )
    parser.add_argument("export_root", type=Path, help="корень распакованной выгрузки")
    parser.add_argument(
        "--mode", choices=("config", "external"), default="config",
        help="режим сканирования: конфигурация или внешние обработки и отчёты",
    )
    args = parser.parse_args(argv)
    root: Path = args.export_root
    if not root.is_dir():
        parser.error("export_root must be an existing directory")

    before = _state(root)
    report = build_report(root, args.mode)
    after = _state(root)
    unchanged = before == after
    report["export_unchanged"] = unchanged
    ok = unchanged and _checks_ok(report)
    report["checks"] = "ok" if ok else "failed"
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
