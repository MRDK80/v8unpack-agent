from uuid import uuid4
from v8unpack_agent.elem_parser import _TABULAR_FIELD_UUID, extract_legacy_list_form_elements

SOURCE = "РегистрНакопленияСписок"
ATTRS = [
    ("4233afe8-5acb-4851-8f21-0af9120bd0f1", "Организация"),
    ("3b529041-5487-457b-b62a-fd9ad52d613a", "Склад"),
    ("28a192a1-5036-448c-9a73-48320e8af9b8", "Номенклатура"),
]

def _slot(uuid): return ["column", ["8", ["16", ["0", uuid]]]]
def _node20(uuids): return ["20", "s1", "s2", "0", "0", "0", *[_slot(u) for u in uuids]]
def _tf(uuids, source=SOURCE):
    return [_TABULAR_FIELD_UUID, "1", ["5", ['"Pattern"', ['"#"', str(uuid4())]], [["10"], ["11", _node20(uuids)]]], "0", ["14", f'"{source}"']]
def _obj(): return {"attr_map": dict(ATTRS)}

def test_names_order_and_fields():
    r=extract_legacy_list_form_elements(_tf([u for u,_ in ATTRS]), _obj())
    assert [x["name"] for x in r] == [n for _,n in ATTRS]
    assert all(x["type"] == "TabularFieldColumn" for x in r)
    assert all(x["source"] == "legacy_list_form_json" for x in r)
    assert r[0]["data_path"] == "РегистрНакопленияСписок.Организация"

def test_nested_and_unmapped():
    r=extract_legacy_list_form_elements({"root":[_tf([ATTRS[0][0], str(uuid4())])]}, _obj())
    assert [x["name"] for x in r] == ["Организация"]

def test_duplicates_collapsed():
    r=extract_legacy_list_form_elements([_tf([ATTRS[0][0]]), _tf([ATTRS[0][0]])], _obj())
    assert [x["name"] for x in r] == ["Организация"]

def test_largest_node20_selected():
    form=_tf([ATTRS[0][0]])
    form[2][2][1].append(_node20([u for u,_ in ATTRS]))
    r=extract_legacy_list_form_elements(form, _obj())
    assert [x["name"] for x in r] == [n for _,n in ATTRS]

def test_ambiguous_slot_falls_back_to_walk_refs():
    """Слот с двумя UUID неоднозначен для блока "20" (candidates пуст).
    После патча #107 _tabular_field_attribute_slots уходит в fallback
    walk_refs и находит оба реквизита через ["0", uuid].
    """
    block=["20", "s1", "s2", "0", "0", "0", ["column", ["0", ATTRS[0][0]], ["0", ATTRS[1][0]]]]
    form=_tf([]); form[2][2][1].append(block)
    r = extract_legacy_list_form_elements(form, _obj())
    assert [x["name"] for x in r] == ["Организация", "Склад"]
    assert all(x["source"] == "legacy_list_form_json" for x in r)

def test_default_source():
    form=_tf([ATTRS[0][0]]); form[4]=[]
    assert extract_legacy_list_form_elements(form, _obj())[0]["data_path"] == "Список.Организация"

def test_null_inputs():
    assert extract_legacy_list_form_elements(None, _obj()) == []
    assert extract_legacy_list_form_elements(_tf([]), None) == []
    assert extract_legacy_list_form_elements(_tf([]), {}) == []

def test_reference_pair_fallback():
    uuids = [uuid for uuid, _name in ATTRS]

    form = [
        _TABULAR_FIELD_UUID,
        "1",
        [
            "5",
            ["references", *[
                ["column", ["0", uuid]]
                for uuid in uuids
            ]],
        ],
        "0",
        ["14", '"РегистрНакопленияСписок"'],
    ]

    result = extract_legacy_list_form_elements(
        form,
        {"attr_map": dict(ATTRS)},
    )

    assert [item["name"] for item in result] == [
        "Организация",
        "Склад",
        "Номенклатура",
    ]

    assert [item["data_path"] for item in result] == [
        "РегистрНакопленияСписок.Организация",
        "РегистрНакопленияСписок.Склад",
        "РегистрНакопленияСписок.Номенклатура",
    ]
