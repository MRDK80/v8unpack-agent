"""Тесты для decode_object_attributes (#84).

Все тесты написаны на минимальных синтетических raw-фрагментах,
не требующих реальной выгрузки конфигурации.

Структура raw-header (prop-блок):
  [0, entry1, entry2, ...]  -- индекс 0 является тегом, затем идут записи реквизитов
аналогичная структура для табличных частей и их реквизитов.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from v8unpack_agent.object_decoder import (
    decode_object_attributes,
    DecodeResult,
    DecodeError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: pathlib.Path, name: str, payload: object) -> pathlib.Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Базовый случай — Catalog.json с реквизитами верхнего уровня
# ---------------------------------------------------------------------------
#
# Структура header в реальных файлах v8unpack:
#   header = [0, [..., [0, [..., [0, prop_block, ts_block, ...]]]]]
#   prop_block = [0, entry1, entry2, ...]  -- тег 0 + записи реквизитов
#   entry = [0, [0, 0, uuid], '"Name"', [0, '"Type"'], [0, 0, [0, '"ru"', '"Synonym"']]]

MINIMAL_CATALOG = {
    "header": [
        0,
        [
            0,
            [
                0,
                [
                    # Properties-блок: [0, entry1, entry2]
                    0,
                    [
                        0,  # тег-обёртка списка записей
                        [0, [0, 0, "3d446927-2fb8-11d7-85a2-0050bae0a772"], '"\u041a\u043e\u0440\u0421\u0447\u0435\u0442"',
                         [0, '"String"'], [0, 0, [0, '"ru"', '"\u041a\u043e\u0440. \u0441\u0447\u0451\u0442"']]],
                        [0, [0, 0, "aabbccdd-0000-0000-0000-000000000001"], '"\u0421\u0443\u043c\u043c\u0430"',
                         [0, '"Number"'], [0, 0, [0, '"ru"', '"\u0421\u0443\u043c\u043c\u0430"']]],
                    ]
                ]
            ]
        ]
    ]
}


def test_basic_properties(tmp_path):
    """decode_object_attributes возвращает Properties с UUID, Name, Type, Synonym."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG)
    result = decode_object_attributes(obj_json)
    assert isinstance(result, DecodeResult)
    assert result.ok
    props = result.data["Properties"]
    assert len(props) == 2
    names = {p["Name"] for p in props}
    assert names == {"КорСчет", "Сумма"}
    for p in props:
        assert "UUID" in p
        assert "Type" in p
        assert "Synonym" in p


def test_uuid_values(tmp_path):
    """UUID реквизитов совпадают с ожидаемыми."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG)
    result = decode_object_attributes(obj_json)
    uuids = {p["UUID"] for p in result.data["Properties"]}
    assert "3d446927-2fb8-11d7-85a2-0050bae0a772" in uuids
    assert "aabbccdd-0000-0000-0000-000000000001" in uuids


def test_type_and_synonym(tmp_path):
    """Type и Synonym корректно декодируются для первого реквизита."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG)
    result = decode_object_attributes(obj_json)
    prop = next(p for p in result.data["Properties"] if p["Name"] == "КорСчет")
    assert prop["Type"] == "String"
    assert prop["Synonym"] == "Кор. счёт"


# ---------------------------------------------------------------------------
# 2. Табличные части
# ---------------------------------------------------------------------------

