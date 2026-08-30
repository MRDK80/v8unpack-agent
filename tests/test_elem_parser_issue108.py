"""RED-тесты issue #108 — две подкатегории категории A.

Оставшиеся 4 формы с причиной TABULAR_FIELD_EMPTY_ATTR_MAP разбиваются
на два принципиально разных случая:

  A2 — CommonForm: владельца нет по определению, пустая карта — норма.
       Ожидается новая причина NO_OWNER_OBJECT.
  A3 — ChartOfCharacteristicType: владелец есть, но layout не распознан
       декодером. Ожидается, что после поддержки layout карта заполняется
       и форма переходит в OK (то есть classify_unindexed_form не вызывается).

Порядок проверок в classify_unindexed_form после патча:
  D → NO_OWNER_OBJECT (CommonForm) → A (TABULAR_FIELD_EMPTY_ATTR_MAP) → ...

NO_OWNER_OBJECT имеет приоритет перед A: проверяется ДО attr_map,
чтобы не считать отсутствие владельца дефектом.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4


from v8unpack_agent.elem_parser import (
    _TABULAR_FIELD_UUID,
    ElemIndexResult,
    UnindexedReason,
    UnindexedResult,
    classify_unindexed_form,
    load_owner_attribute_map,
)
from v8unpack_agent.object_decoder import decode_object_attributes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NULL_UUID = "00000000-0000-0000-0000-000000000000"


def _uuid() -> str:
    return str(uuid4())


def _slot(uuid: str) -> list:
    return ["column", ["8", ["16", ["0", uuid]]]]


def _node20(uuids: list[str]) -> list:
    return ["20", "s1", "s2", "0", "0", "0", *[_slot(u) for u in uuids]]


def _tabular_field(uuids: list[str], source: str = "Список") -> list:
    return [
        _TABULAR_FIELD_UUID,
        "1",
        [
            "5",
            ['"Pattern"', ['"#"', str(uuid4())]],
            [["10"], ["11", _node20(uuids)]],
        ],
        "0",
        ["14", f'"{source}"'],
    ]


def _write(tmp_path: Path, name: str, payload: object) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _write_elem_json(form_dir: Path) -> Path:
    payload = {"tree": {}, "data": {}}
    p = form_dir / "Form.elem.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _real_name_entry(uuid: str, name: str, synonym: str = "") -> list:
    """name-entry production-layout v8unpack (строковые теги)."""
    return [
        "2",
        ["1", "100", uuid],
        json.dumps(name, ensure_ascii=False),
        ["1", '"ru"', json.dumps(synonym or name, ensure_ascii=False)],
        '""', "0", "0", _NULL_UUID,
    ]


def _real_attribute_wrapper(uuid: str, name: str) -> list:
    """attribute-wrapper production v8unpack."""
    descriptor = [
        "2",
        _real_name_entry(uuid, name),
        ['"Pattern"', ['"S"']],
    ]
    return [
        [
            "8",
            [
                "27", descriptor, "0", ["0"], ["0"], "0", '""', "0",
                ['"U"'], ['"U"'], "0", _NULL_UUID, "2", "0",
                ["5004", "0"], ["3", "0", "0"], ["0", "0"], "0",
                ["0"], ['"U"'], "0", "0", "0",
            ],
            "0", "1", "1",
        ],
        "0",
    ]


def _write_owner_json_with_header(owner_dir: Path, name: str, prop_uuids: list[str]) -> Path:
    """Записать <name>.json с валидным production header-layout."""
    wrappers = [_real_attribute_wrapper(u, f"Реквизит{i}") for i, u in enumerate(prop_uuids)]
    root = [
        "1", [], "0",
        ["ts-service", "0"],
        "0",
        ["props-service", str(len(wrappers)), *wrappers],
    ]
    p = owner_dir / f"{name}.json"
    p.write_text(json.dumps({"header": [root]}, ensure_ascii=False), encoding="utf-8")
    return p


# ===========================================================================
# A2 — CommonForm: нет объекта-владельца → NO_OWNER_OBJECT
# ===========================================================================

class TestCategoryA2CommonForm:
    """CommonForm не имеет объекта-владельца. Пустая карта — норма.

    Ожидается: classify_unindexed_form возвращает NO_OWNER_OBJECT,
    а НЕ TABULAR_FIELD_EMPTY_ATTR_MAP.
    Тем самым A2 выходит из числа «реальных дефектов» в агрегате метрики.
    """

    def _make_common_form_dir(self, tmp_path: Path, source: str = "Список") -> Path:
        """Минимальное дерево CommonForm с TabularField."""
        form_dir = (
            tmp_path
            / "CommonForm"
            / "ФормаВыбораПользователя"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        col_uuids = [_uuid(), _uuid()]
        _write(form_dir, "ФормаВыбораПользователя.json", _tabular_field(col_uuids, source))
        # Намеренно НЕТ никакого <Owner>.json рядом — владелец отсутствует
        return form_dir

    def test_reason_is_no_owner_object(self, tmp_path):
        """CommonForm без владельца → NO_OWNER_OBJECT, не TABULAR_FIELD_EMPTY_ATTR_MAP."""
        form_dir = self._make_common_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.NO_OWNER_OBJECT

    def test_not_counted_as_defect(self, tmp_path):
        """NO_OWNER_OBJECT не равен TABULAR_FIELD_EMPTY_ATTR_MAP — не дефект."""
        form_dir = self._make_common_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP

    def test_detail_is_non_empty(self, tmp_path):
        form_dir = self._make_common_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed.detail, str)
        assert unindexed.detail

    def test_common_form_without_tabular_field(self, tmp_path):
        """CommonForm без TabularField → NO_TABULAR_NO_WIDGETS (не NO_OWNER_OBJECT)."""
        form_dir = (
            tmp_path
            / "CommonForm"
            / "ФормаИндикации"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        _write(form_dir, "ФормаИндикации.json", ["label-uuid", "1", "some-data"])
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.NO_OWNER_OBJECT

    def test_does_not_mutate_result(self, tmp_path):
        form_dir = self._make_common_form_dir(tmp_path)
        original_warnings = ["w1"]
        result = ElemIndexResult(
            elem_index_ok=False, elements=[], warnings=original_warnings.copy()
        )
        classify_unindexed_form(form_dir, result)
        assert result.warnings == original_warnings


# ===========================================================================
# A3 — ChartOfCharacteristicType: поддержка layout декодером
# ===========================================================================

class TestCategoryA3ChartOfCharacteristicType:
    """ChartOfCharacteristicType имеет объекта-владельца, но layout
    не был поддержан декодером. После патча decode_object_attributes
    должен возвращать непустые Properties.

    Структура ChartOfCharacteristicType.json аналогична Catalog.json:
    production-layout v8unpack со строковыми тегами и секцией header.
    """

    def _write_coct_json(self, owner_dir: Path, prop_uuids: list[str]) -> Path:
        """ChartOfCharacteristicType.json с валидным production header-layout."""
        return _write_owner_json_with_header(
            owner_dir, "ChartOfCharacteristicType", prop_uuids
        )

    def _make_coct_form_dir(
        self,
        tmp_path: Path,
        prop_uuids: list[str] | None = None,
        tf_uuids: list[str] | None = None,
    ) -> tuple[Path, list[str], list[str]]:
        """Дерево ChartOfCharacteristicType с ФормаВыбораГруппы."""
        p_uuids = prop_uuids or [_uuid(), _uuid()]
        t_uuids = tf_uuids or list(p_uuids)
        form_dir = (
            tmp_path
            / "ChartOfCharacteristicType"
            / "НазначенияСвойств"
            / "ChartOfCharacteristicTypeForm"
            / "ФормаВыбораГруппы"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        _write(form_dir, "ФормаВыбораГруппы.json", _tabular_field(t_uuids))
        owner_dir = form_dir.parent.parent
        self._write_coct_json(owner_dir, p_uuids)
        return form_dir, p_uuids, t_uuids

    def test_decode_object_attributes_returns_properties(self, tmp_path):
        """decode_object_attributes должен вернуть непустые Properties для COCT layout."""
        owner_dir = tmp_path / "ChartOfCharacteristicType" / "НазначенияСвойств"
        owner_dir.mkdir(parents=True)
        prop_uuids = [_uuid(), _uuid()]
        coct_json = _write_owner_json_with_header(owner_dir, "ChartOfCharacteristicType", prop_uuids)

        result = decode_object_attributes(coct_json)
        assert result.ok
        assert result.data["Properties"], (
            "decode_object_attributes должен вернуть непустые Properties "
            "для ChartOfCharacteristicType с production header-layout"
        )

    def test_decode_object_attributes_uuids_match(self, tmp_path):
        """UUID из Properties должны совпадать с записанными."""
        owner_dir = tmp_path / "ChartOfCharacteristicType" / "НазначенияСвойств"
        owner_dir.mkdir(parents=True)
        prop_uuids = [_uuid(), _uuid()]
        coct_json = _write_owner_json_with_header(owner_dir, "ChartOfCharacteristicType", prop_uuids)

        result = decode_object_attributes(coct_json)
        found_uuids = {p["UUID"] for p in result.data["Properties"]}
        assert found_uuids == set(prop_uuids), (
            f"Ожидались UUID {prop_uuids}, получены {found_uuids}"
        )

    def test_load_owner_attribute_map_non_empty_for_coct(self, tmp_path):
        """load_owner_attribute_map должен вернуть непустую карту для COCT формы."""
        form_dir, p_uuids, _ = self._make_coct_form_dir(tmp_path)
        warnings: list[str] = []
        attr_map = load_owner_attribute_map(form_dir, warnings)
        assert attr_map, (
            "load_owner_attribute_map должен вернуть непустую карту "
            "для формы ChartOfCharacteristicType"
        )
        for u in p_uuids:
            assert u in attr_map, f"UUID {u} должен быть в attr_map"

    def test_coct_form_indexed_when_attr_map_filled(self, tmp_path):
        """Когда attr_map заполнена, TF разрезолвится → форма не получает TABULAR_FIELD_EMPTY_ATTR_MAP."""
        p_uuids = [_uuid(), _uuid()]
        form_dir, _, _ = self._make_coct_form_dir(
            tmp_path, prop_uuids=p_uuids, tf_uuids=list(p_uuids)
        )
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP, (
            "ChartOfCharacteristicType с заполненной attr_map не должен "
            "получать TABULAR_FIELD_EMPTY_ATTR_MAP"
        )

    def test_coct_with_empty_header_gives_empty_attr_map(self, tmp_path):
        """Если COCT.json повреждён — карта пуста, причина A."""
        form_dir = (
            tmp_path
            / "ChartOfCharacteristicType"
            / "Тест"
            / "ChartOfCharacteristicTypeForm"
            / "ФормаВыбораГруппы"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        col_uuids = [_uuid()]
        _write(form_dir, "ФормаВыбораГруппы.json", _tabular_field(col_uuids))
        owner_dir = form_dir.parent.parent
        p = owner_dir / "ChartOfCharacteristicType.json"
        p.write_text(json.dumps({"header": "not_a_list"}, ensure_ascii=False), encoding="utf-8")

        warnings: list[str] = []
        attr_map = load_owner_attribute_map(form_dir, warnings)
        assert not attr_map, "Повреждённый header должен давать пустую карту"

        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP


# ===========================================================================
# Регрессии — ранее поддержанные layout не сломаны
# ===========================================================================

class TestRegressions:
    """Поддержанные ранее layout продолжают работать после патча #108."""

    def _write_catalog_json_with_header(self, form_dir: Path, prop_uuids: list[str]) -> Path:
        """Catalog.json с валидным production header."""
        obj_dir = form_dir.parent.parent
        owner_json = obj_dir / "Catalog.json"
        wrappers = [
            _real_attribute_wrapper(u, f"Реквизит{i}")
            for i, u in enumerate(prop_uuids)
        ]
        root = [
            "1", [], "0",
            ["ts-service", "0"],
            "0",
            ["props-service", str(len(wrappers)), *wrappers],
        ]
        owner_json.write_text(
            json.dumps({"header": [root]}, ensure_ascii=False), encoding="utf-8"
        )
        return owner_json

    def test_catalog_layout_still_works(self, tmp_path):
        """Catalog.json с production header → attr_map непуста (регрессия #107)."""
        form_dir = (
            tmp_path / "Catalog" / "Валюты" / "CatalogForm" / "ФормаВыбора"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        prop_uuids = [_uuid(), _uuid()]
        self._write_catalog_json_with_header(form_dir, prop_uuids)
        warnings: list[str] = []
        attr_map = load_owner_attribute_map(form_dir, warnings)
        assert attr_map, "Catalog layout должен давать непустую attr_map"
        for u in prop_uuids:
            assert u in attr_map

    def test_no_owner_object_not_raised_for_catalog(self, tmp_path):
        """Catalog с существующим JSON не получает NO_OWNER_OBJECT."""
        form_dir = (
            tmp_path / "Catalog" / "Валюты" / "CatalogForm" / "ФормаВыбора"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        col_uuids = [_uuid()]
        _write(form_dir, "ФормаВыбора.json", _tabular_field(col_uuids))
        owner_dir = form_dir.parent.parent
        (owner_dir / "Catalog.json").write_text(
            json.dumps({"header": ["bad_layout"]}), encoding="utf-8"
        )
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.NO_OWNER_OBJECT

    def test_document_layout_still_works(self, tmp_path):
        """Document.json с production header → attr_map непуста."""
        form_dir = (
            tmp_path / "Document" / "Документ1" / "DocumentForm" / "ФормаДокумента"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        owner_dir = form_dir.parent.parent
        prop_uuids = [_uuid()]
        _write_owner_json_with_header(owner_dir, "Document", prop_uuids)
        warnings: list[str] = []
        attr_map = load_owner_attribute_map(form_dir, warnings)
        assert attr_map


# ===========================================================================
# Инварианты для issue #108
# ===========================================================================

class TestInvariantsIssue108:
    """Контракт по инвариантам: нет мутаций, нет исключений, нет фантомных data_path."""

    def test_no_owner_object_reason_in_enum(self):
        """NO_OWNER_OBJECT должен присутствовать в UnindexedReason."""
        assert hasattr(UnindexedReason, "NO_OWNER_OBJECT"), (
            "UnindexedReason должен содержать NO_OWNER_OBJECT согласно issue #108"
        )

    def test_all_enum_values_cover_108(self):
        """После патча в enum должны быть все исходные значения плюс NO_OWNER_OBJECT."""
        reasons = {r for r in UnindexedReason}
        required = {
            UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP,
            UnindexedReason.TABULAR_FIELD_NO_UUID_HITS,
            UnindexedReason.NO_TABULAR_NO_WIDGETS,
            UnindexedReason.NO_LEGACY_JSON,
            UnindexedReason.UNKNOWN,
            UnindexedReason.NO_OWNER_OBJECT,
        }
        assert required.issubset(reasons)

    def test_never_raises_on_common_form_dir(self, tmp_path):
        form_dir = tmp_path / "CommonForm" / "ТестоваяФорма"
        form_dir.mkdir(parents=True)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed, UnindexedResult)

    def test_does_not_mutate_elem_result(self, tmp_path):
        form_dir = tmp_path / "CommonForm" / "ТестоваяФорма"
        form_dir.mkdir(parents=True)
        original_warnings = ["existing"]
        result = ElemIndexResult(
            elem_index_ok=False, elements=[], warnings=original_warnings.copy()
        )
        classify_unindexed_form(form_dir, result)
        assert result.warnings == original_warnings
        assert result.elem_index_ok is False
        assert result.elements == []

    def test_input_not_mutated_by_decode(self, tmp_path):
        """decode_object_attributes не мутирует входной файл."""
        owner_dir = tmp_path / "ChartOfCharacteristicType" / "Тест"
        owner_dir.mkdir(parents=True)
        prop_uuids = [_uuid()]
        coct_json = _write_owner_json_with_header(owner_dir, "ChartOfCharacteristicType", prop_uuids)
        content_before = coct_json.read_text(encoding="utf-8")
        decode_object_attributes(coct_json)
        content_after = coct_json.read_text(encoding="utf-8")
        assert content_before == content_after

    def test_no_false_data_path_for_unknown_uuid(self, tmp_path):
        """UUID, отсутствующий в attr_map, не создаёт data_path."""
        from v8unpack_agent.elem_parser import _tabular_field_attribute_slots
        unknown_uuid = _uuid()
        tf = _tabular_field([unknown_uuid])
        attr_map = {_uuid(): "ДругойРеквизит"}  # не содержит unknown_uuid
        slots = _tabular_field_attribute_slots(tf, attr_map)
        assert slots == [], "Неизвестный UUID не должен создавать слот"

    def test_partial_header_gives_partial_result(self, tmp_path):
        """Частично заполненный header даёт частичный результат без исключения."""
        owner_dir = tmp_path / "ChartOfCharacteristicType" / "Тест"
        owner_dir.mkdir(parents=True)
        valid_uuid = _uuid()
        wrappers = [
            _real_attribute_wrapper(valid_uuid, "ВалидныйРеквизит"),
            [["8", ["27", ["2", ["1", "100", _NULL_UUID], '"ПовреждённыйРеквизит"',
                            ["1", '"ru"', '"Синоним"']], ['"Pattern"', ['"S"']]], "0", "1", "1"], "0"],
        ]
        root = [
            "1", [], "0",
            ["ts-service", "0"],
            "0",
            ["props-service", "2", *wrappers],
        ]
        p = owner_dir / "ChartOfCharacteristicType.json"
        p.write_text(json.dumps({"header": [root]}, ensure_ascii=False), encoding="utf-8")
        result = decode_object_attributes(p)
        assert result.ok
        found_uuids = {prop["UUID"] for prop in result.data["Properties"]}
        assert valid_uuid in found_uuids
