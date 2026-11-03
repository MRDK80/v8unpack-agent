"""Сводка резолюции ссылочных типов в post-run report — issue #288.

UUID и имена синтетические. Классификатор перенесён без изменений из
``examples/unresolved_refs_report.py``; здесь проверяются инварианты сводки,
схема 2 отчёта, машинный код предупреждения и обезличенность.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from v8unpack_agent import runner as _runner_at_import  # noqa: F401
from v8unpack_agent.platform_reference_types import PLATFORM_REFERENCE_TYPES
from v8unpack_agent.reference_resolution import (
    RESIDUAL_CLASSES,
    ReferenceResolutionCollector,
    ReferenceResolutionSummary,
)
from v8unpack_agent.run_report import (
    PostRunReport,
    RunReportValidationError,
    RunSummary,
)
from v8unpack_agent.scan_forms import (
    SCAN_WARNING_CODES,
    SCAN_WARNING_REFERENCE_TYPES_UNRESOLVED,
    scan_warning_code,
)

UUID_A = "00000000-0000-0000-0000-0000000000aa"
UUID_B = "00000000-0000-0000-0000-0000000000bb"
STATIC_NAME = min(PLATFORM_REFERENCE_TYPES.values())
STARTED = "2026-11-03T00:00:00Z"
FINISHED = "2026-11-03T00:00:01Z"


def attrs(*types: str) -> list[dict[str, str]]:
    return [{"Name": f"Р{i}", "Type": t} for i, t in enumerate(types)]


def zeros() -> dict[str, int]:
    return dict.fromkeys(RESIDUAL_CLASSES, 0)


def test_collector_counts_and_class_sum(tmp_path: Path) -> None:
    c = ReferenceResolutionCollector()
    c.add_attributes(attrs("CatalogRef.Товары", STATIC_NAME, "Ref#" + UUID_A, "String"))
    c.add_attributes(attrs("Ref#" + UUID_A, "Ref#" + UUID_B))
    c.add_attributes(None)
    s = c.summarize(tmp_path, index_uuids=[])
    assert (s.applicable, s.resolved, s.unresolved, s.unresolved_uuids) == (5, 2, 3, 2)
    assert sum(s.by_class.values()) == s.unresolved
    assert sum(s.by_class_uuids.values()) == s.unresolved_uuids
    # Без позиции идентичности в выгрузке классификатор честно даёт ambiguous.
    assert s.by_class["ambiguous"] == 3


def test_empty_residual(tmp_path: Path) -> None:
    c = ReferenceResolutionCollector()
    c.add_attributes(attrs("CatalogRef.Товары", "String"))
    s = c.summarize(tmp_path, index_uuids=[UUID_A])
    assert (s.applicable, s.resolved, s.unresolved, s.unresolved_uuids) == (1, 1, 0, 0)
    assert s.by_class == zeros() and s.by_class_uuids == zeros()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"applicable": 3, "resolved": 1, "unresolved": 1, "unresolved_uuids": 0},
        {"applicable": 2, "resolved": 1, "unresolved": 1, "unresolved_uuids": 0},
    ],
)
def test_summary_invariants_are_enforced(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ReferenceResolutionSummary(by_class=zeros(), by_class_uuids=zeros(), **kwargs)


def test_report_schema_2_serializes_block_and_schema_1_rejects_it() -> None:
    s = ReferenceResolutionSummary(1, 0, 1, 1, {**zeros(), "reference_only": 1}, {**zeros(), "reference_only": 1})
    common = {"completed": True, "started_at": STARTED, "finished_at": FINISHED,
              "summary": RunSummary.from_objects(()), "objects": ()}
    data = json.loads(PostRunReport(schema_version=2, reference_resolution=s, **common).to_json())
    assert data["schema_version"] == 2
    assert data["summary"]["reference_resolution"] == {
        "applicable": 1,
        "resolved": 0,
        "unresolved": 1,
        "unresolved_uuids": 1,
        "unresolved_by_class": {**zeros(), "reference_only": 1},
        "unresolved_uuids_by_class": {**zeros(), "reference_only": 1},
    }
    with pytest.raises(RunReportValidationError):
        PostRunReport(schema_version=1, reference_resolution=s, **common)


def test_warning_code_registered() -> None:
    assert SCAN_WARNING_REFERENCE_TYPES_UNRESOLVED in SCAN_WARNING_CODES


class _Ctx:
    def __init__(self, attributes):
        self.object_attributes = attributes


def _run(monkeypatch, tmp_path: Path, contexts: list):
    """Реальный runner на пустой выгрузке; формы и контексты подменены.

    Модуль берётся из sys.modules в момент вызова: соседние тесты
    перезагружают пакет (lazy imports), и ссылка времени импорта устаревает.
    """
    runner = importlib.import_module("v8unpack_agent.runner")

    class _Entry:
        form_path = "Catalog/X/Forms/F"
        elem_json_path = None
        warnings = ()

    class _Index:
        def __init__(self) -> None:
            self.forms = [_Entry() for _ in contexts]
            self.scan_warnings: list[str] = []
            self.reference_types = {UUID_B: "CatalogRef.Б"}

        def resolve_reference_type(self, uuid):
            return None

    it = iter(contexts)
    monkeypatch.setattr(runner, "scan_forms", lambda *a, **k: _Index())
    monkeypatch.setattr(runner, "build_form_context", lambda *a, **k: next(it))
    monkeypatch.setattr(runner, "_fallback_form_id", lambda entry, root: "Catalog/X/F")
    monkeypatch.setattr(runner, "_context_form_id", lambda ctx, fid: fid)
    monkeypatch.setattr(runner, "to_llm_prompt_fragment", lambda ctx, limit: "фрагмент")
    monkeypatch.setattr(runner, "_probe_elem_index", lambda ctx, root: (True, None))
    return runner.run_pipeline(
        runner.RunOptions(export_root=tmp_path, include_common_modules=False, include_skd=False)
    )


def test_runner_reports_summary_warning_and_keeps_status(monkeypatch, tmp_path: Path) -> None:
    out = _run(monkeypatch, tmp_path, [_Ctx(attrs("Ref#" + UUID_A, "CatalogRef.Б")), _Ctx(attrs("Ref#" + UUID_A))])
    data = json.loads(out.report.to_json())
    block = data["summary"]["reference_resolution"]
    assert (block["applicable"], block["resolved"], block["unresolved"], block["unresolved_uuids"]) == (3, 1, 2, 1)
    codes = [scan_warning_code(w) for w in out.scan_warnings]
    assert codes.count(SCAN_WARNING_REFERENCE_TYPES_UNRESOLVED) == 1
    text = out.report.to_json() + "\n".join(out.scan_warnings)
    assert UUID_A not in text and UUID_B not in text
    # Остаток не меняет статусы объектов (решение владельца по п.4 #288).
    assert data["run"]["completed"] is True
    assert data["summary"]["complete"] == 2
    assert data["summary"]["partial"] == 0 and data["summary"]["failed"] == 0


def test_runner_empty_residual_has_no_warning(monkeypatch, tmp_path: Path) -> None:
    out = _run(monkeypatch, tmp_path, [_Ctx(attrs("CatalogRef.Б"))])
    assert json.loads(out.report.to_json())["summary"]["reference_resolution"]["unresolved"] == 0
    assert SCAN_WARNING_REFERENCE_TYPES_UNRESOLVED not in "".join(out.scan_warnings)


def test_two_identical_runs_give_identical_summary(monkeypatch, tmp_path: Path) -> None:
    make = lambda: [_Ctx(attrs("Ref#" + UUID_A, "Ref#" + UUID_B, STATIC_NAME))]
    first = json.loads(_run(monkeypatch, tmp_path, make()).report.to_json())["summary"]
    second = json.loads(_run(monkeypatch, tmp_path, make()).report.to_json())["summary"]
    assert first["reference_resolution"] == second["reference_resolution"]