MINIMAL_CATALOG_WITH_TS = {
    "header": [
        0,
        [
            0,
            [
                0,
                [
                    0,
                    [
                        0,  # тег списка реквизитов верхнего уровня
                        [0, [0, 0, "aaaa0001-0000-0000-0000-000000000001"], '"\u041a\u043e\u0434"',
                         [0, '"String"'], [0, 0, [0, '"ru"', '"\u041a\u043e\u0434"']]],
                    ],
                    [
                        # TabularSections-блок: [0, ts_entry]
                        0,  # тег списка ТЧ
                        [
                            # одна ТЧ
                            0,
                            [0, 0, "bbbb0001-0000-0000-0000-000000000001"],
                            '"\u041a\u043e\u043d\u0442\u0430\u043a\u0442\u043d\u0430\u044f\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f"',
                            [0, 0, [0, '"ru"', '"\u041a\u043e\u043d\u0442\u0430\u043a\u0442\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f"']],
                            [
                                # prop-блок реквизитов ТЧ: [0, ts_prop_entry]
                                0,
                                [0, [0, 0, "cccc0001-0000-0000-0000-000000000001"],
                                 '"\u0412\u0438\u0434"', [0, '"CatalogRef.\u0412\u0438\u0434\u044b\u041a\u043e\u043d\u0442\u0430\u043a\u0442\u043d\u043e\u0439\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u0438"'],
                                 [0, 0, [0, '"ru"', '"\u0412\u0438\u0434"']]],
                            ]
                        ]
                    ]
                ]
            ]
        ]
    ]
}


def test_tabular_sections_present(tmp_path):
    """TabularSections присутствуют в результате."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG_WITH_TS)
    result = decode_object_attributes(obj_json)
    assert result.ok
    ts = result.data.get("TabularSections", [])
    assert len(ts) == 1
    assert ts[0]["Name"] == "КонтактнаяИнформация"


def test_tabular_section_properties(tmp_path):
    """Реквизиты внутри ТЧ корректно декодируются."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG_WITH_TS)
    result = decode_object_attributes(obj_json)
    ts = result.data["TabularSections"][0]
    assert "UUID" in ts
    assert "Synonym" in ts
    props = ts["Properties"]
    assert len(props) == 1
    assert props[0]["Name"] == "Вид"
    assert props[0]["Type"] == "CatalogRef.ВидыКонтактнойИнформации"


# ---------------------------------------------------------------------------
# 3. UUID-карта для elem_parser — строится из DecodeResult
# ---------------------------------------------------------------------------

def test_build_uuid_map_from_result(tmp_path):
    """UUID-карта строится из DecodeResult без независимого парсинга header."""
    obj_json = _write_json(tmp_path, "Catalog.json", MINIMAL_CATALOG)
    result = decode_object_attributes(obj_json)
    uuid_map = {p["UUID"]: p["Name"] for p in result.data["Properties"]}
    assert uuid_map["3d446927-2fb8-11d7-85a2-0050bae0a772"] == "КорСчет"
    assert uuid_map["aabbccdd-0000-0000-0000-000000000001"] == "Сумма"


# ---------------------------------------------------------------------------
# 4. Диагностика — ошибки
# ---------------------------------------------------------------------------

def test_file_not_found(tmp_path):
    """Файл отсутствует — ok=False, error содержит JSON_NOT_FOUND."""
    result = decode_object_attributes(tmp_path / "Missing.json")
    assert not result.ok
    assert result.error == DecodeError.JSON_NOT_FOUND


def test_missing_header(tmp_path):
    """JSON без секции header — ok=False, error=HEADER_MISSING."""
    obj_json = _write_json(tmp_path, "Catalog.json", {"data": []})
    result = decode_object_attributes(obj_json)
    assert not result.ok
    assert result.error == DecodeError.HEADER_MISSING


def test_unsupported_version(tmp_path):
    """Header неизвестной структуры — ok=False, error=VERSION_UNSUPPORTED."""
    obj_json = _write_json(tmp_path, "Catalog.json", {"header": "unexpected_string"})
    result = decode_object_attributes(obj_json)
    assert not result.ok
    assert result.error == DecodeError.VERSION_UNSUPPORTED


