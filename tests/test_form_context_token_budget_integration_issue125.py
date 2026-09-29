"""Issue #125: токенный бюджет на настоящем символьном пути.

Без monkeypatch: используется реальная сборка фрагмента с отрицательным
знанием #141 и санитизацией #142. Контексты синтетические и обезличенные;
canary-пути собираются из сегментов, разделитель NT задан кодом символа.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from v8unpack_agent.form_context import (
    BSL_MARKER,
    DATA_PATH_LINE_PREFIX,
    DATA_PATH_MARKERS,
    SUMMARY_MARKER,
    FormContext,
    _data_path_status_line,
    _to_llm_prompt_fragment_chars,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary

SEP_NT = chr(92)
TAIL = ["private-root-125", "a", "b", "c", "d", "Form.json"]
CANARIES = (
    "/" + "/".join(["home", "canary-user-125", *TAIL]),
    SEP_NT.join(["C:", "Users", "canary-user-125", *TAIL]),
    SEP_NT * 2 + SEP_NT.join(["canary-host-125", "canary-share-125", *TAIL]),
)
LOCAL_MARKERS = (
    "canary-user-125",
    "canary-host-125",
    "canary-share-125",
    "private-root-125",
)
BSL_TEXT = (
    "&НаКлиенте\n"
    "Процедура ПриОткрытии(Отказ)\n"
    "    Сообщить(\"demo\");\n"
    "КонецПроцедуры\n"
)
BIG = 10**9


def _context() -> FormContext:
    entries = [
        {
            "scope": "element",
            "element": f"Поле{index}",
            "data_path": None,
            "status": "unresolved",
            "reason": "binding_not_proven",
        }
        for index in range(3)
    ] + [
        {
            "scope": "form",
            "element": None,
            "data_path": None,
            "status": "unknown_layout",
            "reason": "layout_not_recognized",
        }
    ]
    return FormContext(
        form_name="ФормаЭлемента",
        container_name="CatalogForm",
        object_type="Catalog",
        object_name="Справочник1",
        bsl_text=BSL_TEXT,
        summary=FormSummary(
            warnings=[f"Не удалось прочитать {canary}" for canary in CANARIES]
        ),
        metadata={},
        unresolved_data_paths=entries,
    )


def by_chars(text: str) -> int:
    return len(text)


def cyr_heavy(text: str) -> int:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return cyr * 2 + (len(text) - cyr + 3) // 4


COUNTERS: tuple[Callable[[str], int], ...] = (by_chars, cyr_heavy)


def _status_lines(context: FormContext) -> set[str]:
    return {_data_path_status_line(entry) for entry in context.unresolved_data_paths}


def _assert_integrity(result: str, full: str, status_lines: set[str]) -> None:
    assert full.startswith(result)
    assert result in ("", full) or result.endswith("\n")
    markers = tuple(DATA_PATH_MARKERS.values())
    for line in result.split("\n"):
        if line.startswith(DATA_PATH_LINE_PREFIX) or any(m in line for m in markers):
            assert line in status_lines, line
    for marker in LOCAL_MARKERS:
        assert marker not in result
    if BSL_MARKER in result:
        assert result.index(SUMMARY_MARKER) < result.index(BSL_MARKER)


def test_real_legacy_is_bit_for_bit() -> None:
    context = _context()
    full = _to_llm_prompt_fragment_chars(context)
    assert to_llm_prompt_fragment(context) == full
    for limit in (-2, -1, 0, *range(1, len(full) + 2)):
        assert to_llm_prompt_fragment(context, limit) == (
            _to_llm_prompt_fragment_chars(context, limit)
        )


def test_real_full_fragment_is_sanitized_and_has_status_lines() -> None:
    context = _context()
    full = to_llm_prompt_fragment(context)
    for marker in LOCAL_MARKERS:
        assert marker not in full
    lines = full.split("\n")
    for status_line in _status_lines(context):
        assert status_line in lines
    assert full.index(SUMMARY_MARKER) < full.index(BSL_MARKER)
    assert all(entry["data_path"] is None for entry in context.unresolved_data_paths)


@pytest.mark.parametrize("counter", COUNTERS)
def test_real_token_budget_holds_for_every_budget(
    counter: Callable[[str], int],
) -> None:
    context = _context()
    full = to_llm_prompt_fragment(context)
    status_lines = _status_lines(context)
    for budget in range(1, counter(full) + 2):
        result = to_llm_prompt_fragment(
            context, max_tokens=budget, count_tokens=counter
        )
        assert counter(result) <= budget
        _assert_integrity(result, full, status_lines)
    assert to_llm_prompt_fragment(
        context, max_tokens=counter(full), count_tokens=counter
    ) == full


def test_real_char_limit_in_token_mode_keeps_lines_whole() -> None:
    context = _context()
    full = to_llm_prompt_fragment(context)
    status_lines = _status_lines(context)
    for limit in range(1, len(full) + 2):
        result = to_llm_prompt_fragment(
            context, limit, max_tokens=BIG, count_tokens=by_chars
        )
        assert len(result) <= limit
        _assert_integrity(result, full, status_lines)


@pytest.mark.parametrize("max_chars", [60, 200, 400, 800, -1])
@pytest.mark.parametrize("max_tokens", [10, 60, 150, 400, BIG])
def test_real_both_budgets_hold(max_chars: int, max_tokens: int) -> None:
    context = _context()
    full = to_llm_prompt_fragment(context)
    for counter in COUNTERS:
        result = to_llm_prompt_fragment(
            context, max_chars, max_tokens=max_tokens, count_tokens=counter
        )
        if max_chars >= 0:
            assert len(result) <= max_chars
        assert counter(result) <= max_tokens
        _assert_integrity(result, full, _status_lines(context))


@pytest.mark.parametrize("max_chars", [0, -2])
def test_real_empty_char_budget_in_token_mode(max_chars: int) -> None:
    assert to_llm_prompt_fragment(
        _context(), max_chars, max_tokens=BIG, count_tokens=by_chars
    ) == ""


def test_real_counter_failure_falls_back_to_whole_lines() -> None:
    context = _context()
    full = to_llm_prompt_fragment(context)

    def boom(text: str) -> int:
        raise RuntimeError(CANARIES[0])

    for limit in (50, 300, len(full)):
        result = to_llm_prompt_fragment(
            context, limit, max_tokens=5, count_tokens=boom
        )
        assert len(result) <= limit
        _assert_integrity(result, full, _status_lines(context))


def test_real_token_mode_is_deterministic() -> None:
    results = {
        to_llm_prompt_fragment(
            _context(), 500, max_tokens=120, count_tokens=cyr_heavy
        )
        for _ in range(3)
    }
    assert len(results) == 1
