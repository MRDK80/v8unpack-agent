"""Check old ordinary form layouts using synthetic export pairs."""
import pytest

from scripts.form_fixture_issue500 import encoded
from v8unpack_agent.form_evidence import FormSource, analyze_form, make_form_report


@pytest.mark.parametrize("root", ["19", "20", "23"])
@pytest.mark.parametrize("names", [[], ["Я", "А\u200b"]])
def test_inline_forms_preserve_declared_order(root, names):
    records = [[[str(index)], "0", "0", f'"{name}"', []] for index, name in enumerate(names)]
    section = [str(len(records)), *records]
    metadata = {"Тип формы": "0", "Версия элементов формы": f"0-{root}-16",
                "form": [[[root, ["16"], [[], [], section]]]]}
    elements = encoded({"props": []})
    evidence = analyze_form(FormSource("form", encoded(metadata), elements))
    assert make_form_report(evidence.contract()) == {"attribute_names": names}
    section[0] = str(len(records) + 1)
    assert analyze_form(FormSource("form", encoded(metadata), elements)).status == "incomplete"


@pytest.mark.parametrize(("version", "form", "status", "reason"), [
    ("", [], "unavailable", "raw_form_unavailable"),
    ("0-5-1", [[["5", "1", '"Title"', ["Opaque"], "0"]]],
     "unsupported", "form_kind_or_version_unsupported"),
])
def test_unproven_sections_do_not_produce_empty_reports(version, form, status, reason):
    metadata = {"Тип формы": "0", "Версия элементов формы": version, "form": form}
    evidence = analyze_form(FormSource("form", encoded(metadata), encoded({"props": []})))
    assert (evidence.status, evidence.reason, evidence.facts) == (status, reason, ())
    with pytest.raises(ValueError):
        evidence.contract()