def test_corrupted_node_partial(tmp_path):
    """Повреждённый узел реквизита — ok=True (partial), warnings непусты."""
    catalog = {
        "header": [
            0,
            [
                0,
                [
                    0,
                    [
                        0,
                        [
                            0,  # тег списка
                            # нормальный реквизит
                            [0, [0, 0, "aaaa0001-0000-0000-0000-000000000001"], '"\u041a\u043e\u0434"',
                             [0, '"String"'], [0, 0, [0, '"ru"', '"\u041a\u043e\u0434"']]],
                            # повреждённый узел: список меньше 3 элементов
                            [0, [0, 0, "dead0000-0000-0000-0000-000000000002"]],
                            # ещё один нормальный
                            [0, [0, 0, "bbbb0002-0000-0000-0000-000000000002"], '"\u041dаименование"',
                             [0, '"String"'], [0, 0, [0, '"ru"', '"\u041dаименование"']]],
                        ]
                    ]
                ]
            ]
        ]
    }
    obj_json = _write_json(tmp_path, "Catalog.json", catalog)
    result = decode_object_attributes(obj_json)
    assert result.ok
    assert len(result.data["Properties"]) == 2
    assert len(result.warnings) > 0
    assert any("dead0000" in w for w in result.warnings), result.warnings


def test_unknown_type_fallback(tmp_path):
    """Нераспознанный тип реквизита — Property присутствует с Type=None или строкой."""
    catalog = {
        "header": [
            0,
            [
                0,
                [
                    0,
                    [
                        0,
                        [
                            0,  # тег списка
                            [0, [0, 0, "aaaa0001-0000-0000-0000-000000000001"], '"\u0420еквизит1"',
                             None, [0, 0, [0, '"ru"', '"\u0420еквизит один"']]],
                        ]
                    ]
                ]
            ]
        ]
    }
    obj_json = _write_json(tmp_path, "Catalog.json", catalog)
    result = decode_object_attributes(obj_json)
    assert result.ok
    assert len(result.data["Properties"]) == 1
    p = result.data["Properties"][0]
    assert p["Name"] == "Реквизит1"
    assert "Type" in p


# ---------------------------------------------------------------------------
# 5. Пустая конфигурация (0 реквизитов) — не ошибка
# ---------------------------------------------------------------------------

def test_empty_properties_section(tmp_path):
    """Объект без реквизитов — ok=True, Properties=[], TabularSections=[]."""
    catalog = {
        "header": [
            0,
            [
                0,
                [
                    0,
                    [0, []]
                ]
            ]
        ]
    }
    obj_json = _write_json(tmp_path, "Catalog.json", catalog)
    result = decode_object_attributes(obj_json)
    assert result.ok
    assert result.data["Properties"] == []
    assert result.data["TabularSections"] == []


# ---------------------------------------------------------------------------
# 6. catalog_resolver.resolve_data_path совместим с выводом декодера
# ---------------------------------------------------------------------------

