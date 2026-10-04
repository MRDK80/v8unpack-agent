"""Синтетические тесты LLM-проекции модуля (#345)."""

from __future__ import annotations

import dataclasses
from typing import Any, cast

import pytest

from v8unpack_agent.module_projection import (
    ModuleProjection,
    to_llm_module_fragment,
)
from v8unpack_agent.modules import ModuleEntry, ModuleReadStatus

LF = chr(10)
CR = chr(13)
BOM = chr(0xFEFF)
BACKSLASH = chr(92)
BODY = "Процедура Тест()" + LF + "КонецПроцедуры" + LF
PATH = "Catalog/Items/Ext/ObjectModule.bsl"
HEADER = (
    LF.join(
        [
            "MODULE",
            "kind: object",
            "owner_kind: metadata_object",
            "metadata_type: Catalog",
            "owner: Items",
            f"path: {PATH}",
            "BSL",
        ]
    )
    + LF
)
MARKER_HEAD = "[TRUNCATED: "
MARKER_TAIL = " chars]"


def _entry(status: ModuleReadStatus = "ok") -> ModuleEntry:
    return ModuleEntry(
        module_kind="object",
        owner_kind="metadata_object",
        metadata_type="Catalog",
        owner_name="Items",
        relative_path=PATH,
        read_status=status,
    )


def _call(*args: Any, **kwargs: Any) -> ModuleProjection:
    return to_llm_module_fragment(*args, **kwargs)


def _marker(included: int, total: int) -> str:
    return LF + f"{MARKER_HEAD}{included} of {total}{MARKER_TAIL}"


def _parse_marker(text: str) -> tuple[int, int]:
    last = text.rsplit(LF, 1)[1]
    assert last.startswith(MARKER_HEAD) and last.endswith(MARKER_TAIL)
    core = last[len(MARKER_HEAD) : -len(MARKER_TAIL)]
    included, total = core.split(" of ")
    return int(included), int(total)


def test_ok_full_text() -> None:
    result = _call(_entry(), BODY)
    assert result == ModuleProjection(
        status="ok",
        text=HEADER + BODY,
        truncated=False,
        original_chars=len(BODY),
    )


def test_configuration_owner_header() -> None:
    entry = ModuleEntry(
        module_kind="managed_application",
        owner_kind="configuration",
        metadata_type=None,
        owner_name=None,
        relative_path="Configuration.app.bsl",
        read_status="ok",
    )
    expected = (
        LF.join(
            [
                "MODULE",
                "kind: managed_application",
                "owner_kind: configuration",
                "path: Configuration.app.bsl",
                "BSL",
            ]
        )
        + LF
    )
    assert _call(entry, BODY).text == expected + BODY


@pytest.mark.parametrize(
    "status", ["empty", "whitespace_only", "missing", "read_error"]
)
@pytest.mark.parametrize("text", [None, "", BODY])
def test_statuses_without_text(status: Any, text: Any) -> None:
    result = _call(_entry(status), text, max_chars=0)
    assert result == ModuleProjection(
        status=status, text="", truncated=False, original_chars=0
    )


def test_non_ok_status_ignores_text_type() -> None:
    result = _call(_entry("empty"), 123)
    assert result.status == "empty"
    assert result.text == ""


@pytest.mark.parametrize(
    "raw",
    [
        BODY.replace(LF, CR + LF),
        BODY.replace(LF, CR),
        BOM + BODY,
        BOM + BODY.replace(LF, CR + LF),
    ],
)
def test_line_endings_and_bom_normalized(raw: str) -> None:
    result = _call(_entry(), raw)
    assert result.text == HEADER + BODY
    assert result.original_chars == len(BODY)


def test_budget_sweep() -> None:
    body = BODY * 4
    full = HEADER + body
    for limit in range(-1, len(full) + 3):
        result = _call(_entry(), body, max_chars=limit)
        assert result.original_chars == len(body)
        if limit == -1 or limit >= len(full):
            assert result.text == full
            assert not result.truncated
            continue
        assert result.truncated
        assert len(result.text) <= limit
        if not result.text:
            assert len(HEADER) + len(_marker(0, len(body))) > limit
            continue
        included, total = _parse_marker(result.text)
        assert total == len(body)
        expected = HEADER + body[:included] + _marker(included, total)
        assert result.text == expected
        if included + 1 < len(body):
            bigger = len(HEADER) + included + 1
            bigger += len(_marker(included + 1, total))
            assert bigger > limit


def test_zero_budget_gives_empty_truncated() -> None:
    result = _call(_entry(), BODY, max_chars=0)
    assert result == ModuleProjection(
        status="ok", text="", truncated=True, original_chars=len(BODY)
    )


def test_exact_fit_is_not_truncated() -> None:
    full = HEADER + BODY
    exact = _call(_entry(), BODY, max_chars=len(full))
    assert exact.text == full and not exact.truncated
    less = _call(_entry(), BODY, max_chars=len(full) - 1)
    assert less.truncated and len(less.text) <= len(full) - 1


def test_default_is_unlimited() -> None:
    body = BODY * 50
    default = _call(_entry(), body)
    assert default == _call(_entry(), body, max_chars=-1)
    assert default.text == HEADER + body


def test_deterministic_and_frozen() -> None:
    first = _call(_entry(), BODY, max_chars=60)
    second = _call(_entry(), BODY, max_chars=60)
    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast(Any, first).text = "x"


def test_header_has_no_absolute_paths_or_backslashes() -> None:
    result = _call(_entry(), "A")
    header = result.text.split("BSL" + LF)[0]
    assert BACKSLASH not in header
    assert "path: /" not in header
    assert PATH in header


@pytest.mark.parametrize("bad", [True, False, "5", 1.5, None])
def test_max_chars_type_error(bad: Any) -> None:
    with pytest.raises(TypeError):
        _call(_entry(), BODY, max_chars=bad)


def test_max_chars_value_error() -> None:
    with pytest.raises(ValueError):
        _call(_entry(), BODY, max_chars=-2)


def test_ok_requires_text() -> None:
    with pytest.raises(ValueError):
        _call(_entry(), None)
    with pytest.raises(TypeError):
        _call(_entry(), 5)


def test_entry_type_error() -> None:
    with pytest.raises(TypeError):
        _call(object(), BODY)
