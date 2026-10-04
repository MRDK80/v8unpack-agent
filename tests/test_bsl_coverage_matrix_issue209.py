"""Итоговая матрица BSL-покрытия: сквозной синтетический fixture (issue #209).

Fixture содержит все доказанные в #202 layout индекса модулей, форму, общий
модуль, граничные состояния и неподдержанные файлы. Данные синтетические:
имена владельцев условные, production-выгрузка не используется.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from v8unpack_agent.configuration_modules import CONFIGURATION_MODULE_FILES
from v8unpack_agent.metadata_modules import METADATA_OBJECT_MODULE_LAYOUTS
from v8unpack_agent.modules import MODULE_KINDS
from v8unpack_agent.record_set_modules import RECORD_SET_MODULE_FILES
from v8unpack_agent.run_report import RunObjectKind
from v8unpack_agent.runner import RunOptions, RunOutcome, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "docs" / "bsl_coverage_matrix.md"
README = REPO_ROOT / "README.md"

BSL = "Процедура Тест()\nКонецПроцедуры\n".encode()
OWNER = "Alpha"
COMMAND_TYPES = ("Catalog", "DataProcessor", "Document", "InformationRegister", "Report")
SERVICE_TYPES = ("HTTPService", "WebService")
FORM_ID = "Catalog/Alpha/CatalogForm/Main"
COMMON_MODULE_ID = "CommonModule/Shared/CommonModule.obj.bsl"

BOUNDARY: dict[str, tuple[str, bytes | None, str]] = {
    "Catalog/Beta/Catalog.obj.bsl": ("object", b"", "empty"),
    "Catalog/Beta/Catalog.mgr.bsl": ("manager", None, "missing"),
    "InformationRegister/Beta/InformationRegister.obj.bsl": (
        "record_set",
        b"  \n",
        "whitespace_only",
    ),
    "InformationRegister/Beta/InformationRegister.mgr.bsl": ("manager", None, "missing"),
    "Constant/Beta/Constant.obj.bsl": ("value_manager", None, "missing"),
    "WebService/Beta/WebService.obj.bsl": ("service", b"\xef\xbb\xbf", "empty"),
    "CommonCommand/Beta/CommonCommand.obj.bsl": ("command", b"\t\n", "whitespace_only"),
    "Document/Beta/Document.obj.bsl": ("object", b"\xff\xfe\xfa", "read_error"),
    "Document/Beta/Document.mgr.bsl": ("manager", None, "missing"),
}

UNSUPPORTED = (
    "Sequences/Alpha/Sequences.obj.bsl",
    "ChartOfAccounts/Alpha/ChartOfAccounts.obj.bsl",
    "ChartOfCalculationTypes/Alpha/ChartOfCalculationTypes.obj.bsl",
    "AccountingRegister/Alpha/AccountingRegister.obj.bsl",
    "BusinessProcess/Alpha/BusinessProcess.mgr.bsl",
    "ConfigurationExtension.app.bsl",
)

UNSUPPORTED_DOC_PATTERNS = (
    "Sequences.obj.bsl",
    "ChartOfAccounts.obj.bsl",
    "ChartOfCalculationTypes.obj.bsl",
    "AccountingRegister.obj.bsl",
    "ExternalDataProcessor.obj.bsl",
)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _supported() -> dict[str, str]:
    """Относительный путь -> module_kind для каждого доказанного layout."""
    paths: dict[str, str] = {}
    for kind, name in CONFIGURATION_MODULE_FILES.items():
        paths[name] = kind
    for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items():
        for kind, name in layout.items():
            paths[f"{metadata_type}/{OWNER}/{name}"] = kind
    for metadata_type, name in RECORD_SET_MODULE_FILES.items():
        paths[f"{metadata_type}/{OWNER}/{name}"] = "record_set"
    paths[f"Constant/{OWNER}/Constant.obj.bsl"] = "value_manager"
    for metadata_type in SERVICE_TYPES:
        paths[f"{metadata_type}/{OWNER}/{metadata_type}.obj.bsl"] = "service"
    paths[f"CommonCommand/{OWNER}/CommonCommand.obj.bsl"] = "command"
    for metadata_type in COMMAND_TYPES:
        container = f"{metadata_type}Command"
        paths[f"{metadata_type}/{OWNER}/{container}/Run/{container}.obj.bsl"] = "command"
    return paths


def _write(root: Path, relative: str, payload: bytes) -> None:
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _build_export(root: Path) -> None:
    for relative in _supported():
        _write(root, relative, BSL)
    for relative, (_kind, payload, _status) in BOUNDARY.items():
        if payload is None:
            root.joinpath(*relative.split("/")[:-1]).mkdir(parents=True, exist_ok=True)
        else:
            _write(root, relative, payload)
    for relative in UNSUPPORTED:
        _write(root, relative, BSL)
    _write(root, f"{FORM_ID}/CatalogForm.obj.bsl", BSL)
    _write(root, COMMON_MODULE_ID, BSL)


def _run(root: Path) -> RunOutcome:
    return run_pipeline(RunOptions(export_root=root, include_skd=False, include_module_index=True))


def _module_objects(outcome: RunOutcome) -> dict[str, tuple[str, str, str | None]]:
    return {
        item.object: (item.object_kind.value, item.status.value, item.reason_code)
        for item in outcome.report.objects
        if item.object_kind.value.startswith("module_")
    }


def _expected() -> dict[str, tuple[str, str, str | None]]:
    expected: dict[str, tuple[str, str, str | None]] = {
        path: (f"module_{kind}", "complete", None) for path, kind in _supported().items()
    }
    for path, (kind, _payload, status) in BOUNDARY.items():
        report_status = "failed" if status == "read_error" else "excluded"
        expected[path] = (f"module_{kind}", report_status, status)
    return expected


def _without_time(outcome: RunOutcome) -> dict[str, Any]:
    data: dict[str, Any] = dict(outcome.report.to_dict())
    run = dict(data["run"])
    run.pop("started_at")
    run.pop("finished_at")
    data["run"] = run
    return data


def test_fixture_covers_every_proven_layout() -> None:
    kinds = set(_supported().values())
    assert kinds == set(MODULE_KINDS) - {"form", "common_module"}
    assert len(_supported()) == 32


def test_end_to_end_module_statuses(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = _run(tmp_path)
    assert outcome.completed
    assert _module_objects(outcome) == _expected()


def test_every_kind_has_positive_and_boundary_case() -> None:
    positive = set(_supported().values())
    boundary = {kind for kind, _payload, _status in BOUNDARY.values()}
    boundary |= set(CONFIGURATION_MODULE_FILES)
    assert positive == boundary


def test_configuration_boundary_statuses(tmp_path: Path) -> None:
    _write(tmp_path, CONFIGURATION_MODULE_FILES["managed_application"], b"")
    outcome = _run(tmp_path)
    statuses = {kind: status for kind, status, _reason in _module_objects(outcome).values()}
    reasons = {kind: reason for kind, _status, reason in _module_objects(outcome).values()}
    assert set(statuses) == {f"module_{kind}" for kind in CONFIGURATION_MODULE_FILES}
    assert reasons["module_managed_application"] == "empty"
    others = {name for name in reasons if name != "module_managed_application"}
    assert {reasons[name] for name in others} == {"missing"}


def test_form_and_common_module_are_counted_once(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = _run(tmp_path)
    ids = [item.object for item in outcome.report.objects]
    assert len(ids) == len(set(ids))
    forms = [o.object for o in outcome.report.objects if o.object_kind is RunObjectKind.FORM]
    commons = [
        o.object for o in outcome.report.objects if o.object_kind is RunObjectKind.COMMON_MODULE
    ]
    assert forms == [FORM_ID]
    assert commons == [COMMON_MODULE_ID]
    module_ids = set(_module_objects(outcome))
    assert COMMON_MODULE_ID not in module_ids
    assert not any(path.startswith(FORM_ID + "/") for path in module_ids)


def test_unsupported_layouts_produce_no_objects(tmp_path: Path) -> None:
    _build_export(tmp_path)
    ids = {item.object for item in _run(tmp_path).report.objects}
    assert not ids & set(UNSUPPORTED)


def test_summary_modules_matches_objects(tmp_path: Path) -> None:
    _build_export(tmp_path)
    outcome = _run(tmp_path)
    summary: Any = outcome.report.to_dict()["summary"]
    table = summary["modules"]
    by_kind = table["by_kind"]
    expected_totals = Counter(kind for kind, _status, _reason in _expected().values())
    assert {f"module_{name}": row["total"] for name, row in by_kind.items()} == dict(
        expected_totals
    )
    assert by_kind["object"]["read_error"] == 1
    assert by_kind["object"]["empty"] == 1
    assert by_kind["record_set"]["whitespace_only"] == 1
    assert set(table["record_set_by_metadata_type"]) == set(RECORD_SET_MODULE_FILES)
    assert outcome.report.summary.failed >= 1


def test_two_runs_give_identical_report(tmp_path: Path) -> None:
    _build_export(tmp_path)
    first = _without_time(_run(tmp_path))
    second = _without_time(_run(tmp_path))
    assert first == second


def test_matrix_lists_every_layout_and_remainder() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    for kind in MODULE_KINDS:
        assert f"`{kind}`" in text, kind
    for name in CONFIGURATION_MODULE_FILES.values():
        assert name in text, name
    for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items():
        assert f"`{metadata_type}`" in text or f"{metadata_type}/" in text, metadata_type
        for name in layout.values():
            suffix = name.split(".", 1)[1]
            assert f"{{Type}}.{suffix}" in text or name in text, name
    for name in RECORD_SET_MODULE_FILES.values():
        assert name in text, name
    for pattern in UNSUPPORTED_DOC_PATTERNS:
        assert pattern in text, pattern


def test_matrix_is_impersonal() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    assert "\\" not in text
    assert ":/" not in text
    assert UUID_RE.search(text) is None
    for line in text.splitlines():
        assert not line.strip().startswith("/")


def test_readme_links_to_matrix() -> None:
    text = README.read_text(encoding="utf-8")
    assert "docs/bsl_coverage_matrix.md" in text
    assert "docs/modules.md" in text
