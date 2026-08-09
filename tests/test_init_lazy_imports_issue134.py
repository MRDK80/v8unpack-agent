"""Issue #134 (часть B): ``import v8unpack_agent`` не должен загружать ``forms_index`` и ``skd_extractor``.

Целевой путь: tests/test_init_lazy_imports_issue134.py

Гарантия, которую фиксируют эти тесты
-------------------------------------
После #131 корневой импорт всё ещё eager-загружал ``v8unpack_agent.forms_index``
и ``v8unpack_agent.skd_extractor`` из шапки ``__init__.py``. Символы
``FormsIndex``, ``FormsIndexEntry``, ``is_form_stale`` и ``SkdResult``,
``SkdBatchResult``, ``extract_skd_queries``, ``extract_all_skd_queries``
должны отдаваться двумя независимыми ленивыми группами ``__getattr__``:
подмодуль грузится при первом обращении к символу, а не при импорте пакета.
Публичная поверхность (``__all__``, корневые и прямые импорты) не меняется.

Проверки идут в чистом subprocess, поэтому результат не зависит от порядка
выполнения других тестов и от прогретого ``sys.modules``. Путь к интерпретатору
берётся из ``sys.executable``; абсолютные пути и литеральные разделители не
используются.

Независимость групп проверяется отдельными тестами: обращение к символу одной
группы не должно подтягивать чужой подмодуль.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

LAZY_FORMS_INDEX_NAMES = ("FormsIndex", "FormsIndexEntry", "is_form_stale")

LAZY_SKD_NAMES = (
    "SkdResult",
    "SkdBatchResult",
    "extract_skd_queries",
    "extract_all_skd_queries",
)

FORMS_INDEX_MODULE = "v8unpack_agent.forms_index"

SKD_EXTRACTOR_MODULE = "v8unpack_agent.skd_extractor"


def _run_clean_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    """Выполнить фрагмент в чистом интерпретаторе без прогретого sys.modules."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_plain_import_does_not_load_forms_index() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.forms_index" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-forms-index-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "lazy-forms-index-ok" in result.stdout


def test_plain_import_does_not_load_skd_extractor() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.skd_extractor" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-skd-extractor-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "lazy-skd-extractor-ok" in result.stdout


def test_forms_index_group_loads_on_first_access_only() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.forms_index" not in sys.modules
        first = v8unpack_agent.FormsIndex
        assert "v8unpack_agent.forms_index" in sys.modules
        assert "v8unpack_agent.skd_extractor" not in sys.modules
        assert v8unpack_agent.FormsIndex is first
        assert v8unpack_agent.FormsIndexEntry is not None
        assert v8unpack_agent.is_form_stale is not None
        print("forms-index-group-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "forms-index-group-ok" in result.stdout


def test_skd_group_loads_on_first_access_only() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.skd_extractor" not in sys.modules
        first = v8unpack_agent.extract_skd_queries
        assert "v8unpack_agent.skd_extractor" in sys.modules
        assert "v8unpack_agent.forms_index" not in sys.modules
        assert v8unpack_agent.extract_skd_queries is first
        assert v8unpack_agent.SkdResult is not None
        assert v8unpack_agent.SkdBatchResult is not None
        assert v8unpack_agent.extract_all_skd_queries is not None
        print("skd-group-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "skd-group-ok" in result.stdout


def test_issue131_guarantees_still_hold() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        for name in (
            "v8unpack_agent.drift_checker",
            "v8unpack_agent.form_artifact",
            "logging",
            "hashlib",
            "datetime",
        ):
            assert name not in sys.modules, name
        print("issue131-guarantees-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "issue131-guarantees-ok" in result.stdout


def test_root_import_returns_same_objects_as_submodule_import() -> None:
    import v8unpack_agent
    from v8unpack_agent import forms_index as forms_index_module
    from v8unpack_agent import skd_extractor as skd_extractor_module

    for name in LAZY_FORMS_INDEX_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(forms_index_module, name)

    for name in LAZY_SKD_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(skd_extractor_module, name)


def test_lazy_names_remain_in_dunder_all() -> None:
    import v8unpack_agent

    expected = set(LAZY_FORMS_INDEX_NAMES) | set(LAZY_SKD_NAMES)
    assert expected.issubset(set(v8unpack_agent.__all__))


def test_root_star_style_import_still_works() -> None:
    result = _run_clean_interpreter(
        """
        from v8unpack_agent import (
            FormsIndex,
            FormsIndexEntry,
            SkdBatchResult,
            SkdResult,
            extract_all_skd_queries,
            extract_skd_queries,
            is_form_stale,
        )

        for obj in (
            FormsIndex,
            FormsIndexEntry,
            is_form_stale,
            SkdResult,
            SkdBatchResult,
            extract_skd_queries,
            extract_all_skd_queries,
        ):
            assert obj is not None
        print("root-import-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "root-import-ok" in result.stdout


def test_direct_submodule_import_still_works() -> None:
    result = _run_clean_interpreter(
        """
        from v8unpack_agent.forms_index import FormsIndex, FormsIndexEntry, is_form_stale
        from v8unpack_agent.skd_extractor import (
            SkdBatchResult,
            SkdResult,
            extract_all_skd_queries,
            extract_skd_queries,
        )

        assert FormsIndex is not None
        assert FormsIndexEntry is not None
        assert is_form_stale is not None
        assert SkdResult is not None
        assert SkdBatchResult is not None
        assert extract_skd_queries is not None
        assert extract_all_skd_queries is not None
        print("direct-import-ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "direct-import-ok" in result.stdout


def test_existing_lazy_groups_and_public_surface_intact() -> None:
    import v8unpack_agent

    assert v8unpack_agent.parse_elem_json is not None
    assert v8unpack_agent.ElemIndexResult is not None
    assert v8unpack_agent.FormUnpacker is not None
    assert v8unpack_agent.update_forms_index is not None
    assert v8unpack_agent.scan_forms is not None
    assert v8unpack_agent.check_drift is not None
    assert v8unpack_agent.FormArtifact is not None
    assert v8unpack_agent.FormContext is not None
    assert v8unpack_agent.classify_form is not None

    for name in v8unpack_agent.__all__:
        assert getattr(v8unpack_agent, name) is not None, name


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import v8unpack_agent

    with pytest.raises(AttributeError):
        _ = v8unpack_agent.NoSuchSymbol
