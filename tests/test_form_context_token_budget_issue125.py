# Issue #125: токенный бюджет to_llm_prompt_fragment.
#
# Синтетические данные и фейковые счётчики токенов, без сети и внешних
# токенайзеров. Символьный путь подменяется фейком, чтобы проверять алгоритм
# бюджета изолированно; реальный символьный режим и санитизация покрываются
# существующими тестами #77/#141/#142 через тот же символьный путь.
from __future__ import annotations

import inspect

import pytest

from v8unpack_agent import form_context as fc

NEG = "data_path: Объект.Склад; status: unresolved; reason: data_path не доказан"
FULL = (
    "# FORM Catalog.Demo.Form.ItemForm\n"
    "## SUMMARY\n"
    '{"form": "ItemForm", "items": 3}\n'
    "## OBJECT_ATTRIBUTES\n"
    "data_path: Объект.Код; status: resolved; type: String\n"
    + NEG + "\n"
    "data_path: None\n"
    "## BSL\n"
    "Процедура ПриОткрытии(Отказ)\n"
    "    Сообщить(\"demo\");\n"
    "КонецПроцедуры\n"
)
SANITIZED_MARK = "<redacted>"


class _Sentinel:
    def __getattr__(self, name: str):  # pragma: no cover - не должен вызываться
        raise AssertionError(f"обход символьного пути: доступ к {name}")


@pytest.fixture
def calls(monkeypatch):
    log: list[int] = []

    def fake(context, max_chars=-1):
        log.append(max_chars)
        return FULL if max_chars < 0 else FULL[:max_chars]

    monkeypatch.setattr(fc, "_to_llm_prompt_fragment_chars", fake)
    return log


def chars(text: str) -> int:
    return len(text)


def cyr_heavy(text: str) -> int:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return cyr * 2 + (len(text) - cyr + 3) // 4


def lines_of(text: str) -> list[str]:
    return text.split("\n")


def assert_line_prefix(result: str) -> None:
    assert FULL.startswith(result)
    assert result == "" or result.endswith("\n")


def test_signature_is_keyword_only_extension():
    params = inspect.signature(fc.to_llm_prompt_fragment).parameters
    legacy = inspect.signature(fc._to_llm_prompt_fragment_chars).parameters
    assert list(params)[:2] == ["context", "max_chars"]
    assert params["max_chars"].default == legacy["max_chars"].default
    assert params["max_tokens"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["count_tokens"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["max_tokens"].default is None
    assert params["count_tokens"].default is None


@pytest.mark.parametrize("max_chars", [-1, 0, 1, 17, 40, 10_000])
def test_legacy_passthrough_bit_for_bit(calls, max_chars):
    expected = FULL if max_chars < 0 else FULL[:max_chars]
    assert fc.to_llm_prompt_fragment(_Sentinel(), max_chars) == expected
    assert calls == [max_chars]


def test_legacy_default_uses_legacy_default(calls):
    default = inspect.signature(fc._to_llm_prompt_fragment_chars).parameters[
        "max_chars"
    ].default
    fc.to_llm_prompt_fragment(_Sentinel())
    assert calls == [default]


def test_max_tokens_without_counter_raises(calls):
    with pytest.raises(ValueError, match="только вместе"):
        fc.to_llm_prompt_fragment(_Sentinel(), max_tokens=10)
    assert calls == []


def test_counter_without_max_tokens_raises(calls):
    with pytest.raises(ValueError, match="только вместе"):
        fc.to_llm_prompt_fragment(_Sentinel(), count_tokens=chars)
    assert calls == []


@pytest.mark.parametrize("bad", [True, 1.5, "10"])
def test_max_tokens_type_is_checked(calls, bad):
    with pytest.raises(TypeError):
        fc.to_llm_prompt_fragment(_Sentinel(), max_tokens=bad, count_tokens=chars)


def test_counter_must_be_callable(calls):
    with pytest.raises(TypeError):
        fc.to_llm_prompt_fragment(_Sentinel(), max_tokens=5, count_tokens=42)


@pytest.mark.parametrize("max_tokens", [0, -1, -100])
def test_non_positive_tokens_give_empty(calls, max_tokens):
    assert fc.to_llm_prompt_fragment(
        _Sentinel(), max_tokens=max_tokens, count_tokens=chars
    ) == ""


def test_zero_chars_gives_empty_in_token_mode(calls):
    assert fc.to_llm_prompt_fragment(
        _Sentinel(), 0, max_tokens=1000, count_tokens=chars
    ) == ""


@pytest.mark.parametrize("max_chars", [-1, 30, 64, 120, 250, 10_000])
@pytest.mark.parametrize("max_tokens", [1, 5, 20, 60, 120, 10_000])
@pytest.mark.parametrize("counter", [chars, cyr_heavy])
def test_both_budgets_hold(calls, max_chars, max_tokens, counter):
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), max_chars, max_tokens=max_tokens, count_tokens=counter
    )
    if max_chars >= 0:
        assert len(result) <= max_chars
    assert counter(result) <= max_tokens
    assert_line_prefix(result)


