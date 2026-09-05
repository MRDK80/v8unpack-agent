"""v8unpack-agent — надстройка над v8unpack для агентных пайплайнов.

Реализует доработки из статьи «Обычные формы 1С в агентном пайплайне:
пошаговая распаковка»: фабрику путей по конвенции, FormArtifact с флагом
полноты распаковки, forms_index с контролем рассинхрона и распаковку как
pre-step индексации.

Публичная поверхность
---------------------
- :func:`~v8unpack_agent.form_paths.form_paths`
- :func:`~v8unpack_agent.form_paths.item_modules`
- :func:`~v8unpack_agent.form_paths.all_module_paths`
- :class:`~v8unpack_agent.form_artifact.FormArtifact`
- :class:`~v8unpack_agent.forms_index.FormsIndex`
- :class:`~v8unpack_agent.forms_index.FormsIndexEntry`
- :func:`~v8unpack_agent.forms_index.is_form_stale`
- :func:`~v8unpack_agent.pipeline.unpack_all_forms`
- :func:`~v8unpack_agent.pipeline.update_forms_index`
- :func:`~v8unpack_agent.pipeline.discover_form_bins`
- :func:`~v8unpack_agent.scan_forms.scan_forms`
- :class:`~v8unpack_agent.scan_forms.FormEntry`
- :class:`~v8unpack_agent.scan_forms.FormScanIndex`
- :func:`~v8unpack_agent.drift_checker.check_drift`
- :class:`~v8unpack_agent.drift_checker.DriftReport`
- :func:`~v8unpack_agent.managed_forms.discover_elem_forms` (issue #55)
- :class:`~v8unpack_agent.managed_forms.ElemFormEntry` (issue #55)
- :class:`~v8unpack_agent.form_summary.FormSummary` (issue #69)
- :func:`~v8unpack_agent.form_summary.build_form_summary` (issue #69)
- :func:`~v8unpack_agent.form_summary.build_form_summary_from_elem_index` (issue #69)
- :class:`~v8unpack_agent.form_classifier.FormClass` (issue #98)
- :func:`~v8unpack_agent.form_classifier.classify_form` (issue #98)
- :func:`~v8unpack_agent.form_classifier.classify_form_by_name` (issue #98)
- :func:`~v8unpack_agent.form_classifier.classify_form_by_bindings` (issue #98)
"""

from v8unpack_agent.form_paths import (
    all_module_paths,
    form_paths,
    form_root,
    item_modules,
)


