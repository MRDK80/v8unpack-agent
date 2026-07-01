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
"""

from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_paths import all_module_paths, form_paths, form_root, item_modules
from v8unpack_agent.forms_index import FormsIndex, FormsIndexEntry, is_form_stale
from v8unpack_agent.skd_extractor import (
    SkdBatchResult,
    SkdResult,
    extract_all_skd_queries,
    extract_skd_queries,
)
from v8unpack_agent.elem_parser import ElemIndexResult, parse_elem_json
from v8unpack_agent.pipeline import (
    ErfUnpacker,
    FormUnpacker,
    discover_form_bins,
    unpack_all_forms,
    unpack_erf,
    update_forms_index,
)
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex, scan_forms
from v8unpack_agent.drift_checker import DriftReport, check_drift
from v8unpack_agent.form_router import FormRouter, RouteResult

__all__ = [
    "form_paths",
    "form_root",
    "item_modules",
    "all_module_paths",
    "FormArtifact",
    "FormsIndex",
    "FormsIndexEntry",
    "is_form_stale",
    "FormUnpacker",
    "ErfUnpacker",
    "discover_form_bins",
    "unpack_all_forms",
    "unpack_erf",
    "update_forms_index",
    "SkdResult",
    "extract_skd_queries",
    "ElemIndexResult",
    "parse_elem_json",
    "SkdBatchResult",
    "extract_all_skd_queries",
    "scan_forms",
    "FormEntry",
    "FormScanIndex",
    "check_drift",
    "DriftReport",
    "FormRouter",
    "RouteResult",
]
