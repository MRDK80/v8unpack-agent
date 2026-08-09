"""Issue #134: публичный ``form_key`` и совместимый алиас ``_form_key``.

Целевой путь: tests/test_form_key_public_issue134.py

Что фиксируют эти тесты
-----------------------
``FormRouter.reindex`` использовал приватный хелпер чужого модуля
(``drift_checker._form_key``). Часть A #134 вводит публичное имя ``form_key``
и оставляет ``_form_key`` тонким алиасом без второй реализации.

Тесты характеризационные: ожидаемые ключи и отпечаток сняты на ``main``
(``c1210ba4``) до правки, поэтому любое изменение формата составного ключа
или разделителя падает здесь, а не в проде.

Абсолютных путей и литеральных разделителей ФС в тестах нет; порядок запуска
не важен.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest

import v8unpack_agent
from v8unpack_agent import drift_checker
from v8unpack_agent.drift_checker import form_key
from v8unpack_agent.form_router import FormRouter

EXPECTED_PARAMS = [
    "object_type",
    "object_name",
    "container_name",
    "form_name",
]

# Baseline, снятый на main (c1210ba4) до введения публичного имени.
KEY_CASES = (
    (
        ("Catalog", "Товары", "Forms", "ФормаЭлемента"),
        "Catalog/Товары/Forms/ФормаЭлемента",
    ),
    (
        ("Document", "ЗаказПокупателя", "Forms", "ФормаДокумента"),
        "Document/ЗаказПокупателя/Forms/ФормаДокумента",
    ),
    (("", "", "", ""), "///"),
    (
        ("CommonForm", "ОбщаяФорма", "", "Форма"),
        "CommonForm/ОбщаяФорма//Форма",
    ),
    (
        ("External", "Обработка", "Forms", "Форма|Спец"),
        "External/Обработка/Forms/Форма|Спец",
    ),
)

BASELINE_FINGERPRINT = (
    "0ed1995d45f10dae2b1ca6ccc446ba85433020dc3a068e1064382c7916e57c21"
)


def test_public_form_key_exists_and_is_documented() -> None:
    assert callable(form_key)
    assert form_key.__doc__ is not None
    assert form_key.__doc__.strip(), "публичный form_key обязан иметь docstring"


def test_private_name_is_alias_of_public_function() -> None:
    assert drift_checker._form_key is form_key


def test_signature_is_unchanged() -> None:
    params = list(inspect.signature(form_key).parameters)
    assert params == EXPECTED_PARAMS


def test_key_separator_is_unchanged() -> None:
    assert drift_checker._KEY_SEP == "/"


@pytest.mark.parametrize("args,expected", KEY_CASES)
def test_keys_match_baseline_byte_for_byte(
    args: tuple[str, str, str, str], expected: str
) -> None:
    assert form_key(*args) == expected
    assert drift_checker._form_key(*args) == expected


def test_fingerprint_of_all_cases_matches_baseline() -> None:
    joined = "\n".join(form_key(*args) for args, _ in KEY_CASES)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    assert digest == BASELINE_FINGERPRINT


def test_reindex_uses_public_name_with_deferred_import() -> None:
    source = inspect.getsource(FormRouter.reindex)
    assert "from v8unpack_agent.drift_checker import form_key" in source
    assert "_form_key" not in source


def test_form_key_is_not_exported_from_package_root() -> None:
    assert "form_key" not in v8unpack_agent.__all__
    with pytest.raises(AttributeError):
        _ = v8unpack_agent.form_key
