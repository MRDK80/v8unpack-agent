"""Issue #128: ``import v8unpack_agent`` не должен загружать ``elem_parser``.

Целевой путь: tests/test_init_lazy_imports_issue128.py

Гарантия, которую фиксируют эти тесты
-------------------------------------
Тяжёлый модуль ``v8unpack_agent.elem_parser`` (~60 КБ) загружается только при
первом обращении к его публичным символам (``ElemIndexResult``, ``parse_elem_json``),
а не при импорте пакета. Проверка идёт в чистом subprocess, поэтому результат не
зависит от порядка выполнения других тестов и от уже прогретого ``sys.modules``.

Переносимость: путь к интерпретатору берётся из ``sys.executable``, литеральные
разделители путей и абсолютные пути не используются.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

LAZY_ELEM_NAMES = ("ElemIndexResult", "parse_elem_json")

ELEM_PARSER_MODULE = "v8unpack_agent.elem_parser"


def _run_clean_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    """Выполнить фрагмент в чистом интерпретаторе без прогретого sys.modules."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_plain_import_does_not_load_elem_parser() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.elem_parser" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-elem-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "lazy-elem-ok" in result.stdout


def test_elem_parser_loads_on_first_attribute_access() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.elem_parser" not in sys.modules
        first = v8unpack_agent.parse_elem_json
        assert "v8unpack_agent.elem_parser" in sys.modules
        second = v8unpack_agent.parse_elem_json
        assert first is second
        assert v8unpack_agent.ElemIndexResult is not None
        print("eager-after-access-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "eager-after-access-ok" in result.stdout


def test_root_import_returns_same_objects_as_submodule_import() -> None:
    import v8unpack_agent
    from v8unpack_agent import elem_parser as elem_parser_module

    for name in LAZY_ELEM_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(elem_parser_module, name)


def test_elem_names_remain_in_dunder_all() -> None:
    import v8unpack_agent

    assert set(LAZY_ELEM_NAMES).issubset(set(v8unpack_agent.__all__))


def test_direct_submodule_import_still_works() -> None:
    result = _run_clean_interpreter(
        """
        from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json

        assert ElemIndexResult is not None
        assert parse_elem_json is not None
        print("direct-import-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "direct-import-ok" in result.stdout


def test_public_surface_and_existing_lazy_groups_intact() -> None:
    import v8unpack_agent

    assert v8unpack_agent.FormArtifact is not None
    assert v8unpack_agent.FormsIndex is not None
    assert v8unpack_agent.check_drift is not None
    assert v8unpack_agent.classify_form is not None
    assert v8unpack_agent.SERVICE_FORM_NAME_PATTERNS is not None
    assert v8unpack_agent.scan_forms is not None
    assert v8unpack_agent.FormSummary is not None
    assert v8unpack_agent.FormContext is not None
    assert v8unpack_agent.discover_elem_forms is not None
    assert v8unpack_agent.discover_managed_forms is not None
    assert v8unpack_agent.ManagedFormEntry is not None

    for name in v8unpack_agent.__all__:
        assert getattr(v8unpack_agent, name) is not None, name


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import pytest

    import v8unpack_agent

    with pytest.raises(AttributeError):
        _ = v8unpack_agent.NoSuchSymbol
