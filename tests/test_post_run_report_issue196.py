"""Контракт post-run report для issue #196."""

import json
from enum import Enum

import pytest

from v8unpack_agent.run_report import (
    ObjectRunResult,
    PostRunReport,
    RunFatalError,
    RunObjectKind,
    RunObjectStatus,
    RunReportValidationError,
    RunReportWriteError,
    RunSummary,
    common_module_status,
    decode_error_reason_code,
    skd_status,
    unindexed_reason_code,
    write_post_run_report,
)

STARTED = "2026-01-01T00:00:00Z"
FINISHED = "2026-01-01T00:00:01Z"


def _complete(name: str = "Document/Example/Form/Main") -> ObjectRunResult:
    return ObjectRunResult(
        object=name,
        object_kind=RunObjectKind.FORM,
        status=RunObjectStatus.COMPLETE,
    )


def _report(*objects: ObjectRunResult) -> PostRunReport:
    items = tuple(objects)
    return PostRunReport(
        schema_version=1,
        completed=True,
        started_at=STARTED,
        finished_at=FINISHED,
        summary=RunSummary.from_objects(items),
        objects=items,
    )


def test_summary_keeps_excluded_outside_found():
    objects = (
        _complete(),
        ObjectRunResult(
            object="CommonModule/Example",
            object_kind=RunObjectKind.COMMON_MODULE,
            status=RunObjectStatus.EXCLUDED,
            reason_code="not_selected",
        ),
    )

    summary = RunSummary.from_objects(objects)

    assert summary.found == 1
    assert summary.excluded == 1
    assert summary.discovered == 2


def test_summary_rejects_broken_invariant():
    with pytest.raises(RunReportValidationError, match="found must equal"):
        RunSummary(found=2, complete=1, partial=0, failed=0)


@pytest.mark.parametrize("status", [RunObjectStatus.PARTIAL, RunObjectStatus.FAILED])
def test_degraded_result_requires_stage_and_reason(status):
    with pytest.raises(RunReportValidationError, match="require stage"):
        ObjectRunResult(
            object="Document/Example/Form/Main",
            object_kind=RunObjectKind.FORM,
            status=status,
        )


def test_complete_rejects_degradation_details():
    with pytest.raises(RunReportValidationError, match="must not contain"):
        ObjectRunResult(
            object="Document/Example/Form/Main",
            object_kind=RunObjectKind.FORM,
            status=RunObjectStatus.COMPLETE,
            reason_code="unexpected_detail",
        )


def test_report_rejects_summary_not_matching_objects():
    with pytest.raises(RunReportValidationError, match="do not match"):
        PostRunReport(
            schema_version=1,
            completed=True,
            started_at=STARTED,
            finished_at=FINISHED,
            summary=RunSummary(found=0, complete=0, partial=0, failed=0),
            objects=(_complete(),),
        )


def test_report_orders_objects_and_json_deterministically():
    report = _report(
        _complete("Document/Z/Form/Main"),
        _complete("Document/A/Form/Main"),
    )

    first = report.to_json()
    second = report.to_json()
    payload = json.loads(first)

    assert first == second
    assert [item["object"] for item in payload["objects"]] == [
        "Document/A/Form/Main",
        "Document/Z/Form/Main",
    ]


def test_fatal_report_requires_sanitized_error():
    error = RunFatalError(
        reason_code="pipeline_error",
        error_type="runtime_error",
        message="Управляемое завершение",
    )
    report = PostRunReport(
        schema_version=1,
        completed=False,
        started_at=STARTED,
        finished_at=FINISHED,
        summary=RunSummary(found=0, complete=0, partial=0, failed=0),
        objects=(),
        fatal_error=error,
    )

    assert report.to_dict()["fatal_error"] == {
        "reason_code": "pipeline_error",
        "error_type": "runtime_error",
        "message": "Управляемое завершение",
    }


def test_report_rejects_absolute_path_and_traceback():
    with pytest.raises(RunReportValidationError, match="absolute path"):
        _complete("/private/example")
    with pytest.raises(RunReportValidationError, match="single-line"):
        RunFatalError(
            reason_code="pipeline_error",
            error_type="runtime_error",
            message="Traceback\nsecret",
        )


def test_writer_round_trips_utf8(tmp_path):
    target = tmp_path / "post-run.json"
    report = _report(_complete("Документ/Пример/Форма/Основная"))

    write_post_run_report(report, target)

    assert json.loads(target.read_text(encoding="utf-8")) == report.to_dict()


def test_writer_preserves_old_target_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "post-run.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("v8unpack_agent.run_report.os.replace", fail_replace)

    with pytest.raises(RunReportWriteError, match="post-run.json"):
        write_post_run_report(_report(_complete()), target)

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("*.tmp")) == []


def test_writer_requires_existing_parent(tmp_path):
    target = tmp_path / "missing" / "post-run.json"

    with pytest.raises(RunReportWriteError, match="does not exist"):
        write_post_run_report(_report(_complete()), target)


class _SyntheticReason(str, Enum):
    VALUE = "existing_reason"


def test_existing_enum_values_are_reused():
    assert unindexed_reason_code(_SyntheticReason.VALUE) == "existing_reason"
    assert decode_error_reason_code(_SyntheticReason.VALUE) == "existing_reason"


@pytest.mark.parametrize(
    ("read_status", "expected"),
    [
        ("ok", (RunObjectStatus.COMPLETE, None)),
        ("empty", (RunObjectStatus.PARTIAL, "empty_bsl")),
        ("missing", (RunObjectStatus.FAILED, "missing_bsl")),
        ("read_error", (RunObjectStatus.FAILED, "bsl_read_error")),
    ],
)
def test_common_module_status_mapping(read_status, expected):
    assert common_module_status(read_status) == expected


@pytest.mark.parametrize(
    ("extracted", "warnings", "expected"),
    [
        (True, False, (RunObjectStatus.COMPLETE, None)),
        (True, True, (RunObjectStatus.PARTIAL, "skd_warning")),
        (False, False, (RunObjectStatus.FAILED, "skd_not_extracted")),
    ],
)
def test_skd_status_mapping(extracted, warnings, expected):
    assert skd_status(extracted, has_warnings=warnings) == expected
