"""Guard-тест синхронизации таблицы REFERENCE_TYPE_PREFIXES с документацией.

Тест падает, если Markdown-таблица в docs/scan_forms.md расходится с фактическим
dict в v8unpack_agent/scan_forms.py: составом, значениями префиксов или порядком
строк. Ожидаемый dict в тесте не дублируется — источник истины один (issue #168).
"""

from __future__ import annotations

import re
from pathlib import Path

from v8unpack_agent.scan_forms import REFERENCE_TYPE_PREFIXES

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "scan_forms.md"

START = "<!-- reference-type-prefixes:start -->"
END = "<!-- reference-type-prefixes:end -->"

CELL = re.compile(r"^`([^`\s|]+)`$")
SEPARATOR = re.compile(r"^[\s|:-]+$")


def _read_doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _extract_block(text: str) -> list[str]:
    assert text.count(START) == 1, f"маркер {START} должен быть ровно один раз"
    assert text.count(END) == 1, f"маркер {END} должен быть ровно один раз"

    start = text.index(START)
    end = text.index(END)
    assert start < end, "начальный маркер должен располагаться раньше конечного"

    block = text[start + len(START) : end]
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    assert lines, "блок таблицы префиксов пуст"
    return lines


def _split_row(line: str) -> list[str]:
    assert line.startswith("|") and line.endswith("|"), (
        f"строка таблицы должна быть ограничена вертикальными чертами: {line!r}"
    )
    return [cell.strip() for cell in line.strip("|").split("|")]


def _cell(raw: str) -> str:
    match = CELL.match(raw)
    assert match, f"ячейка должна иметь вид `Value`: {raw!r}"
    return match.group(1)


def parse_markdown_table(text: str) -> dict[str, str]:
    lines = _extract_block(text)

    documented: dict[str, str] = {}
    for line in lines:
        cells = _split_row(line)
        assert len(cells) == 2, (
            f"строка должна содержать ровно два столбца, получено {len(cells)}: {line!r}"
        )
        if all(SEPARATOR.match(cell) for cell in cells):
            continue
        if not any(CELL.match(cell) for cell in cells):
            continue  # строка заголовка

        kind, prefix = _cell(cells[0]), _cell(cells[1])
        assert kind not in documented, f"вид продублирован в таблице: {kind}"
        documented[kind] = prefix

    assert documented, "в блоке маркеров не найдено ни одной строки данных"
    return documented


def test_markers_present_once_and_ordered() -> None:
    text = _read_doc()
    assert text.count(START) == 1
    assert text.count(END) == 1
    assert text.index(START) < text.index(END)


def test_documented_table_matches_reference_type_prefixes() -> None:
    documented = parse_markdown_table(_read_doc())
    assert documented == dict(sorted(REFERENCE_TYPE_PREFIXES.items()))


def test_documented_table_order_is_deterministic() -> None:
    documented = parse_markdown_table(_read_doc())
    assert list(documented) == sorted(REFERENCE_TYPE_PREFIXES)


def test_documented_cells_are_non_empty() -> None:
    documented = parse_markdown_table(_read_doc())
    assert all(kind and prefix for kind, prefix in documented.items())
