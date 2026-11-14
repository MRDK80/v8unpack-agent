"""Independent name and pointer expectations for additional export layouts."""
import pytest

from scripts.form_fixture_issue500 import encoded, fixture_pair
from v8unpack_agent.form_evidence import FormSource, analyze_form, make_form_report


@pytest.mark.parametrize(("version", "name_slot"), [("26", 3), ("27", 4)])
@pytest.mark.parametrize("empty", [False, True])
def test_ordinary_forms(version, name_slot, empty):
    name = "Поле\u200b"
    raw = [["19"], *(["0"] * (name_slot - 1)), f'"{name}"', "0"]
    section = ["0" if empty else "Дочерние элементы отдельно"]
    metadata = {"Тип формы": "0", "Версия элементов формы": f"0-{version}",
                "form": [[[version, [], [[], [], section]]]]}
    elements = {"props": None if empty else [{"id": "19", "name": name, "raw": raw}]}
    result = analyze_form(FormSource("ordinary", encoded(metadata), encoded(elements)))
    assert make_form_report(result.contract()) == {"attribute_names": [] if empty else [name]}
    if not empty:
        assert result.facts[0].raw_pointer == f"/props/0/raw/{name_slot}"
        raw[name_slot] = '"wrong"'
        invalid = analyze_form(FormSource("ordinary", encoded(metadata), encoded(elements)))
        assert invalid.status == "incomplete" and not invalid.facts
    section.append("leftover")
    invalid = analyze_form(FormSource("ordinary", encoded(metadata), encoded(elements)))
    assert invalid.status == "incomplete" and not invalid.facts


def test_managed_root_two_with_child_reports_only_top_level():
    metadata, elements = fixture_pair(["Parent", "Sibling"], root_version="2")
    prop = elements["props"][0]
    prop["raw"][13] = "отдельно"
    child = {"id": "12", "name": "Column",
             "raw": ["5", "12", "0", '"Column"', "0", "0", "0", "0", "0", "0"]}
    prop["child"] = [child]
    result = analyze_form(FormSource("managed", encoded(metadata), encoded(elements)))
    assert make_form_report(result.contract()) == {"attribute_names": ["Parent", "Sibling"]}
    assert result.to_dict()["coverage"]["nested_attributes"] == "unsupported"
    child["raw"][1] = "13"
    invalid = analyze_form(FormSource("managed", encoded(metadata), encoded(elements)))
    assert invalid.status == "unsupported" and not invalid.facts
