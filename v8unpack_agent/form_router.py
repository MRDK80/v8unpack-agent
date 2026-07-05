"""
Agent routing: выбор форм из FormScanIndex по контексту запроса.
Чистая строковая маршрутизация — без LLM-вызовов.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

from v8unpack_agent.drift_checker import _form_key


@dataclass
class RouteResult:
    matched: List[FormEntry]
    confidence: float          # 0.0–1.0
    warnings: List[str] = field(default_factory=list)


class FormRouter:
    """Маршрутизирует строковый запрос агента к FormEntry из FormScanIndex."""

    def __init__(self, index_path: Path) -> None:
        self._index_path = index_path
        self._index: FormScanIndex = self._load_index(index_path)
        self._entries: List[FormEntry] = self._index.forms

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, query: str) -> RouteResult:
        """Вернуть FormEntry, соответствующие запросу.

        Приоритет совпадений
        --------------------
        1. ``form_name`` — точное совпадение (conf=1.0)
        2. ``object_name`` — точное, case-insensitive (conf=0.9)
        3. ``object_type`` — частичное, case-insensitive (conf=0.4)

        Для форм внешних обработок (issue #25) ``object_name`` — имя обработки,
        поэтому маршрутизация работает без изменений логики.
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

        Составной ключ: (object_type, object_name, container_name, form_name).
        Новые добавляются, существующие заменяются, остальные не трогаются.
        Метаданные верхнего уровня (scanned_at, scan_warnings) сохраняются.
        """

        from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

        lookup = {
            _form_key(e.object_type, e.object_name, e.container_name, e.form_name): e
            for e in self._entries
        }
        for form in changed_forms:
            lookup[_form_key(
                form.object_type,
                form.object_name,
                form.container_name,
                form.form_name,
            )] = form
        self._entries = list(lookup.values())
        self._index = FormScanIndex(
            forms=self._entries,
            total=len(self._entries),
            scanned_at=self._index.scanned_at,
            scan_warnings=self._index.scan_warnings,
        )
        self._index.save(self._index_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_index(index_path: Path) -> FormScanIndex:
        """Загрузить FormScanIndex из JSON, сохраняя все поля схемы."""

        from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

        if not index_path.exists():
            return FormScanIndex()
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        entries = []
        for row in raw.get("forms", []):
            elem = row.get("form_elem_path")
            entries.append(FormEntry(
                object_type=row["object_type"],
                object_name=row["object_name"],
                container_name=row["container_name"],
                form_name=row["form_name"],
                form_path=Path(row["form_path"]),
                bsl_path=Path(row["bsl_path"]),
                json_path=Path(row["json_path"]),
                warnings=list(row.get("warnings", [])),
                bsl_mtime=float(row.get("bsl_mtime", 0.0)),
                form_elem_path=Path(elem) if elem else None,
            ))
        return FormScanIndex(
            forms=entries,
            total=raw.get("total", len(entries)),
            scanned_at=raw.get("scanned_at", ""),
            scan_warnings=list(raw.get("scan_warnings", [])),
        )

    @staticmethod
    def _load(index_path: Path) -> List[FormEntry]:
        return FormRouter._load_index(index_path).forms

    @staticmethod
    def _save(index_path: Path, entries: List[FormEntry]) -> None:
        """Сохранить список записей как FormScanIndex без метаданных (устар.)."""

        from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

        idx = FormScanIndex(forms=entries, total=len(entries))
        idx.save(index_path)
