"""Distinguish structural limits from malformed exports."""
import pytest

from scripts.form_fixture_issue500 import encoded, fixture_pair
from v8unpack_agent import form_evidence as evidence


@pytest.mark.parametrize(("limit", "reason"), [
    ("MAX_DOCUMENT_NODES", "document_node_budget"),
    ("MAX_DOCUMENT_DEPTH", "document_depth_budget"),
])
def test_document_budget_refuses_a_report_with_a_specific_reason(monkeypatch, limit, reason):
    metadata, elements = fixture_pair(["Code"])
    source = evidence.FormSource("synthetic", encoded(metadata), encoded(elements))
    assert evidence.analyze_form(source).status == "complete"
    monkeypatch.setattr(evidence, limit, 1)
    result = evidence.analyze_form(source)
    assert (result.status, result.reason, result.facts) == ("incomplete", reason, ())
    with pytest.raises(ValueError):
        result.contract()


def test_recovery_requires_an_unambiguous_case_fallback(tmp_path):
    from scripts.reextract_form_roots_issue545 import alternate_uuid

    uuid = "AAAAAAAA-0000-0000-0000-000000000000"
    lower = uuid.lower()
    member = tmp_path / (lower + ".0")
    member.mkdir()
    assert alternate_uuid(tmp_path, uuid) is None
    (member / "form").touch()
    expected = None if (tmp_path / (uuid + ".0")).exists() else lower
    assert alternate_uuid(tmp_path, uuid) == expected
    (tmp_path / (uuid + ".1")).touch()
    assert alternate_uuid(tmp_path, uuid) is None
