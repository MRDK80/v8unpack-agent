"""Регрессионные тесты ленивых корневых импортов — issue #140.

Состав sys.modules проверяется только в отдельных процессах: повторный
импорт в текущем процессе искажён уже загруженным пакетом. Дочерние
процессы отдают результат как JSON, локализованный текст не разбирается.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

import v8unpack_agent

LAZY_MODULES = ("form_router", "form_classifier", "coverage_metric")

GUARDED_MODULES = (
    "v8unpack_agent.drift_checker",
    "v8unpack_agent.form_artifact",
    "v8unpack_agent.elem_parser",
    "v8unpack_agent.pipeline",
    "v8unpack_agent.forms_index",
    "v8unpack_agent.skd_extractor",
    "logging",
    "hashlib",
    "datetime",
)

DUMP_PACKAGE_MODULES = (
    "print(json.dumps(sorted("
    "name for name in sys.modules"
    " if name == 'v8unpack_agent' or name.startswith('v8unpack_agent.')"
    ")))"
)


def _run_json(body: str) -> Any:
    code = "import json, sys\n" + body
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _clean_import_modules() -> list[str]:
    return _run_json("import v8unpack_agent\n" + DUMP_PACKAGE_MODULES)


@pytest.mark.parametrize("module", LAZY_MODULES)
def test_lazy_module_not_loaded_after_clean_import(module: str) -> None:
    assert f"v8unpack_agent.{module}" not in _clean_import_modules()


def test_clean_import_keeps_form_paths_eager() -> None:
    """Часть C: form_paths остаётся eager, решение задокументировано."""
    assert "v8unpack_agent.form_paths" in _clean_import_modules()


def test_form_paths_root_name_is_the_function_not_the_module() -> None:
    """Причина, по которой form_paths не делается ленивым (часть C issue #140).

    Имя form_paths — одновременно подмодуль пакета и публичная функция.
    Eager-импорт в __init__ выставляет атрибут пакета в функцию, поэтому
    подмодуль достаётся через importlib.import_module: форма
    `import v8unpack_agent.form_paths as module` вернула бы функцию, так как
    Python 3.7+ сначала берёт getattr(package, "form_paths").
    """
    result = _run_json(
        "import importlib, types\n"
        "module = importlib.import_module('v8unpack_agent.form_paths')\n"
        "from v8unpack_agent import form_paths\n"
        "print(json.dumps([\n"
        "    isinstance(form_paths, types.ModuleType),\n"
        "    callable(form_paths),\n"
        "    form_paths is module.form_paths,\n"
        "]))"
    )
    assert result == [False, True, True]


def test_form_paths_root_name_survives_submodule_import_first() -> None:
    """Контракт, который был бы утрачен при ленивом form_paths."""
    result = _run_json(
        "import importlib\n"
        "importlib.import_module('v8unpack_agent.form_paths')\n"
        "import v8unpack_agent\n"
        "print(json.dumps([\n"
        "    callable(v8unpack_agent.form_paths),\n"
        "    callable(getattr(v8unpack_agent, 'form_root')),\n"
        "]))"
    )
    assert result == [True, True]


def test_guarded_modules_not_loaded_after_clean_import() -> None:
    loaded = set(
        _run_json(
            "before = set(sys.modules)\n"
            "import v8unpack_agent\n"
            "print(json.dumps(sorted(set(sys.modules) - before)))"
        )
    )
    assert sorted(set(GUARDED_MODULES) & loaded) == []


def test_attribute_access_loads_router_group() -> None:
    result = _run_json(
        "import v8unpack_agent\n"
        "before = 'v8unpack_agent.form_router' in sys.modules\n"
        "v8unpack_agent.FormRouter\n"
        "after = 'v8unpack_agent.form_router' in sys.modules\n"
        "print(json.dumps([before, after]))"
    )
    assert result == [False, True]


def test_route_result_resolves_without_prior_module_import() -> None:
    result = _run_json(
        "from v8unpack_agent import RouteResult\n"
        "print(json.dumps([RouteResult.__name__]))"
    )
    assert result == ["RouteResult"]


def test_attribute_access_loads_classifier_group() -> None:
    result = _run_json(
        "import v8unpack_agent\n"
        "before = 'v8unpack_agent.form_classifier' in sys.modules\n"
        "v8unpack_agent.classify_form\n"
        "after = 'v8unpack_agent.form_classifier' in sys.modules\n"
        "print(json.dumps([before, after]))"
    )
    assert result == [False, True]


@pytest.mark.parametrize(
    "name",
    [
        "FormRouter",
        "RouteResult",
        "FormClass",
        "SERVICE_FORM_NAME_PATTERNS",
        "classify_form",
        "classify_form_by_bindings",
        "classify_form_by_name",
    ],
)
def test_lazy_names_stay_in_all_and_resolve(name: str) -> None:
    assert name in v8unpack_agent.__all__
    assert getattr(v8unpack_agent, name) is not None


@pytest.mark.parametrize("name", ["FormRouter", "RouteResult", "classify_form"])
def test_repeated_access_returns_same_object(name: str) -> None:
    assert getattr(v8unpack_agent, name) is getattr(v8unpack_agent, name)


def test_lazy_symbols_are_identical_to_module_attributes() -> None:
    from v8unpack_agent.form_classifier import classify_form
    from v8unpack_agent.form_router import FormRouter, RouteResult

    assert v8unpack_agent.FormRouter is FormRouter
    assert v8unpack_agent.RouteResult is RouteResult
    assert v8unpack_agent.classify_form is classify_form


def test_every_public_name_resolves() -> None:
    unresolved = [
        name for name in v8unpack_agent.__all__ if not hasattr(v8unpack_agent, name)
    ]
    assert unresolved == []


def test_star_import_exposes_exactly_all() -> None:
    namespace: dict[str, object] = {}
    exec("from v8unpack_agent import *", namespace)  # noqa: S102
    exported = {key for key in namespace if not key.startswith("__")}
    assert set(v8unpack_agent.__all__) <= exported


def test_from_root_import_still_works() -> None:
    result = _run_json(
        "from v8unpack_agent import FormRouter, classify_form\n"
        "print(json.dumps([FormRouter.__name__, classify_form.__name__]))"
    )
    assert result == ["FormRouter", "classify_form"]


def test_no_circular_import_on_lazy_resolution() -> None:
    result = _run_json(
        "import v8unpack_agent\n"
        "v8unpack_agent.FormRouter\n"
        "v8unpack_agent.classify_form\n"
        "v8unpack_agent.FormClass\n"
        "print(json.dumps(['ok']))"
    )
    assert result == ["ok"]


def test_runner_and_cli_still_import() -> None:
    result = _run_json(
        "from v8unpack_agent import cli, runner\n"
        "print(json.dumps([cli.__name__, runner.__name__]))"
    )
    assert result == ["v8unpack_agent.cli", "v8unpack_agent.runner"]
