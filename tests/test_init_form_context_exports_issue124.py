"""Issue #124: корневой экспорт FormContext должен работать и остаться ленивым.

Целевой путь: tests/test_init_form_context_exports_issue124.py

Замечание о границах ленивости
------------------------------
Начиная с issue #128 ``import v8unpack_agent`` не загружает ни ``form_context``,
ни ``elem_parser``, ни ``pipeline``. Эти тесты проверяют часть гарантии,
относящуюся к #124: ``form_context`` не импортируется до первого обращения к
новым именам. Полная проверка ленивости — в
``tests/test_init_lazy_imports_issue128.py``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

NEW_NAMES = ("FormContext", "build_form_context", "to_llm_prompt_fragment")


def _run_clean_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_root_import_returns_same_objects_as_submodule_import() -> None:
    import v8unpack_agent
    from v8unpack_agent import form_context as fc_module

    for name in NEW_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(fc_module, name)


def test_new_names_are_listed_in_dunder_all() -> None:
    import v8unpack_agent

    assert set(NEW_NAMES).issubset(set(v8unpack_agent.__all__))


def test_plain_import_does_not_load_form_context() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.form_context" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "lazy-ok" in result.stdout


def test_attribute_access_loads_dependencies_and_is_repeatable() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.form_context" not in sys.modules
        first = v8unpack_agent.FormContext
        assert "v8unpack_agent.form_context" in sys.modules
        second = v8unpack_agent.FormContext
        assert first is second
        assert v8unpack_agent.build_form_context is not None
        assert v8unpack_agent.to_llm_prompt_fragment is not None
        print("eager-after-access-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "eager-after-access-ok" in result.stdout


def test_existing_lazy_groups_and_deprecated_shims_still_work() -> None:
    import v8unpack_agent

    assert v8unpack_agent.FormSummary is not None
    assert v8unpack_agent.build_form_summary is not None
    assert v8unpack_agent.scan_forms is not None
    assert v8unpack_agent.discover_elem_forms is not None
    assert v8unpack_agent.discover_managed_forms is not None
    assert v8unpack_agent.ManagedFormEntry is not None


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import pytest

    import v8unpack_agent

    with pytest.raises(AttributeError):
        _ = v8unpack_agent.NoSuchSymbol
