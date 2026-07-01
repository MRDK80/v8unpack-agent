"""
Agent routing: выбор форм из FormScanIndex по контексту запроса.
Чистая строковая маршрутизация — без LLM-вызовов.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


@dataclass
class RouteResult:
    matched: List[FormEntry]
    confidence: float          # 0.0–1.0
    warnings: List[str] = field(default_factory=list)


class FormRouter:
    """Маршрутизирует строковый запрос агента к FormEntry из FormScanIndex."""

    def __init__(self, index_path: Path) -> None:
        self._index_path = index_path
        self._entries: List[FormEntry] = self._load(index_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, query: str) -> RouteResult:
        """Вернуть FormEntry, соответствующие запросу.

        Предназначен для вызова из LLM-инструментального слоя: LLM извлекает
        сущность из пользовательского запроса и передаёт её сюда. Регистр
        не нормируется намеренно — LLM может вернуть разный регистр для одного
        объекта, поэтому сравнение case-insensitive на уровнях 2 и 3.

        Приоритет совпадений
        --------------------
        1. ``form_name`` — точное совпадение (conf=1.0)
        2. ``object_name`` — точное, case-insensitive (conf=0.9)
        3. ``object_type`` — частичное, case-insensitive (conf=0.4)
        """

        q = query.strip()
        if not q:
            return RouteResult(matched=[], confidence=0.0,
                               warnings=["Empty query"])

        q_lower = q.lower()

        # 1) Точное совпадение по form_name
        exact = [e for e in self._entries if e.form_name == q]
        if exact:
            return RouteResult(matched=exact, confidence=1.0)

        # 2) Точное совпадение по object_name (case-insensitive)
        by_obj = [e for e in self._entries
                  if e.object_name.lower() == q_lower]
        if by_obj:
            conf = round(0.5 + 0.4 * min(len(by_obj), 1), 2)
            return RouteResult(matched=by_obj, confidence=conf)

        # 3) Частичное совпадение по object_type (case-insensitive)
        by_type = [e for e in self._entries
                   if q_lower in e.object_type.lower()]
        if by_type:
            return RouteResult(matched=by_type, confidence=0.4)

        return RouteResult(matched=[], confidence=0.0,
                           warnings=[f"No match for query: {q!r}"])

    def reindex(self, changed_forms: List[FormEntry]) -> None:
        """Обновить записи без полного пересканирования.

        Составной ключ: (object_type, object_name, form_name).
        Новые добавляются, существующие заменяются, остальные не трогаются.
        """
        lookup = {
            (e.object_type, e.object_name, e.form_name): e
            for e in self._entries
        }
        for form in changed_forms:
            lookup[(form.object_type, form.object_name, form.form_name)] = form
        self._entries = list(lookup.values())
        self._save(self._index_path, self._entries)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load(index_path: Path) -> List[FormEntry]:
        if not index_path.exists():
            return []
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        entries = []
        for row in raw.get("forms", []):
            entries.append(FormEntry(
                object_type=row["object_type"],
                object_name=row["object_name"],
                container_name=row["container_name"],
                form_name=row["form_name"],
                form_path=Path(row["form_path"]),
                bsl_path=Path(row["bsl_path"]),
                json_path=Path(row["json_path"]),
                warnings=list(row.get("warnings", [])),
            ))
        return entries

    @staticmethod
    def _save(index_path: Path, entries: List[FormEntry]) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total": len(entries),
            "forms": [
                {
                    "object_type": e.object_type,
                    "object_name": e.object_name,
                    "container_name": e.container_name,
                    "form_name": e.form_name,
                    "form_path": e.form_path.as_posix(),
                    "bsl_path": e.bsl_path.as_posix(),
                    "json_path": e.json_path.as_posix(),
                    "warnings": e.warnings,
                }
                for e in entries
            ],
        }
        index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
