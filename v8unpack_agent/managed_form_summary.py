"""DEPRECATED-шим: используйте :mod:`v8unpack_agent.form_summary`.

Модуль переименован в рамках issue #69. Имя с префиксом ``Managed*`` вводило
в заблуждение — выжимка строится для ЛЮБОЙ elem-формы (обычной и управляемой),
никакой привязки к управляемым формам нет.

Старые публичные имена сохранены как deprecated-алиасы к новым символам из
:mod:`v8unpack_agent.form_summary`. При обращении к ним выдаётся
:class:`DeprecationWarning`. Уберите этот шим в будущем мажорном релизе.

Соответствие имён:

- ``ManagedFormSummary``                        → ``FormSummary``
- ``build_managed_form_summary``                → ``build_form_summary``
- ``build_managed_form_summary_from_elem_index``→ ``build_form_summary_from_elem_index``

``to_normalized_json`` не переименовывалась и реэкспортируется без warning.
"""

from __future__ import annotations

import warnings
from typing import Any

from v8unpack_agent.form_summary import (
    FormSummary,
    build_form_summary,
    build_form_summary_from_elem_index,
    to_normalized_json,
)

__all__ = [
    "ManagedFormSummary",
    "build_managed_form_summary",
    "build_managed_form_summary_from_elem_index",
    "to_normalized_json",
]

#: Старое имя → (новое имя, новый объект).
_DEPRECATED: dict[str, tuple[str, Any]] = {
    "ManagedFormSummary": ("FormSummary", FormSummary),
    "build_managed_form_summary": ("build_form_summary", build_form_summary),
    "build_managed_form_summary_from_elem_index": (
        "build_form_summary_from_elem_index",
        build_form_summary_from_elem_index,
    ),
}


def __getattr__(name: str) -> Any:
    """PEP 562: warn on access to deprecated aliases.

    Warning срабатывает при каждом обращении к устаревшему имени, а не только
    при первом импорте модуля, — так каждый тест/пользователь видит подсказку
    незави��имо от кеша модулей.
    """
    entry = _DEPRECATED.get(name)
    if entry is not None:
        new_name, obj = entry
        warnings.warn(
            f"{name} устарело и будет удалено; используйте "
            f"v8unpack_agent.form_summary.{new_name}",
            DeprecationWarning,
            stacklevel=2,
        )
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
