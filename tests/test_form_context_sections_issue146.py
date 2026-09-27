"""Issue #146: выбор целых блоков в ``to_llm_prompt_fragment``.

Контексты синтетические и обезличенные, без сети и реальных выгрузок.
Canary-пути собираются из сегментов, разделитель NT задан кодом символа.
Эталон legacy-режима заморожен в ``_legacy_reference`` по состоянию
epic/81-rag-routing до #146 и не зависит от новой реализации.
"""

from __future__ import annotations

import inspect
import itertools
from collections.abc import Callable

import pytest

from v8unpack_agent._safe_paths import sanitize_diagnostic
from v8unpack_agent.form_context import (
    ALL_SECTIONS,
    BSL_MARKER,
    DATA_PATH_LINE_PREFIX,
    DATA_PATH_MARKERS,
    NO_OBJECT_PLACEHOLDER,
    OBJECT_ATTRIBUTES_MARKER,
    SECTION_BSL,
    SECTION_FORM,
    SECTION_OBJECT_ATTRIBUTES,
    SECTION_SUMMARY,
    SUMMARY_MARKER,
    FormContext,
    _data_path_status_line,
    _line_prefix,
    _split_complete_lines,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_summary import FormSummary, to_normalized_json

SEP_NT = chr(92)
TAIL = ["private-root-146", "a", "b", "c", "d", "Form.json"]
CANARIES = (
    "/" + "/".join(["home", "canary-user-146", *TAIL]),
    SEP_NT.join(["C:", "Users", "canary-user-146", *TAIL]),
    SEP_NT * 2 + SEP_NT.join(["canary-host-146", "canary-share-146", *TAIL]),
)
LOCAL_MARKERS = (
    "canary-user-146",
    "canary-host-146",
    "canary-share-146",
    "private-root-146",
)
BSL_TEXT = (
    "&НаКлиенте\n"
    "Процедура ПриОткрытии(Отказ)\n"
    "    Сообщить(\"demo\");\n"
    "КонецПроцедуры\n"
)
BIG = 10**9
OTHERS = (SECTION_SUMMARY, SECTION_OBJECT_ATTRIBUTES, SECTION_BSL)
MARKER_OF = {
    SECTION_SUMMARY: SUMMARY_MARKER,
    SECTION_OBJECT_ATTRIBUTES: OBJECT_ATTRIBUTES_MARKER,
    SECTION_BSL: BSL_MARKER,
}
ALLOWED = "form, summary, object_attributes, bsl"


def _context(*, statuses: int = 3) -> FormContext:
    entries = [
        {
            "scope": "element",
            "element": f"Поле{index}",
            "data_path": None,
            "status": "unresolved",
            "reason": "binding_not_proven",
        }
        for index in range(statuses)
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


def _legacy_reference(context: FormContext, max_chars: int = -1) -> str:
    """Замороженный символьный режим до #146 (независимый эталон)."""
    if max_chars == 0 or max_chars < -1:
        return ""
    header = sanitize_diagnostic("# FORM " + "/".join(
        part
        for part in (
            context.object_type,
            context.object_name,
            context.container_name,
            context.form_name,
        )
        if part
    ))
    summary_block = sanitize_diagnostic(to_normalized_json(context.summary))
    status_lines = [
        sanitize_diagnostic(_data_path_status_line(entry))
        for entry in context.unresolved_data_paths
    ]
    assert context.bsl_text is not None
    assert context.object_attributes is None
    fragment = "\n".join((
        header, SUMMARY_MARKER, summary_block, *status_lines,
        OBJECT_ATTRIBUTES_MARKER, NO_OBJECT_PLACEHOLDER, BSL_MARKER,
        context.bsl_text,
    ))
    if max_chars == -1:
        return fragment
    offset = len(header) + len(SUMMARY_MARKER) + len(summary_block) + 3
    for line in status_lines:
        end = offset + len(line)
        if offset < max_chars < end:
            return fragment[:offset]
        offset = end + 1
    return fragment[:max_chars]


def _blocks(context: FormContext) -> dict[str, str]:
    full = _legacy_reference(context)
    head, rest = full.split("\n" + SUMMARY_MARKER + "\n", 1)
    summary, rest = rest.split("\n" + OBJECT_ATTRIBUTES_MARKER + "\n", 1)
    attrs, bsl = rest.split("\n" + BSL_MARKER + "\n", 1)
    return {
        SECTION_FORM: head,
        SECTION_SUMMARY: SUMMARY_MARKER + "\n" + summary,
        SECTION_OBJECT_ATTRIBUTES: OBJECT_ATTRIBUTES_MARKER + "\n" + attrs,
        SECTION_BSL: BSL_MARKER + "\n" + bsl,
    }


def _expected(context: FormContext, chosen: tuple[str, ...]) -> str:
    blocks = _blocks(context)
    return "\n".join(blocks[name] for name in ALL_SECTIONS if name in chosen)


def _subsets() -> list[tuple[str, ...]]:
    result = []
    for size in range(len(OTHERS) + 1):
        for combo in itertools.combinations(OTHERS, size):
            result.append((SECTION_FORM, *combo))
    return result


SUBSETS = _subsets()


def by_chars(text: str) -> int:
    return len(text)


def cyr_heavy(text: str) -> int:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return cyr * 2 + (len(text) - cyr + 3) // 4


COUNTERS: tuple[Callable[[str], int], ...] = (by_chars, cyr_heavy)


def _status_lines(context: FormContext) -> set[str]:
    return {
        sanitize_diagnostic(_data_path_status_line(entry))
        for entry in context.unresolved_data_paths
    }


def _assert_negative_knowledge(
    result: str, context: FormContext, summary_on: bool
) -> None:
    markers = tuple(DATA_PATH_MARKERS.values())
    if not summary_on:
        assert SUMMARY_MARKER not in result
        assert DATA_PATH_LINE_PREFIX not in result
        assert not any(marker in result for marker in markers)
        return
    whole = _status_lines(context)
    for line in result.split("\n"):
        if line.startswith(DATA_PATH_LINE_PREFIX) or any(m in line for m in markers):
            assert line in whole, line


def _assert_clean(result: str) -> None:
    for marker in LOCAL_MARKERS:
        assert marker not in result


# --- публичный контракт ---


def test_section_constants_are_canonical() -> None:
    assert (SECTION_FORM, SECTION_SUMMARY, SECTION_OBJECT_ATTRIBUTES, SECTION_BSL) == (
        "form", "summary", "object_attributes", "bsl",
    )
    assert ALL_SECTIONS == ("form", "summary", "object_attributes", "bsl")
    assert isinstance(ALL_SECTIONS, tuple)


def test_signature_adds_keyword_only_sections() -> None:
    params = inspect.signature(to_llm_prompt_fragment).parameters
    assert list(params)[:2] == ["context", "max_chars"]
    assert params["max_chars"].default == -1
    for name in ("max_tokens", "count_tokens", "sections"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is None


# --- legacy ---


def test_default_is_bit_for_bit_legacy() -> None:
    context = _context()
    full = _legacy_reference(context)
    assert to_llm_prompt_fragment(context) == full
    for limit in (-2, -1, 0, *range(1, len(full) + 2)):
        expected = _legacy_reference(context, limit)
        assert to_llm_prompt_fragment(context, limit) == expected
        assert to_llm_prompt_fragment(context, limit, sections=None) == expected


@pytest.mark.parametrize("counter", COUNTERS)
@pytest.mark.parametrize("max_chars", [-1, 80, 300])
@pytest.mark.parametrize("max_tokens", [5, 60, BIG])
def test_default_token_mode_matches_full_set(
    counter: Callable[[str], int], max_chars: int, max_tokens: int
) -> None:
    context = _context()
    legacy = to_llm_prompt_fragment(
        context, max_chars, max_tokens=max_tokens, count_tokens=counter
    )
    full_set = to_llm_prompt_fragment(
        context, max_chars, max_tokens=max_tokens, count_tokens=counter,
        sections=ALL_SECTIONS,
    )
    assert legacy == full_set


def test_all_sections_equals_legacy_for_every_limit() -> None:
    context = _context()
    full = _legacy_reference(context)
    for limit in (-1, 0, *range(1, len(full) + 2)):
        assert to_llm_prompt_fragment(context, limit, sections=ALL_SECTIONS) == (
            _legacy_reference(context, limit)
        )


# --- состав блоков ---


@pytest.mark.parametrize("chosen", SUBSETS)
def test_every_subset_has_only_chosen_blocks(chosen: tuple[str, ...]) -> None:
    context = _context()
    result = to_llm_prompt_fragment(context, sections=chosen)
    assert result == _expected(context, chosen)
    assert result.startswith("# FORM ")
    lines = result.split("\n")
    for name, marker in MARKER_OF.items():
        assert (marker in lines) is (name in chosen)
    assert "\n\n" not in result.replace(BSL_TEXT, "")


@pytest.mark.parametrize("chosen", SUBSETS)
def test_order_and_duplicates_do_not_matter(chosen: tuple[str, ...]) -> None:
    context = _context()
    expected = _expected(context, chosen)
    for perm in itertools.permutations(chosen):
        assert to_llm_prompt_fragment(context, sections=perm) == expected
        assert to_llm_prompt_fragment(context, sections=list(perm) * 2) == expected
    assert to_llm_prompt_fragment(context, sections=set(chosen)) == expected
    assert to_llm_prompt_fragment(context, sections=iter(chosen)) == expected


def test_summary_before_bsl_when_both_chosen() -> None:
    result = to_llm_prompt_fragment(
        _context(), sections=(SECTION_BSL, SECTION_SUMMARY, SECTION_FORM)
    )
    assert result.index(SUMMARY_MARKER) < result.index(BSL_MARKER)


# --- ошибки и граничные наборы ---


def test_unknown_section_is_value_error_with_stable_message() -> None:
    with pytest.raises(ValueError) as info:
        to_llm_prompt_fragment(_context(), sections=(SECTION_FORM, "sumary"))
    assert str(info.value) == (
        "to_llm_prompt_fragment: неизвестные секции: 'sumary'; "
        f"допустимые: {ALLOWED}"
    )


def test_several_unknown_sections_are_sorted() -> None:
    with pytest.raises(ValueError) as info:
        to_llm_prompt_fragment(_context(), sections=["zeta", "form", "alpha", "zeta"])
    assert str(info.value) == (
        "to_llm_prompt_fragment: неизвестные секции: 'alpha', 'zeta'; "
        f"допустимые: {ALLOWED}"
    )


def test_unknown_section_message_passes_sanitizer() -> None:
    with pytest.raises(ValueError) as info:
        to_llm_prompt_fragment(_context(), sections=(SECTION_FORM, CANARIES[0]))
    _assert_clean(str(info.value))


def test_plain_string_is_rejected() -> None:
    with pytest.raises(TypeError):
        to_llm_prompt_fragment(_context(), sections="form")


def test_non_string_name_is_rejected() -> None:
    with pytest.raises(TypeError):
        to_llm_prompt_fragment(_context(), sections=(SECTION_FORM, 1))


@pytest.mark.parametrize(
    "chosen",
    [
        (SECTION_SUMMARY,),
        (SECTION_BSL,),
        (SECTION_SUMMARY, SECTION_OBJECT_ATTRIBUTES, SECTION_BSL),
    ],
)
def test_non_empty_set_without_form_is_value_error(chosen: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="должен включать 'form'"):
        to_llm_prompt_fragment(_context(), sections=chosen)


@pytest.mark.parametrize("empty", [(), [], set(), frozenset()])
def test_empty_sections_give_empty_string(empty: object) -> None:
    context = _context()
    assert to_llm_prompt_fragment(context, sections=empty) == ""
    assert to_llm_prompt_fragment(context, 500, sections=empty) == ""
    assert to_llm_prompt_fragment(
        context, max_tokens=BIG, count_tokens=by_chars, sections=empty
    ) == ""


def test_empty_sections_still_validate_token_pairing() -> None:
    with pytest.raises(ValueError, match="только вместе"):
        to_llm_prompt_fragment(_context(), max_tokens=10, sections=())


# --- бюджеты после фильтрации ---


@pytest.mark.parametrize("chosen", SUBSETS)
def test_char_budget_applies_to_filtered_text(chosen: tuple[str, ...]) -> None:
    context = _context()
    filtered = _expected(context, chosen)
    summary_on = SECTION_SUMMARY in chosen
    for limit in range(1, len(filtered) + 2):
        result = to_llm_prompt_fragment(context, limit, sections=chosen)
        assert len(result) <= limit
        assert filtered.startswith(result)
        _assert_negative_knowledge(result, context, summary_on)
    assert to_llm_prompt_fragment(context, len(filtered), sections=chosen) == filtered


def test_filter_frees_budget_for_bsl() -> None:
    context = _context()
    chosen = (SECTION_FORM, SECTION_BSL)
    filtered = _expected(context, chosen)
    limited = to_llm_prompt_fragment(context, len(filtered), sections=chosen)
    assert limited == filtered
    assert BSL_TEXT in limited
    assert BSL_TEXT not in to_llm_prompt_fragment(context, len(filtered))


@pytest.mark.parametrize("chosen", SUBSETS)
@pytest.mark.parametrize("counter", COUNTERS)
def test_token_budget_applies_to_filtered_text(
    chosen: tuple[str, ...], counter: Callable[[str], int]
) -> None:
    context = _context()
    filtered = _expected(context, chosen)
    summary_on = SECTION_SUMMARY in chosen
    for budget in range(1, counter(filtered) + 2):
        result = to_llm_prompt_fragment(
            context, max_tokens=budget, count_tokens=counter, sections=chosen
        )
        assert counter(result) <= budget
        assert filtered.startswith(result)
        assert result in ("", filtered) or result.endswith("\n")
        _assert_negative_knowledge(result, context, summary_on)
    assert to_llm_prompt_fragment(
        context, max_tokens=counter(filtered), count_tokens=counter, sections=chosen
    ) == filtered


@pytest.mark.parametrize("chosen", SUBSETS)
@pytest.mark.parametrize("max_chars", [30, 120, 400, -1])
@pytest.mark.parametrize("max_tokens", [10, 60, BIG])
def test_both_budgets_hold_after_filter(
    chosen: tuple[str, ...], max_chars: int, max_tokens: int
) -> None:
    context = _context()
    filtered = _expected(context, chosen)
    for counter in COUNTERS:
        result = to_llm_prompt_fragment(
            context, max_chars, max_tokens=max_tokens, count_tokens=counter,
            sections=chosen,
        )
        if max_chars >= 0:
            assert len(result) <= max_chars
        assert counter(result) <= max_tokens
        assert filtered.startswith(result)
        _assert_negative_knowledge(result, context, SECTION_SUMMARY in chosen)


@pytest.mark.parametrize("chosen", SUBSETS)
def test_counter_failure_falls_back_on_filtered_text(chosen: tuple[str, ...]) -> None:
    context = _context()
    filtered = _expected(context, chosen)

    def boom(text: str) -> int:
        raise RuntimeError(CANARIES[0])

    for limit in (40, 200, len(filtered)):
        result = to_llm_prompt_fragment(
            context, limit, max_tokens=5, count_tokens=boom, sections=chosen
        )
        char_limited = to_llm_prompt_fragment(context, limit, sections=chosen)
        expected = "".join(
            _line_prefix(_split_complete_lines(filtered), len(char_limited))
        )
        assert result == expected
        assert len(result) <= limit
        _assert_clean(result)
        _assert_negative_knowledge(result, context, SECTION_SUMMARY in chosen)


# --- #141 и #142 ---


def test_negative_knowledge_follows_summary() -> None:
    context = _context(statuses=4)
    for chosen in SUBSETS:
        result = to_llm_prompt_fragment(context, sections=chosen)
        if SECTION_SUMMARY in chosen:
            lines = result.split("\n")
            for line in _status_lines(context):
                assert line in lines
        _assert_negative_knowledge(result, context, SECTION_SUMMARY in chosen)


def test_negative_knowledge_is_atomic_for_every_cut() -> None:
    context = _context(statuses=4)
    chosen = (SECTION_FORM, SECTION_SUMMARY)
    filtered = _expected(context, chosen)
    for limit in range(1, len(filtered) + 2):
        _assert_negative_knowledge(
            to_llm_prompt_fragment(context, limit, sections=chosen), context, True
        )


@pytest.mark.parametrize("chosen", SUBSETS)
def test_every_subset_is_sanitized(chosen: tuple[str, ...]) -> None:
    context = _context()
    full = to_llm_prompt_fragment(context, sections=chosen)
    _assert_clean(full)
    for limit in range(1, len(full) + 2, 7):
        _assert_clean(to_llm_prompt_fragment(context, limit, sections=chosen))
    for counter in COUNTERS:
        _assert_clean(to_llm_prompt_fragment(
            context, max_tokens=50, count_tokens=counter, sections=chosen
        ))


# --- детерминизм ---


def test_repeated_calls_are_deterministic() -> None:
    for chosen in (*SUBSETS, None):
        runs = {
            to_llm_prompt_fragment(
                _context(), 300, max_tokens=90, count_tokens=cyr_heavy, sections=chosen
            )
            for _ in range(3)
        }
        assert len(runs) == 1
