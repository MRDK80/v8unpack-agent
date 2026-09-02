"""Контракт post-run report для issue #196."""

import importlib
import json

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
    scan_warning_reason_code,
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


def test_partial_and_failed_serialize_required_details():
    report = _report(
        ObjectRunResult(
            object="Document/Example/Form/Partial",
            object_kind=RunObjectKind.FORM,
            status=RunObjectStatus.PARTIAL,
            stage="elem_parse",
            reason_code="invalid_elem_json",
            message="Часть данных формы недоступна",
        ),
        ObjectRunResult(
            object="Document/Example/Form/Failed",
            object_kind=RunObjectKind.FORM,
            status=RunObjectStatus.FAILED,
            stage="context",
            reason_code="unsupported_structure",
            message="Результат объекта не сформирован",
        ),
    )

    payload = report.to_dict()

    assert report.summary.partial == 1
    assert report.summary.failed == 1
    assert {item["status"] for item in payload["objects"]} == {
        "partial",
        "failed",
    }


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


@pytest.mark.parametrize(
    "message",
    [
        "Ошибка чтения /private/example.json",
        "Ошибка чтения C:/private/example.json",
    ],
)
def test_fatal_error_rejects_embedded_absolute_path(message):
    with pytest.raises(RunReportValidationError, match="absolute path"):
        RunFatalError(
            reason_code="pipeline_error",
            error_type="runtime_error",
            message=message,
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


def test_existing_enum_values_are_reused():
    from v8unpack_agent.elem_parser import UnindexedReason
    from v8unpack_agent.object_decoder import DecodeError

    unindexed_reason = next(iter(UnindexedReason))
    decode_error = next(iter(DecodeError))

    assert unindexed_reason_code(unindexed_reason) == unindexed_reason.value
    assert decode_error_reason_code(decode_error) == decode_error.value


def test_scan_warning_mapping_delegates_to_canonical_parser(monkeypatch):
    scan_forms_module = importlib.import_module("v8unpack_agent.scan_forms")
    monkeypatch.setattr(
        scan_forms_module,
        "scan_warning_code",
        lambda _: "synthetic_warning",
    )

    assert scan_warning_reason_code("synthetic warning") == "synthetic_warning"


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
