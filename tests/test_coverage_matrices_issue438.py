"""Матрицы покрытия на финальном коде эпика #431 (issue #438)."""

from __future__ import annotations

import re
from pathlib import Path

from v8unpack_agent.extension_modules import EXTENSION_MODULE_FILES
from v8unpack_agent.metadata_modules import METADATA_OBJECT_MODULE_LAYOUTS
from v8unpack_agent.record_set_modules import RECORD_SET_MODULE_FILES
from v8unpack_agent.search_contract import LINK_BASES

ROOT = Path(__file__).resolve().parents[1]
MATRIX = (ROOT / "docs" / "bsl_coverage_matrix.md").read_text(encoding="utf-8")
SEARCH = (ROOT / "docs" / "search_coverage.md").read_text(encoding="utf-8")


def _section(text: str, title: str) -> str:
    start = text.index(title)
    end = text.find("\n## ", start + len(title))
    return text[start:] if end == -1 else text[start:end]


def test_matrix_lists_every_supported_layout_after_epic() -> None:
    for metadata_type, file_name in RECORD_SET_MODULE_FILES.items():
        assert f"{metadata_type}/{{Name}}/{file_name}" in MATRIX
    for metadata_type in ("ChartOfAccounts", "ChartOfCalculationTypes"):
        assert "object" in METADATA_OBJECT_MODULE_LAYOUTS[metadata_type]
        assert f"`{metadata_type}`" in MATRIX
    for file_name in EXTENSION_MODULE_FILES.values():
        assert file_name in MATRIX


def test_remainder_has_reason_for_every_row() -> None:
    remainder = _section(MATRIX, "## Остаток: необработанные BSL-файлы")
    rows = [line for line in remainder.splitlines() if line.startswith("| ") and "---" not in line]
    assert rows[0].startswith("| Шаблон")
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 4 and all(cells), row
    for gone in ("Sequences.obj.bsl` |", "ChartOfAccounts.obj.bsl` |", "AccountingRegister.obj.bsl` |"):
        assert gone not in remainder


def test_final_measurement_is_balanced_and_read_only() -> None:
    final = _section(MATRIX, "## Замер на финальном коде эпика #431 (#438)")
    assert re.search(r"sha256 `[0-9a-f]{64}`", final)
    row = next(line for line in final.splitlines() if line.startswith("| без обработчика (стало)"))
    assert [c.strip() for c in row.strip("|").split("|")][1:] == ["0", "0", "0", "0"]
    row = next(line for line in final.splitlines() if line.startswith("| оригинал не изменился"))
    assert [c.strip() for c in row.strip("|").split("|")][1:] == ["да"] * 4
    totals = next(line for line in final.splitlines() if line.startswith("| BSL-файлов"))
    handlers = next(line for line in final.splitlines() if line.startswith("| индекс модулей /"))
    for total, split in zip(
        [c.strip() for c in totals.strip("|").split("|")][1:],
        [c.strip() for c in handlers.strip("|").split("|")][1:],
    ):
        assert int(total) == sum(int(x) for x in split.split("/"))


def test_external_bsl_only_remainder_is_documented() -> None:
    remainder = _section(MATRIX, "## Остаток: необработанные BSL-файлы")
    assert "без `ExternalDataProcessor.json`" in remainder
    final = _section(MATRIX, "## Замер на финальном коде эпика #431 (#438)")
    assert "coverage_issue438_v2" in final and "466" in final


def test_search_coverage_reflects_epic() -> None:
    assert "layout" in LINK_BASES
    assert "## Состояние на финальном коде эпика #431 (#438)" in SEARCH
    for marker in ("`layout` (#436)", "extension:<имя>/", "#437"):
        assert marker in SEARCH
    assert "`unconfirmed`; в карточки не входит" not in SEARCH