def test_catalog_resolver_compat(tmp_path):
    """resolve_data_path работает по файлу, декодируемому decode_object_attributes,
    без изменения публичного API catalog_resolver."""
    catalog_dir = tmp_path / "Catalog" / "Банки"
    catalog_dir.mkdir(parents=True)
    obj_json = catalog_dir / "Catalog.json"
    obj_json.write_text(
        json.dumps({
            "Properties": [
                {"Name": "КорСчет", "Type": "String", "Synonym": "Кор. счёт"}
            ],
            "TabularSections": []
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    from v8unpack_agent.catalog_resolver import resolve_data_path
    rb = resolve_data_path("Объект.КорСчет", obj_json)
    assert rb.resolved
    assert rb.attribute_name == "КорСчет"
    assert rb.value_type == "String"


# ---------------------------------------------------------------------------
# 7. Поддержка Document, AccumulationRegister, InformationRegister
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "Document.json",
    "AccumulationRegister.json",
    "InformationRegister.json",
])
def test_supported_object_types(tmp_path, filename):
    """decode_object_attributes работает для Document, AccumulationRegister, InformationRegister."""
    payload = {
        "header": [
            0,
            [
                0,
                [
                    0,
                    [
                        0,
                        [
                            0,  # тег списка
                            [0, [0, 0, "aaaa0001-0000-0000-0000-000000000001"], '"\u0420еквизит"',
                             [0, '"String"'], [0, 0, [0, '"ru"', '"\u0420еквизит"']]],
                        ]
                    ]
                ]
            ]
        ]
    }
    obj_json = _write_json(tmp_path, filename, payload)
    result = decode_object_attributes(obj_json)
    assert result.ok
    assert len(result.data["Properties"]) == 1


# ---------------------------------------------------------------------------
# 8. Реальный production-layout v8unpack со строковыми тегами
# ---------------------------------------------------------------------------

_REAL_NULL_UUID = "00000000-0000-0000-0000-000000000000"

# UUID tipa, zashityy v _real_attribute_wrapper nizhe.
_REF_TYPE_IN_FIXTURE = "Ref#eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _real_name_entry(uuid: str, name: str, synonym: str) -> list:
    return [
        "2",
        ["1", "100", uuid],
        json.dumps(name, ensure_ascii=False),
        ["1", '"ru"', json.dumps(synonym, ensure_ascii=False)],
        '""', "0", "0", _REAL_NULL_UUID,
    ]


def _real_attribute_wrapper(uuid: str, name: str, synonym: str) -> list:
    descriptor = [
        "2",
        _real_name_entry(uuid, name, synonym),
        ['"Pattern"', ['"#"', "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"]],
    ]
    return [
        [
            "8",
            [
                "27", descriptor, "0", ["0"], ["0"], "0", '""', "0",
                ['"U"'], ['"U"'], "0", _REAL_NULL_UUID, "2", "0",
                ["5004", "0"], ["3", "0", "0"], ["0", "0"], "0",
                ["0"], ['"U"'], "0", "0", "0",
            ],
            "0", "1", "1",
        ],
        "0",
    ]


def _real_tabular_section() -> list:
    header = [
        "1",
        [
            "11", "service-1", "service-2", "service-3", "service-4",
            [
                "0",
                _real_name_entry(
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "Товары",
                    "Товары",
                ),
            ],
            "0", ["0"], ["0"],
        ],
    ]
    columns = [
        "columns-service-uuid",
        "1",
        _real_attribute_wrapper(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "Номенклатура",
            "Номенклатура",
        ),
    ]
    return [header, "1", columns]


def _real_v8_document() -> dict:
    root = [
        "1",
        [],
        "0",
        ["tabular-sections-service-uuid", "1", _real_tabular_section()],
        "0",
        [
            "properties-service-uuid",
            "1",
            _real_attribute_wrapper(
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "Организация",
                "Организация",
            ),
        ],
    ]
    return {"header": [root]}


def test_real_v8_string_tags_and_containers(tmp_path):
    obj_json = _write_json(tmp_path, "Document.json", _real_v8_document())
    result = decode_object_attributes(obj_json)

    assert result.ok
    assert result.warnings == []
    assert [p["Name"] for p in result.data["Properties"]] == ["Организация"]
    assert result.data["Properties"][0]["Type"] == _REF_TYPE_IN_FIXTURE

    sections = result.data["TabularSections"]
    assert [section["Name"] for section in sections] == ["Товары"]
    assert [p["Name"] for p in sections[0]["Properties"]] == ["Номенклатура"]
    assert sections[0]["Properties"][0]["Type"] == _REF_TYPE_IN_FIXTURE


def test_real_v8_uuid_map_contains_top_level_and_ts_props(tmp_path):
    obj_json = _write_json(tmp_path, "Document.json", _real_v8_document())
    result = decode_object_attributes(obj_json)
    mapping = {p["UUID"]: p["Name"] for p in result.data["Properties"]}
    for section in result.data["TabularSections"]:
        mapping.update({p["UUID"]: p["Name"] for p in section["Properties"]})

    assert mapping["dddddddd-dddd-4ddd-8ddd-dddddddddddd"] == "Организация"
    assert mapping["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"] == "Номенклатура"


def test_real_v8_unknown_wrapper_keeps_uuid_map(tmp_path):
    """Неизвестная wrapper-версия не должна терять UUID/name-entry."""
    entry = _real_name_entry(
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "НовыйРеквизит",
        "Новый реквизит",
    )
    payload = {
        "header": [[
            "99",
            ["unknown", ["layout", ["changed", entry]]],
        ]],
    }
    obj_json = _write_json(tmp_path, "Document.json", payload)

    result = decode_object_attributes(obj_json)

    assert result.ok
    assert result.data["TabularSections"] == []
    assert result.data["Properties"] == [{
        "UUID": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "Name": "НовыйРеквизит",
        "Type": None,
        "Synonym": "Новый реквизит",
    }]


@pytest.mark.parametrize("tag", ["0", "1", "2", "3"])
def test_real_v8_supported_name_entry_tags(tmp_path, tag):
    """Production-варианты name-entry сохраняют UUID и имя."""
    entry = _real_name_entry(
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "Реквизит",
        "Реквизит",
    )
    entry[0] = tag
    payload = {"header": [["99", ["unknown", entry]]]}
    obj_json = _write_json(tmp_path, "Document.json", payload)

    result = decode_object_attributes(obj_json)

    assert result.ok
    assert result.data["Properties"] == [{
        "UUID": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "Name": "Реквизит",
        "Type": None,
        "Synonym": "Реквизит",
    }]


def test_real_v8_foreign_name_entry_tag_is_rejected(tmp_path):
    entry = _real_name_entry("ffffffff-ffff-4fff-8fff-ffffffffffff", "X", "X")
    entry[0] = "99"
    result = decode_object_attributes(_write_json(tmp_path, "Document.json", {"header": [entry]}))
    assert result.data["Properties"] == []

# ---------------------------------------------------------------------------
# 9. Production Type: primitivy, ssylochnye, TYPE_UNKNOWN (#84 DoD p.1)
# ---------------------------------------------------------------------------

def _wrapper_with_type_node(type_node: list) -> list:
    # attribute-wrapper s proizvolnym type_node v descriptor
    descriptor = [
        "2",
        _real_name_entry(
            "cafe0001-0000-4000-8000-000000000001", "TestRekvizit", "Test"
        ),
        ['"Pattern"', type_node],
    ]
    return [
        [
            "8",
            [
                "27", descriptor, "0", ["0"], ["0"], "0", '""', "0",
                ['"U"'], ['"U"'], "0", _REAL_NULL_UUID, "2", "0",
                ["5004", "0"], ["3", "0", "0"], ["0", "0"], "0",
                ["0"], ['"U"'], "0", "0", "0",
            ],
            "0", "1", "1",
        ],
        "0",
    ]


def _doc_with_type_node(type_node: list) -> dict:
    root = [
        "1", [], "0", ["ts-service", "0"], "0",
        ["props-service", "1", _wrapper_with_type_node(type_node)],
    ]
    return {"header": [root]}


@pytest.mark.parametrize("code,expected", [
    ("S", "String"),
    ("N", "Number"),
    ("B", "Boolean"),
    ("D", "Date"),
    ("U", "Undefined"),
    ("T", "Null"),
])
def test_production_type_primitive_resolved(tmp_path, code, expected):
    payload = _doc_with_type_node(['"%s"' % code])
    result = decode_object_attributes(_write_json(tmp_path, "Document.json", payload))

    assert result.ok
    props = result.data["Properties"]
    assert len(props) == 1
    assert props[0]["Type"] == expected
    assert not any("TYPE_UNKNOWN" in w for w in result.warnings)


def test_production_type_ref_uuid(tmp_path):
    ref = "12345678-1234-4234-8234-123456789abc"
    payload = _doc_with_type_node(['"#"', ref])
    result = decode_object_attributes(_write_json(tmp_path, "Document.json", payload))

    assert result.ok
    assert result.data["Properties"][0]["Type"] == "Ref#" + ref
    assert not any("TYPE_UNKNOWN" in w for w in result.warnings)


def test_production_type_unknown_warning(tmp_path):
    payload = _doc_with_type_node(['"ZZZ"'])
    result = decode_object_attributes(_write_json(tmp_path, "Document.json", payload))

    assert result.ok
    props = result.data["Properties"]
    assert len(props) == 1
    assert props[0]["Type"] is None
    assert any("TYPE_UNKNOWN" in w for w in result.warnings), result.warnings
