"""Synthetic inline ordinary form records and refusal cases."""
import pytest

from scripts.form_fixture_issue500 import encoded
from v8unpack_agent.form_evidence import FormSource, analyze_form, make_form_report
from v8unpack_agent.report_provenance import checked_envelope


@pytest.fixture
def inline_pair():
    section = ["2", [["2"], "0", "0", '"Второй\u200b"', "0"], [["1"], "0", "0", '"Первый"', "0"]]
    metadata = {"Тип формы": "0", "Версия элементов формы": "0-25-16",
                "form": [[["25", ["16"], [[], [], section]]]]}
    return metadata, {"props": []}, section


def test_inline_order_and_metadata_provenance(inline_pair):
    metadata, elements, _ = inline_pair
    evidence = analyze_form(FormSource("form", encoded(metadata), encoded(elements)))
    contract = evidence.contract()
    report = make_form_report(contract)
    assert report == {"attribute_names": ["Второй\u200b", "Первый"]}
    fields = checked_envelope(contract, report, "producer")["fields"]
    assert [(f["artifact"], f["source_pointer"], f["raw_pointer"]) for f in fields] == [
        ("metadata", pointer, pointer) for pointer in ("/form/0/0/2/2/1/3", "/form/0/0/2/2/2/3")
    ]


@pytest.mark.parametrize("fault", ["count", "missing", "mixed", "duplicate", "version", "unquoted"])
def test_invalid_inline_sections_refuse_complete_report(inline_pair, fault):
    metadata, elements, section = inline_pair
    if fault == "count":
        section[0] = "3"
    elif fault == "missing":
        del elements["props"]
    elif fault == "mixed":
        elements["props"] = [{"name": "Conflicting"}]
    elif fault == "duplicate":
        section[2][0] = ["2"]
    elif fault == "version":
        metadata["form"][0][0][1] = ["17"]
    else:
        section[1][3] = "unquoted"
    result = analyze_form(FormSource("form", encoded(metadata), encoded(elements)))
    assert result.status in {"incomplete", "unsupported"} and not result.facts


def test_inline_zero_counter_proves_empty(inline_pair):
    metadata, elements, section = inline_pair
    section[:] = ["0"]
    result = analyze_form(FormSource("form", encoded(metadata), encoded(elements)))
    assert make_form_report(result.contract()) == {"attribute_names": []}