def __getattr__(name: str):
    """Lazy-load selected exports to keep `python -m v8unpack_agent.scan_forms` clean."""
    if name in {
        "SERVICE_FORM_NAME_PATTERNS",
        "FormClass",
        "classify_form",
        "classify_form_by_bindings",
        "classify_form_by_name",
    }:
        # Ленивая группа form_classifier (issue #140, часть A). Вместе с ней
        # перестаёт загружаться транзитивный coverage_metric: его тянет
        # form_classifier ради DATA_ELEMENT_TYPES.
        from v8unpack_agent.form_classifier import (
            SERVICE_FORM_NAME_PATTERNS,
            FormClass,
            classify_form,
            classify_form_by_bindings,
            classify_form_by_name,
        )

        values = {
            "SERVICE_FORM_NAME_PATTERNS": SERVICE_FORM_NAME_PATTERNS,
            "FormClass": FormClass,
            "classify_form": classify_form,
            "classify_form_by_bindings": classify_form_by_bindings,
            "classify_form_by_name": classify_form_by_name,
        }
        globals().update(values)
        return values[name]
    if name in {"FormRouter", "RouteResult"}:
        # Ленивая группа form_router (issue #140, часть B). Публичные имена
        # FormRouter и RouteResult остаются в __all__ и доступны как раньше.
        from v8unpack_agent.form_router import FormRouter, RouteResult

        values = {
            "FormRouter": FormRouter,
            "RouteResult": RouteResult,
        }
        globals().update(values)
        return values[name]
    if name in {"FormsIndex", "FormsIndexEntry", "is_form_stale"}:
        from v8unpack_agent.forms_index import (
            FormsIndex,
            FormsIndexEntry,
            is_form_stale,
        )

        values = {
            "FormsIndex": FormsIndex,
            "FormsIndexEntry": FormsIndexEntry,
            "is_form_stale": is_form_stale,
        }
        globals().update(values)
        return values[name]

    if name in {"SkdResult", "SkdBatchResult", "extract_skd_queries",
                "extract_all_skd_queries"}:
        from v8unpack_agent.skd_extractor import (
            SkdBatchResult,
            SkdResult,
            extract_all_skd_queries,
            extract_skd_queries,
        )

        values = {
            "SkdResult": SkdResult,
            "SkdBatchResult": SkdBatchResult,
            "extract_skd_queries": extract_skd_queries,
            "extract_all_skd_queries": extract_all_skd_queries,
        }
        globals().update(values)
        return values[name]

    if name in {"ElemIndexResult", "parse_elem_json"}:
        from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json

        values = {
            "ElemIndexResult": ElemIndexResult,
            "parse_elem_json": parse_elem_json,
        }
        globals().update(values)
        return values[name]

    if name in {"FormUnpacker", "ErfUnpacker", "discover_form_bins",
                "unpack_all_forms", "unpack_erf", "update_forms_index"}:
        from v8unpack_agent.pipeline import (
            ErfUnpacker,
            FormUnpacker,
            discover_form_bins,
            unpack_all_forms,
            unpack_erf,
            update_forms_index,
        )

        values = {
            "FormUnpacker": FormUnpacker,
            "ErfUnpacker": ErfUnpacker,
            "discover_form_bins": discover_form_bins,
            "unpack_all_forms": unpack_all_forms,
            "unpack_erf": unpack_erf,
            "update_forms_index": update_forms_index,
        }
        globals().update(values)
        return values[name]
    
    if name in {"FormArtifact"}:
        from v8unpack_agent.form_artifact import FormArtifact

        values = {
            "FormArtifact": FormArtifact,
        }
        globals().update(values)
        return values[name]

    if name in {"check_drift", "DriftReport"}:
        from v8unpack_agent.drift_checker import DriftReport, check_drift

        values = {
            "check_drift": check_drift,
            "DriftReport": DriftReport,
        }
        globals().update(values)
        return values[name]

    if name in {"scan_forms", "FormEntry", "FormScanIndex"}:
        from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms

        values = {
            "scan_forms": scan_forms,
            "FormEntry": FormEntry,
            "FormScanIndex": FormScanIndex,
        }
        globals().update(values)
        return values[name]

    if name in {"discover_elem_forms", "ElemFormEntry",
                "discover_managed_forms", "ManagedFormEntry"}:
        from v8unpack_agent.managed_forms import (
            ElemFormEntry,
            ManagedFormEntry,
            discover_elem_forms,
            discover_managed_forms,
        )

        values = {
            "discover_elem_forms": discover_elem_forms,
            "ElemFormEntry": ElemFormEntry,
            "discover_managed_forms": discover_managed_forms,
            "ManagedFormEntry": ManagedFormEntry,
        }
        globals().update(values)
        return values[name]

    if name in {"FormSummary", "build_form_summary",
                "build_form_summary_from_elem_index"}:
        from v8unpack_agent.form_summary import (
            FormSummary,
            build_form_summary,
            build_form_summary_from_elem_index,
        )

        values = {
            "FormSummary": FormSummary,
            "build_form_summary": build_form_summary,
            "build_form_summary_from_elem_index": build_form_summary_from_elem_index,
        }
        globals().update(values)
        return values[name]

    if name in {"FormContext", "build_form_context",
                "to_llm_prompt_fragment"}:
        from v8unpack_agent.form_context import (
            FormContext,
            build_form_context,
            to_llm_prompt_fragment,
        )

        values = {
            "FormContext": FormContext,
            "build_form_context": build_form_context,
            "to_llm_prompt_fragment": to_llm_prompt_fragment,
        }
        globals().update(values)
        return values[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Публичная поверхность пакета. Порядок — isort-style (требование RUF022):
# SCREAMING_SNAKE_CASE → CamelCase → snake_case. Порядок не является
# контрактом: тесты #124/#128/#131/#134 проверяют состав через set()/in,
# а не последовательность. Ленивые имена разрешаются через PEP 562
# __getattr__, сортировка не делает импорты eager.
#
# Происхождение экспортов (карта сохранена при сортировке):
#   issue #55                                   — discover_elem_forms
#                                                 ElemFormEntry
#   deprecated aliases (обратная совместимость) — discover_managed_forms
#                                                 ManagedFormEntry
#   issue #69                                   — FormSummary
#                                                 build_form_summary
#                                                 build_form_summary_from_elem_index
#   issue #124 (form_context, issue #77)        — FormContext
#                                                 build_form_context
#                                                 to_llm_prompt_fragment
#   issue #98                                   — FormClass
#                                                 classify_form
#                                                 classify_form_by_name
#                                                 classify_form_by_bindings
#                                                 SERVICE_FORM_NAME_PATTERNS
__all__ = [
    "SERVICE_FORM_NAME_PATTERNS",
    "DriftReport",
    "ElemFormEntry",
    "ElemIndexResult",
    "ErfUnpacker",
    "FormArtifact",
    "FormClass",
    "FormContext",
    "FormEntry",
    "FormRouter",
    "FormScanIndex",
    "FormSummary",
    "FormUnpacker",
    "FormsIndex",
    "FormsIndexEntry",
    "ManagedFormEntry",
    "RouteResult",
    "SkdBatchResult",
    "SkdResult",
    "all_module_paths",
    "build_form_context",
    "build_form_summary",
    "build_form_summary_from_elem_index",
    "check_drift",
    "classify_form",
    "classify_form_by_bindings",
    "classify_form_by_name",
    "discover_elem_forms",
    "discover_form_bins",
    "discover_managed_forms",
    "extract_all_skd_queries",
    "extract_skd_queries",
    "form_paths",
    "form_root",
    "is_form_stale",
    "item_modules",
    "parse_elem_json",
    "scan_forms",
    "to_llm_prompt_fragment",
    "unpack_all_forms",
    "unpack_erf",
    "update_forms_index",
]
