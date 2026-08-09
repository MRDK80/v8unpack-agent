"""Issue #131: ``import v8unpack_agent`` не должен загружать ``form_artifact`` и ``drift_checker``.

Целевой путь: tests/test_init_lazy_imports_issue131.py

Гарантия, которую фиксируют эти тесты
-------------------------------------
После #128 корневой импорт пакета всё ещё eager-загружает
``v8unpack_agent.form_artifact`` и ``v8unpack_agent.drift_checker``. Символы
``FormArtifact``, ``check_drift`` и ``DriftReport`` должны отдаваться ленивыми
группами ``__getattr__``: подмодуль загружается при первом обращении к символу,
а не при импорте пакета. Публичная поверхность (``__all__``, корневые и прямые
импорты) не меняется.

Проверки идут в чистом subprocess, поэтому результат не зависит от порядка
выполнения других тестов и от прогретого ``sys.modules``. Путь к интерпретатору
берётся из ``sys.executable``; абсолютные пути и литеральные разделители не
используются.

Про stdlib-зависимости: ``logging``, ``hashlib`` и ``datetime`` проверяются
отдельными тестами, чтобы фактический dependency graph был виден по одному
упавшему тесту, а не смешивался с основной гарантией ленивости.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

LAZY_FORM_ARTIFACT_NAMES = ("FormArtifact",)

LAZY_DRIFT_CHECKER_NAMES = ("check_drift", "DriftReport")

FORM_ARTIFACT_MODULE = "v8unpack_agent.form_artifact"

DRIFT_CHECKER_MODULE = "v8unpack_agent.drift_checker"


def _run_clean_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    """Выполнить фрагмент в чистом интерпретаторе без прогретого sys.modules."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_plain_import_does_not_load_form_artifact() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.form_artifact" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-form-artifact-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "lazy-form-artifact-ok" in result.stdout


def test_plain_import_does_not_load_drift_checker() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.drift_checker" not in sys.modules, sorted(
            m for m in sys.modules if m.startswith("v8unpack_agent")
        )
        print("lazy-drift-checker-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "lazy-drift-checker-ok" in result.stdout


def test_plain_import_does_not_load_hashlib() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "hashlib" not in sys.modules
        print("no-hashlib-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "no-hashlib-ok" in result.stdout


def test_plain_import_does_not_load_datetime() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "datetime" not in sys.modules
        print("no-datetime-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "no-datetime-ok" in result.stdout


def test_plain_import_does_not_load_logging() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "logging" not in sys.modules
        print("no-logging-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "no-logging-ok" in result.stdout


def test_form_artifact_loads_on_first_attribute_access() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.form_artifact" not in sys.modules
        first = v8unpack_agent.FormArtifact
        assert "v8unpack_agent.form_artifact" in sys.modules
        second = v8unpack_agent.FormArtifact
        assert first is second
        print("form-artifact-after-access-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "form-artifact-after-access-ok" in result.stdout


def test_drift_checker_loads_on_first_attribute_access() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.drift_checker" not in sys.modules
        first = v8unpack_agent.check_drift
        assert "v8unpack_agent.drift_checker" in sys.modules
        second = v8unpack_agent.check_drift
        assert first is second
        assert v8unpack_agent.DriftReport is not None
        print("drift-checker-after-access-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "drift-checker-after-access-ok" in result.stdout


def test_drift_report_access_also_loads_drift_checker() -> None:
    result = _run_clean_interpreter(
        """
        import sys

        import v8unpack_agent

        assert "v8unpack_agent.drift_checker" not in sys.modules
        first = v8unpack_agent.DriftReport
        assert "v8unpack_agent.drift_checker" in sys.modules
        assert v8unpack_agent.DriftReport is first
        print("drift-report-after-access-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "drift-report-after-access-ok" in result.stdout


def test_root_import_returns_same_objects_as_submodule_import() -> None:
    import v8unpack_agent
    from v8unpack_agent import drift_checker as drift_checker_module
    from v8unpack_agent import form_artifact as form_artifact_module

    for name in LAZY_FORM_ARTIFACT_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(form_artifact_module, name)

    for name in LAZY_DRIFT_CHECKER_NAMES:
        assert getattr(v8unpack_agent, name) is getattr(drift_checker_module, name)


def test_lazy_names_remain_in_dunder_all() -> None:
    import v8unpack_agent

    expected = set(LAZY_FORM_ARTIFACT_NAMES) | set(LAZY_DRIFT_CHECKER_NAMES)
    assert expected.issubset(set(v8unpack_agent.__all__))


def test_root_star_style_import_still_works() -> None:
    result = _run_clean_interpreter(
        """
        from v8unpack_agent import DriftReport, FormArtifact, check_drift

        assert FormArtifact is not None
        assert check_drift is not None
        assert DriftReport is not None
        print("root-import-ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "root-import-ok" in result.stdout


def test_direct_submodule_import_still_works() -> None:
    result = _run_clean_interpreter(
        """
        from v8unpack_agent.drift_checker import DriftReport, check_drift
        from v8unpack_agent.form_artifact import FormArtifact

        assert FormArtifact is not None
        assert check_drift is not None
        assert DriftReport is not None
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
    assert v8unpack_agent.FormContext is not None
    assert v8unpack_agent.FormSummary is not None
    assert v8unpack_agent.classify_form is not None

    for name in v8unpack_agent.__all__:
        assert getattr(v8unpack_agent, name) is not None, name


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import pytest

    import v8unpack_agent

    with pytest.raises(AttributeError):
        _ = v8unpack_agent.NoSuchSymbol
