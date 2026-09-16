"""Регресс #278: метрика применимости учитывает вторую ступень резолюции.

Все имена и UUID синтетические либо взяты из доказанной статической таблицы
платформенных типов. Данные реальной конфигурации здесь не используются.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.platform_reference_types import PLATFORM_REFERENCE_TYPES

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "unresolved_refs_report.py"
)
MODULE_NAME = "unresolved_refs_report_issue278"

INDEX_NAME = "CatalogRef.SyntheticCatalog"
STATIC_NAME = min(PLATFORM_REFERENCE_TYPES.values())
SYNTHETIC_UUID = "00000000-0000-0000-0000-000000000001"
UNKNOWN_REF = "Ref#" + SYNTHETIC_UUID
NON_REFERENCE = "String"
FOREIGN_XDTO = "synthetic:NotAReferenceType"


@pytest.fixture(scope="module")
def report_module() -> Iterator[Any]:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Модуль обязан попасть в sys.modules до exec_module.
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(MODULE_NAME, None)


def _population(module: Any, type_names: list[str]) -> tuple[int, int, int]:
    """Контракт счётчиков отчёта: applicable / resolved / unresolved."""
    applicable = 0
    resolved = 0
    unresolved = 0
    for type_name in type_names:
        if not module.is_reference_type(type_name):
            continue
        applicable += 1
        if type_name.startswith(module.REF_PREFIX):
            unresolved += 1
        else:
            resolved += 1
    return applicable, resolved, unresolved


def test_index_name_is_applicable(report_module: Any) -> None:
    assert report_module.is_reference_type(INDEX_NAME)


def test_static_table_name_is_applicable(report_module: Any) -> None:
    assert report_module.is_reference_type(STATIC_NAME)


def test_unknown_ref_is_applicable(report_module: Any) -> None:
    assert report_module.is_reference_type(UNKNOWN_REF)


def test_non_reference_type_is_not_applicable(report_module: Any) -> None:
    assert not report_module.is_reference_type(NON_REFERENCE)


def test_foreign_xdto_name_is_not_applicable(report_module: Any) -> None:
    # Префиксная XDTO-форма сама по себе не доказывает ссылочный тип.
    assert FOREIGN_XDTO not in set(PLATFORM_REFERENCE_TYPES.values())
    assert not report_module.is_reference_type(FOREIGN_XDTO)


def test_every_static_name_is_applicable(report_module: Any) -> None:
    for type_name in PLATFORM_REFERENCE_TYPES.values():
        assert report_module.is_reference_type(type_name), type_name


def test_membership_uses_values_not_uuid_keys(report_module: Any) -> None:
    for uuid_key in PLATFORM_REFERENCE_TYPES:
        assert not report_module.is_reference_type(uuid_key), uuid_key


def test_module_exposes_static_name_set(report_module: Any) -> None:
    assert report_module.PLATFORM_REFERENCE_TYPE_NAMES == frozenset(
        PLATFORM_REFERENCE_TYPES.values()
    )


def test_static_resolution_grows_applicable_and_resolved(report_module: Any) -> None:
    base = [INDEX_NAME, UNKNOWN_REF, NON_REFERENCE]
    assert _population(report_module, base) == (2, 1, 1)

    with_static = [*base, STATIC_NAME]
    applicable, resolved, unresolved = _population(report_module, with_static)
    assert (applicable, resolved, unresolved) == (3, 2, 1)


def test_static_resolution_keeps_unresolved_stable(report_module: Any) -> None:
    base_unresolved = _population(report_module, [INDEX_NAME, UNKNOWN_REF])[2]
    grown_unresolved = _population(
        report_module, [INDEX_NAME, UNKNOWN_REF, STATIC_NAME]
    )[2]
    assert grown_unresolved == base_unresolved
