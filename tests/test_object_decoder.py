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
                            [0, [0, 0]],
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
