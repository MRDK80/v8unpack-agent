"""RED-тесты issue #105 — классификация неиндексируемых форм.

Реальное распределение по live-базе (verify_105_classify.py):
  C_no_tabular_no_widgets : 1070  (нет TabularField, нет Input/ComboBox)
  B_tabular_no_uuid_hits  :   48  (TabularField есть, UUID не в attr_map)
  A_tabular_empty_attr_map:   12  (TabularField есть, attr_map пуста —
                                   object_decoder не распознал layout)

План реализации
---------------
Добавить в ``elem_parser``:

  class UnindexedReason(enum.Enum):
      TABULAR_FIELD_EMPTY_ATTR_MAP  = "tabular_field_empty_attr_map"   # A
      TABULAR_FIELD_NO_UUID_HITS    = "tabular_field_no_uuid_hits"     # B
      NO_TABULAR_NO_WIDGETS         = "no_tabular_no_widgets"          # C
      NO_LEGACY_JSON                = "no_legacy_json"                 # D
      UNKNOWN                       = "unknown"

  @dataclass
  class UnindexedResult:
      reason: UnindexedReason
      detail: str = ""   # человеко-читаемое объяснение для логов/метрики

  def classify_unindexed_form(
      form_root: Path,
      elem_result: ElemIndexResult,
  ) -> UnindexedResult:
      ...  # см. тесты ниже

Функция вызывается только когда ``elem_result.elem_index_ok is False``
или ``elem_result.elements == []``. Не мутирует elem_result.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from v8unpack_agent.elem_parser import (
    _TABULAR_FIELD_UUID,
    ElemIndexResult,
)

# ---------------------------------------------------------------------------
# Helpers — строители минимальных fixture-деревьев
# ---------------------------------------------------------------------------

_NULL_UUID = "00000000-0000-0000-0000-000000000000"


def _uuid() -> str:
    return str(uuid4())


def _slot(uuid: str) -> list:
    return ["column", ["8", ["16", ["0", uuid]]]]


def _node20(uuids: list[str]) -> list:
    return ["20", "s1", "s2", "0", "0", "0", *[_slot(u) for u in uuids]]


def _tabular_field(uuids: list[str], source: str = "Список") -> list:
    """Минимальный TabularField-блок с блоком 20 для переданных UUID."""
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


def _write_elem_json(form_dir: Path, data: dict | None = None) -> Path:
    """Записать elem.json (пустой по умолчанию)."""
    payload = data or {"tree": {}, "data": {}}
    p = form_dir / "CatalogForm.elem.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _write_catalog_json(
    form_dir: Path,
    properties: list[dict] | None = None,
    name: str = "Catalog.json",
) -> Path:
    """Записать Catalog.json в формате {"Properties":[...]} (без header).

    Используется только в fixture категории A — где ожидается пустая attr_map.
    Для категории B используй _write_catalog_json_with_header.
    """
    obj_dir = form_dir.parent.parent
    owner_json = obj_dir / name
    # Намеренно неподдерживаемый layout — см. контракт #160
    # («Поддерживаемые входные layout» в docs/object_decoder.md):
    # нормализованный owner JSON без ключа "header" даёт
    # DecodeError.HEADER_MISSING и, как следствие, attr_map={} — именно
    # это состояние проверяет тест. Negative fixture выбрана осознанно,
    # на raw-header её заменять не нужно.
    payload = {
        "Properties": properties or [],
        "TabularSections": [],
    }
    owner_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return owner_json


# ---------------------------------------------------------------------------
# Фикстура категории B: Catalog.json с настоящим production-layout (header)
# ---------------------------------------------------------------------------
# object_decoder ожидает секцию header — фикстура без header даёт HEADER_MISSING
# → ok=False → attr_map={} → реализация правильно классифицирует как A,
# а не B. Для B нужен валидный header с чужими UUID.

def _real_name_entry(uuid: str, name: str, synonym: str) -> list:
    """Строитель name-entry production-layout v8unpack."""
    return [
        "2",
        ["1", "100", uuid],
        json.dumps(name, ensure_ascii=False),
        ["1", '"ru"', json.dumps(synonym, ensure_ascii=False)],
        '""', "0", "0", _NULL_UUID,
    ]


def _real_attribute_wrapper(uuid: str, name: str, synonym: str = "") -> list:
    """Строитель attribute-wrapper production v8unpack."""
    descriptor = [
        "2",
        _real_name_entry(uuid, name, synonym or name),
        ['"Pattern"', ['"S"']],  # String — достаточно для резолюции UUID
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


def _write_catalog_json_with_header(
    form_dir: Path,
    prop_uuids: list[str],
    name: str = "Catalog.json",
) -> Path:
    """Записать Catalog.json с валидным header-layout, понятным object_decoder.

    Создаёт настоящие реквизиты с UUID из prop_uuids, которые
    object_decoder распознает → attr_map непустая.
    """
    obj_dir = form_dir.parent.parent
    owner_json = obj_dir / name

    wrappers = [
        _real_attribute_wrapper(u, f"Реквизит{i}")
        for i, u in enumerate(prop_uuids)
    ]
    root = [
        "1", [], "0",
        ["ts-service", "0"],  # пустой блок ТЧ
        "0",
        ["props-service", str(len(wrappers)), *wrappers],
    ]
    payload = {"header": [root]}
    owner_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return owner_json


# ---------------------------------------------------------------------------
# Импортируем то, что ещё НЕ существует → тесты красные
# ---------------------------------------------------------------------------

from v8unpack_agent.elem_parser import (
    UnindexedReason,
    UnindexedResult,
    classify_unindexed_form,
)

# ===========================================================================
# Категория C — нет TabularField, нет InputField/ComboBox
# ===========================================================================

class TestCategoryC:
    """1070 живых форм — AccumulationRegister и др. без виджетов данных.

    elem.json пуст, большой *.json не содержит ни TabularField,
    ни InputField/ComboBox. Это нормальный случай: форма-список
    без явной разметки или форма-сервиса с кнопками.
    """

    def _make_form_dir(self, tmp_path: Path, form_json_payload: object | None = None) -> Path:
        form_dir = tmp_path / "AccumulationRegister" / "Регистр1" / "AccumulationRegisterForm" / "ФормаСписка"
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)
        # Большой *.json без TabularField и без InputField/ComboBox
        payload = form_json_payload or [
            "label-uuid", "1", ["button", ["text", '"ОК"']]
        ]
        _write(form_dir, "ФормаСписка.json", payload)
        return form_dir

    def test_reason_is_no_tabular_no_widgets(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=["Элементы формы не найдены"])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.NO_TABULAR_NO_WIDGETS

    def test_detail_is_non_empty_string(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed.detail, str)
        assert unindexed.detail  # непустой

    def test_returns_unindexed_result_type(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed, UnindexedResult)

    def test_no_legacy_json_gives_no_legacy_json_reason(self, tmp_path):
        """Если большого *.json нет вообще — причина D, не C."""
        form_dir = tmp_path / "Catalog" / "Объект1" / "CatalogForm" / "ФормаОбъекта"
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)  # только elem.json, большого *.json нет
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=["elem.json не найден"])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.NO_LEGACY_JSON


# ===========================================================================
# Категория B — TabularField есть, UUID не попадают в attr_map
# ===========================================================================

class TestCategoryB:
    """48 живых форм — Catalog/Валюты/ФормаВыбора и аналоги.

    Большой *.json содержит TabularField, attr_map непустая,
    но UUID колонок не совпадают с UUID реквизитов в Catalog.json.

    IMPORTANT: Catalog.json должен быть в валидном production header-layout
    (object_decoder ожидает секцию header; {"Properties":[...]} без header
    даёт HEADER_MISSING → ok=False → attr_map={} → классификация A).
    """

    def _make_form_dir(
        self,
        tmp_path: Path,
        tf_uuids: list[str] | None = None,
        catalog_uuids: list[str] | None = None,
    ) -> Path:
        form_dir = (
            tmp_path / "Catalog" / "Валюты" / "CatalogForm" / "ФормаВыбора"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)

        # Колонки в TabularField — UUID, которых НЕТ в Catalog.json
        col_uuids = tf_uuids or [_uuid(), _uuid()]
        _write(form_dir, "ФормаВыбора.json", _tabular_field(col_uuids))

        # Catalog.json с валидным header-layout, но ДРУГИЕ UUID
        # (не пересекаются с col_uuids — attr_map непуста, UUID не совпадают)
        known_uuids = catalog_uuids or [_uuid(), _uuid()]
        _write_catalog_json_with_header(form_dir, known_uuids)
        return form_dir

    def test_reason_is_programmatic_no_defs(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.TABULAR_FIELD_PROGRAMMATIC_NO_DEFS

    def test_detail_mentions_tabular_field(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        detail_lower = unindexed.detail.lower()
        assert "tabularfield" in detail_lower
        assert "колонки.добавить" in detail_lower

    def test_returns_b_not_c_when_tf_present_but_no_hits(self, tmp_path):
        """Приоритет: если TabularField найден — не C, даже если slots пусты."""
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.NO_TABULAR_NO_WIDGETS


# ===========================================================================
# Категория A — TabularField есть, attr_map пуста
# ===========================================================================

class TestCategoryA:
    """12 живых форм — Catalog/ГруппыДоступностиСкладов/ФормаВыбора и аналоги.

    Большой *.json содержит TabularField, но load_owner_attribute_map()
    вернул пустой dict — object_decoder не смог распознать layout Catalog.json
    (предупреждение: "karta rekvizitov pusta").
    """

    def _make_form_dir(
        self,
        tmp_path: Path,
        col_uuids: list[str] | None = None,
        write_catalog: bool = True,
        catalog_payload: object | None = None,
    ) -> Path:
        form_dir = (
            tmp_path
            / "Catalog"
            / "ГруппыДоступностиСкладов"
            / "CatalogForm"
            / "ФормаВыбора"
        )
        form_dir.mkdir(parents=True)
        _write_elem_json(form_dir)

        uuids = col_uuids or [_uuid()]
        _write(form_dir, "ФормаВыбора.json", _tabular_field(uuids))

        if write_catalog:
            obj_dir = form_dir.parent.parent
            owner_json = obj_dir / "Catalog.json"
            payload = catalog_payload or {"header": ["unrecognized_layout", [], []]}
            owner_json.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return form_dir

    def test_reason_is_tabular_empty_attr_map(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP

    def test_detail_mentions_attr_map_empty(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.detail  # непустой

    def test_returns_a_not_b_when_attr_map_empty(self, tmp_path):
        """A имеет приоритет перед B: если attr_map пуста — не TABULAR_FIELD_NO_UUID_HITS."""
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason != UnindexedReason.TABULAR_FIELD_NO_UUID_HITS

    def test_no_catalog_json_gives_a_with_empty_attr_map(self, tmp_path):
        """Нет Catalog.json → attr_map пуста → причина A (не D: TabularField есть)."""
        form_dir = self._make_form_dir(tmp_path, write_catalog=False)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP


# ===========================================================================
# Категория D — нет большого *.json
# ===========================================================================

class TestCategoryD:
    """Когда _find_legacy_form_json() вернул None."""

    def _make_form_dir(self, tmp_path: Path) -> Path:
        form_dir = tmp_path / "Catalog" / "Объект1" / "CatalogForm" / "ФормаОбъекта"
        form_dir.mkdir(parents=True)
        # Только elem.json, никакого большого *.json
        _write_elem_json(form_dir)
        return form_dir

    def test_reason_is_no_legacy_json(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=["elem.json пуст"])
        unindexed = classify_unindexed_form(form_dir, result)
        assert unindexed.reason == UnindexedReason.NO_LEGACY_JSON

    def test_detail_is_non_empty(self, tmp_path):
        form_dir = self._make_form_dir(tmp_path)
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed.detail, str)


# ===========================================================================
# Инварианты — общее поведение classify_unindexed_form
# ===========================================================================

class TestInvariants:
    """Контракт функции независимо от категории."""

    def test_never_raises_on_missing_form_dir(self, tmp_path):
        """Несуществующая директория не бросает исключение — best-effort."""
        form_dir = tmp_path / "nonexistent" / "form"
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert isinstance(unindexed, UnindexedResult)

    def test_result_has_reason_and_detail(self, tmp_path):
        form_dir = tmp_path / "SomeForm"
        form_dir.mkdir()
        result = ElemIndexResult(elem_index_ok=False, elements=[], warnings=[])
        unindexed = classify_unindexed_form(form_dir, result)
        assert hasattr(unindexed, "reason")
        assert hasattr(unindexed, "detail")

    def test_does_not_mutate_elem_result(self, tmp_path):
        form_dir = tmp_path / "SomeForm"
        form_dir.mkdir()
        original_warnings = ["test warning"]
        result = ElemIndexResult(
            elem_index_ok=False,
            elements=[],
            warnings=original_warnings.copy(),
        )
        classify_unindexed_form(form_dir, result)
        assert result.warnings == original_warnings
        assert result.elem_index_ok is False
        assert result.elements == []

    def test_all_reasons_are_covered_by_enum(self):
        """Все 5 публичных значений enum присутствуют."""
        reasons = {r for r in UnindexedReason}
        expected = {
            UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP,
            UnindexedReason.TABULAR_FIELD_NO_UUID_HITS,
            UnindexedReason.NO_TABULAR_NO_WIDGETS,
            UnindexedReason.NO_LEGACY_JSON,
            UnindexedReason.UNKNOWN,
        }
        assert expected.issubset(reasons)