def test_stricter_char_limit_wins(calls):
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), 40, max_tokens=10_000, count_tokens=chars
    )
    assert len(result) <= 40
    assert result == "# FORM Catalog.Demo.Form.ItemForm\n"


def test_stricter_token_limit_wins(calls):
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), 10_000, max_tokens=50, count_tokens=chars
    )
    assert len(result) <= 50
    assert result == "# FORM Catalog.Demo.Form.ItemForm\n## SUMMARY\n"


def test_unlimited_budgets_return_full(calls):
    assert fc.to_llm_prompt_fragment(
        _Sentinel(), -1, max_tokens=10**9, count_tokens=chars
    ) == FULL


def test_cyrillic_counter_is_stricter_than_latin(calls):
    budget = 90
    latin = fc.to_llm_prompt_fragment(
        _Sentinel(), max_tokens=budget, count_tokens=lambda s: (len(s) + 3) // 4
    )
    cyr = fc.to_llm_prompt_fragment(
        _Sentinel(), max_tokens=budget, count_tokens=cyr_heavy
    )
    assert len(cyr) < len(latin)
    assert cyr_heavy(cyr) <= budget


def test_deterministic(calls):
    runs = {
        fc.to_llm_prompt_fragment(_Sentinel(), 200, max_tokens=70, count_tokens=cyr_heavy)
        for _ in range(5)
    }
    assert len(runs) == 1


def test_summary_before_bsl(calls):
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), max_tokens=10**6, count_tokens=chars
    )
    assert result.index("## SUMMARY") < result.index("## BSL")


@pytest.mark.parametrize("cut", range(len(FULL) + 1))
def test_new_mode_never_leaves_broken_line(calls, cut):
    by_tokens = fc.to_llm_prompt_fragment(
        _Sentinel(), max_tokens=max(cut, 1), count_tokens=chars
    )
    by_chars = fc.to_llm_prompt_fragment(
        _Sentinel(), cut, max_tokens=10**6, count_tokens=chars
    )
    for result in (by_tokens, by_chars):
        assert_line_prefix(result)
        for line in lines_of(result)[:-1]:
            assert line + "\n" in FULL


@pytest.mark.parametrize("cut", range(len(FULL) + 1))
def test_negative_knowledge_line_is_atomic(calls, cut):
    for result in (
        fc.to_llm_prompt_fragment(_Sentinel(), cut, max_tokens=10**6, count_tokens=chars),
        fc.to_llm_prompt_fragment(_Sentinel(), max_tokens=max(cut, 1), count_tokens=chars),
    ):
        partial = [
            line for line in lines_of(result)
            if line and NEG.startswith(line) and line != NEG
        ]
        assert not partial
        if "data_path: Объект.Склад" in result:
            assert NEG + "\n" in result
        if "data_path: None" in result:
            assert "data_path: None\n" in result


def test_counter_exception_falls_back_to_char_budget(calls):
    def boom(text: str) -> int:
        raise RuntimeError("secret-internal-detail")

    result = fc.to_llm_prompt_fragment(_Sentinel(), 120, max_tokens=5, count_tokens=boom)
    assert len(result) <= 120
    assert_line_prefix(result)
    assert result == "".join(
        fc._line_prefix(fc._split_complete_lines(FULL), 120)
    )


@pytest.mark.parametrize("bad_value", [None, "3", 2.0, -1, True])
def test_counter_invalid_result_falls_back(calls, bad_value):
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), 120, max_tokens=5, count_tokens=lambda s: bad_value
    )
    assert len(result) <= 120
    assert_line_prefix(result)
    assert result == "".join(
        fc._line_prefix(fc._split_complete_lines(FULL), 120)
    )


def test_token_mode_only_uses_sanitized_char_path(monkeypatch):
    sanitized = FULL.replace("demo", SANITIZED_MARK)
    seen: list[int] = []

    def fake(context, max_chars=-1):
        seen.append(max_chars)
        return sanitized if max_chars < 0 else sanitized[:max_chars]

    monkeypatch.setattr(fc, "_to_llm_prompt_fragment_chars", fake)
    result = fc.to_llm_prompt_fragment(
        _Sentinel(), 500, max_tokens=10**6, count_tokens=chars
    )
    assert seen == [500, -1]
    assert sanitized.startswith(result)
    assert "\"demo\"" not in result
    assert NEG + "\n" in result
