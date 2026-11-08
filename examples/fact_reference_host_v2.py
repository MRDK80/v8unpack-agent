"""Versioned provider-metadata adapter; unchanged R11 facts/selection policy.

Категория: импортируемый reference-компонент, opt-in v2 исследования #497.
Входные данные: те же host TaskEvidence/output/scope и native Ollama calls.
Ожидаемый результат: прежняя проверка ссылок/validator, явный отказ или report.
Поведение без данных: импорт/запуск не вызывает модель и ничего не пишет.
"""
from __future__ import annotations

import copy
from typing import Any

from examples.fact_reference_host import SYSTEM, strict_proposal
from examples.fact_reference_host import FactReferenceHost as OriginalHost

__all__ = ['SYSTEM', 'FactReferenceHost', 'strict_proposal']


class FactReferenceHost(OriginalHost):
    """Accept only observed provider index=0 metadata for a single call.

    No prompt, payload, schema, selection check, materialization or writer change.
    v1 is retained unchanged; native v1 rejected this Ollama metadata.
    """
    def native(self, calls: Any) -> dict[str, Any]:
        if type(calls) is list and len(calls) == 1 and type(calls[0]) is dict:
            function = calls[0].get('function')
            if type(function) is dict and 'index' in function:
                if type(function['index']) is not int or function['index'] != 0:
                    return {'status':'blocked', 'reason':'invalid_native_index'}
                calls = copy.deepcopy(calls)
                del calls[0]['function']['index']
        return super().native(calls)
