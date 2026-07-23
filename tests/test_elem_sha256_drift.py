"""Тесты для issue #58: дрейф управляемых форм через elem_sha256.

Проверяет, что:
- Elem-формы, попавшие в реестр через discovery (#55/#57), получают
  корректный структурный elem_sha256 (тот же путь через _compute_elem_sha256).
- Изменение структуры (parse_elem_json) приводит к structure_modified.
- Несемантическое изменение (GUID / переупорядочивание ключей) НЕ даёт
  ложный дрейф — хэш детерминирован и устойчив к шуму.
- Отсутствие Form.xml не влияет на дрейф (Form.xml вообще не участвует).
- Регрессии #38 (bsl_sha256) и #40 (elem_sha256 для ordinary-форм) зелёные
  (smoke-тесты, основные регрессионные наборы — в test_drift_content_hash.py
  и test_elem_structure_hash.py).

Все тесты — только in-memory / tmp-фикстуры. Реальные конфигурации, пути
и строки подключения не используются.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.scan_forms import FormScanIndex, scan_forms
from v8unpack_agent.drift_checker import check_drift


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_elem_json(
    form_dir: Path,
    filename: str,
    elements: list[dict],
    extra_top_level: dict | None = None,
) -> Path:
    """Записать *.elem.json с заданным списком элементов.

    ``extra_top_level`` — дополнительные поля верхнего уровня (GUID, косметика),
    которые НЕ должны влиять на структурный хэш.
    """
    payload: dict = {"elements": elements}
    if extra_top_level:
        payload.update(extra_top_level)
    p = form_dir / filename
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _structural_elements() -> list[dict]:
    """Базовый список структурно значимых элементов формы."""
    return [
        {
            "name": "Field1",
            "type": "InputField",
            "parent": None,
            "parent_path": None,
            "path": "Field1",
            "page": None,
            "source": "data",
        },
        {
            "name": "Button1",
            "type": "Button",
            "parent": None,
            "parent_path": None,
            "path": "Button1",
            "page": None,
            "source": "data",
        },
    ]


def _make_elem_only_form(
    root: Path,
    object_type: str,
    object_name: str,
    container: str,
    form_name: str,
    elements: list[dict] | None = None,
    extra_top_level: dict | None = None,
) -> Path:
    """Создать elem-only форму (нет .obj.bsl, есть *.elem.json).

    Возвращает путь к директории формы.
    """
    form_dir = root / object_type / object_name / container / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    _make_elem_json(
        form_dir,
        f"{container}.elem.json",
        elements if elements is not None else _structural_elements(),
        extra_top_level=extra_top_level,
    )
    return form_dir


def _make_ordinary_form(
    root: Path,
    object_type: str,
    object_name: str,
    container: str,
    form_name: str,
    bsl_content: str = "// bsl",
    elements: list[dict] | None = None,
) -> Path:
    """Создать обычную форму с .obj.bsl и опционально с *.elem.json."""
    form_dir = root / object_type / object_name / container / form_name
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / f"{container}.obj.bsl").write_text(bsl_content, encoding="utf-8")
    (form_dir / f"{container}.json").write_text("{}", encoding="utf-8")
    if elements is not None:
        _make_elem_json(
            form_dir,
            f"{container}.elem.json",
            elements,
        )
    return form_dir


# ---------------------------------------------------------------------------
# AC1: Elem-форма из реестра получает elem_sha256
# ---------------------------------------------------------------------------

class TestElemOnlyFormGetsSha256:
    """AC1: elem-формы, добавленные в реестр через discovery (#57),
    получают корректный структурный elem_sha256."""

    def test_elem_only_form_has_elem_sha256(self, tmp_path: Path) -> None:
        """Elem-only форма (нет .obj.bsl, есть *.elem.json) получает elem_sha256."""
        _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement"
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        elem_entries = [
            e for e in idx.forms if e.elem_json_path is not None
        ]
        assert elem_entries, "Elem-only форма не попала в индекс"
        entry = elem_entries[0]
        assert entry.elem_sha256 is not None, (
            "elem_sha256 должен быть вычислен для elem-only формы"
        )
        assert len(entry.elem_sha256) == 64, "elem_sha256 должен быть SHA-256 hex (64 символа)"

    def test_elem_only_form_sha256_same_as_direct_compute(self, tmp_path: Path) -> None:
        """elem_sha256 в реестре совпадает с прямым вызовом _compute_elem_sha256."""
        from v8unpack_agent.scan_forms import _compute_elem_sha256

        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement"
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        entry = next(e for e in idx.forms if e.elem_json_path is not None)

        direct = _compute_elem_sha256(form_dir)
        assert direct is not None
        assert entry.elem_sha256 == direct, (
            "elem_sha256 в реестре должен совпадать с _compute_elem_sha256"
        )

    def test_elem_only_form_without_content_has_none_sha256(self, tmp_path: Path) -> None:
        """Elem-only форма с пустым/нераспознаваемым elem.json → elem_sha256 is None."""
        form_dir = (
            tmp_path / "Catalog" / "Empty" / "CatalogForm" / "FormEmpty"
        )
        form_dir.mkdir(parents=True, exist_ok=True)
        # elem.json есть, но пустой объект — parse_elem_json вернёт elem_index_ok=False
        (form_dir / "CatalogForm.elem.json").write_text("{}", encoding="utf-8")

        idx = scan_forms(tmp_path, include_elem_only=True)
        elem_entries = [e for e in idx.forms if e.elem_json_path is not None]
        assert elem_entries, "Форма должна попасть в индекс"
        # пустой elem.json → None sha256 (parse_elem_json: нет elements)
        assert elem_entries[0].elem_sha256 is None, (
            "Для пустого elem.json elem_sha256 должен быть None"
        )

    def test_elem_json_path_and_sha256_both_present(self, tmp_path: Path) -> None:
        """При наличии *.elem.json оба поля elem_json_path и elem_sha256 заполнены вместе."""
        _make_elem_only_form(
            tmp_path, "Document", "Orders", "DocumentForm", "FormManaged"
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        entry = next(e for e in idx.forms if e.elem_json_path is not None)
        # Оба поля либо одновременно None, либо одновременно заполнены
        # (для валидного elem.json — оба заполнены)
        assert entry.elem_json_path is not None
        assert entry.elem_sha256 is not None

    def test_elem_sha256_round_trip_preserved(self, tmp_path: Path) -> None:
        """elem_sha256 сохраняется в JSON и корректно восстанавливается через load."""
        _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement"
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        original_hash = next(
            e.elem_sha256 for e in idx.forms if e.elem_sha256 is not None
        )
        save_path = tmp_path / "idx.json"
        idx.save(save_path)
        loaded = FormScanIndex.load(save_path)
        restored_hash = next(
            e.elem_sha256 for e in loaded.forms if e.elem_sha256 is not None
        )
        assert restored_hash == original_hash, (
            "elem_sha256 должен пережить save/load round-trip"
        )


# ---------------------------------------------------------------------------
# AC2: Изменение структуры → structure_modified
# ---------------------------------------------------------------------------

class TestStructureModifiedOnElemChange:
    """AC2: изменение parse_elem_json-структуры elem-only формы
    приводит к structure_modified в drift_checker."""

    def test_elem_only_structure_change_triggers_structure_modified(
        self, tmp_path: Path
    ) -> None:
        """Добавление элемента в elem-only форму → structure_modified."""
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=[
                {"name": "Field1", "type": "InputField", "parent": None,
                 "parent_path": None, "path": "Field1", "page": None, "source": "data"},
            ],
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        # Меняем структуру: добавляем элемент
        _make_elem_json(
            form_dir,
            "CatalogForm.elem.json",
            [
                {"name": "Field1", "type": "InputField", "parent": None,
                 "parent_path": None, "path": "Field1", "page": None, "source": "data"},
                {"name": "Button1", "type": "Button", "parent": None,
                 "parent_path": None, "path": "Button1", "page": None, "source": "data"},
            ],
        )

        report = check_drift(tmp_path, save_path)
        assert any(
            "FormManagedElement" in k for k in report.structure_modified
        ), f"Ожидался structure_modified для FormManagedElement, got: {report.structure_modified}"
        assert report.has_drift is True

    def test_elem_only_element_removal_triggers_structure_modified(
        self, tmp_path: Path
    ) -> None:
        """Удаление элемента из elem-only формы → structure_modified."""
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        # Убираем один элемент
        _make_elem_json(
            form_dir,
            "CatalogForm.elem.json",
            [_structural_elements()[0]],  # только первый
        )

        report = check_drift(tmp_path, save_path)
        assert any(
            "FormManagedElement" in k for k in report.structure_modified
        ), f"Ожидался structure_modified, got: {report.structure_modified}"

    def test_elem_only_type_change_triggers_structure_modified(
        self, tmp_path: Path
    ) -> None:
        """Смена типа элемента (структурное поле) → structure_modified."""
        elements_v1 = [
            {"name": "Field1", "type": "InputField", "parent": None,
             "parent_path": None, "path": "Field1", "page": None, "source": "data"},
        ]
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=elements_v1,
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        elements_v2 = [
            {"name": "Field1", "type": "Label", "parent": None,  # тип изменён
             "parent_path": None, "path": "Field1", "page": None, "source": "data"},
        ]
        _make_elem_json(form_dir, "CatalogForm.elem.json", elements_v2)

        report = check_drift(tmp_path, save_path)
        assert any(
            "FormManagedElement" in k for k in report.structure_modified
        ), f"Смена типа элемента должна давать structure_modified, got: {report.structure_modified}"


# ---------------------------------------------------------------------------
# AC3: Несемантическое изменение НЕ даёт ложный дрейф
# ---------------------------------------------------------------------------

class TestNoFalseDriftOnNonSemanticChange:
    """AC3: GUID, порядок ключей, layout-коды, косметика — не дают ложный дрейф."""

    def test_guid_change_no_false_drift(self, tmp_path: Path) -> None:
        """Изменение GUID в elem.json не меняет elem_sha256 (GUID — косметика)."""
        from v8unpack_agent.scan_forms import _compute_elem_sha256

        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
            extra_top_level={"guid": "aaaaaaaa-0000-0000-0000-000000000001"},
        )
        hash1 = _compute_elem_sha256(form_dir)

        # Меняем GUID — структура (name/type/path/parent) не изменилась
        _make_elem_json(
            form_dir,
            "CatalogForm.elem.json",
            _structural_elements(),
            extra_top_level={"guid": "bbbbbbbb-ffff-ffff-ffff-ffffffffffff"},
        )
        hash2 = _compute_elem_sha256(form_dir)

        assert hash1 is not None
        assert hash1 == hash2, (
            "Изменение GUID не должно влиять на elem_sha256 (GUID — вне структурных ключей)"
        )

    def test_key_reorder_no_false_drift(self, tmp_path: Path) -> None:
        """Переупорядочивание ключей внутри элемента не меняет elem_sha256.

        Хэш строится через json.dumps(..., sort_keys=True), поэтому порядок
        ключей в исходном JSON не влияет на результат.
        """
        from v8unpack_agent.scan_forms import _compute_elem_sha256

        elements_order_a = [
            {
                "name": "Field1",
                "type": "InputField",
                "parent": None,
                "parent_path": None,
                "path": "Field1",
                "page": None,
                "source": "data",
            }
        ]
        elements_order_b = [
            {
                "source": "data",  # другой порядок ключей
                "page": None,
                "path": "Field1",
                "parent_path": None,
                "parent": None,
                "type": "InputField",
                "name": "Field1",
            }
        ]

        form_dir = tmp_path / "Catalog" / "Banks" / "CatalogForm" / "FormManagedElement"
        form_dir.mkdir(parents=True, exist_ok=True)

        (form_dir / "CatalogForm.elem.json").write_text(
            json.dumps({"elements": elements_order_a}), encoding="utf-8"
        )
        hash_a = _compute_elem_sha256(form_dir)

        (form_dir / "CatalogForm.elem.json").write_text(
            json.dumps({"elements": elements_order_b}), encoding="utf-8"
        )
        hash_b = _compute_elem_sha256(form_dir)

        assert hash_a is not None
        assert hash_a == hash_b, (
            "Переупорядочивание ключей элемента не должно менять elem_sha256 "
            "(sort_keys=True при сериализации)"
        )

    def test_cosmetic_fields_no_false_drift(self, tmp_path: Path) -> None:
        """Косметические поля (left/top/width/height/color/font) не влияют на хэш."""
        from v8unpack_agent.scan_forms import _compute_elem_sha256

        elements = _structural_elements()
        form_dir = tmp_path / "Catalog" / "Banks" / "CatalogForm" / "FormManagedElement"
        form_dir.mkdir(parents=True, exist_ok=True)

        # Без косметики
        (form_dir / "CatalogForm.elem.json").write_text(
            json.dumps({"elements": elements}), encoding="utf-8"
        )
        hash_clean = _compute_elem_sha256(form_dir)

        # С косметикой на верхнем уровне
        cosmetic = {
            "elements": elements,
            "left": 100, "top": 200, "width": 800, "height": 600,
            "color": "#FFFFFF", "font": "Arial",
        }
        (form_dir / "CatalogForm.elem.json").write_text(
            json.dumps(cosmetic), encoding="utf-8"
        )
        hash_cosmetic = _compute_elem_sha256(form_dir)

        assert hash_clean is not None
        assert hash_clean == hash_cosmetic, (
            "Косметические поля верхнего уровня не должны влиять на elem_sha256"
        )

    def test_no_false_drift_in_registry_on_guid_change(self, tmp_path: Path) -> None:
        """Сквозной тест: GUID-изменение в реестре не порождает structure_modified."""
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
            extra_top_level={"guid": "aaaaaaaa-0000-0000-0000-000000000001"},
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        # «Изменяем» файл: только GUID, структура та же
        _make_elem_json(
            form_dir,
            "CatalogForm.elem.json",
            _structural_elements(),
            extra_top_level={"guid": "bbbbbbbb-ffff-ffff-ffff-ffffffffffff"},
        )

        report = check_drift(tmp_path, save_path)
        assert report.structure_modified == [], (
            f"GUID-изменение не должно давать structure_modified, got: {report.structure_modified}"
        )


# ---------------------------------------------------------------------------
# AC4: Отсутствие Form.xml не влияет на дрейф
# ---------------------------------------------------------------------------

class TestFormXmlAbsenceNoDriftImpact:
    """AC4: Form.xml нигде не участвует в расчёте дрейфа."""

    def test_no_drift_without_form_xml(self, tmp_path: Path) -> None:
        """Дрейф не возникает при полном отсутствии Form.xml."""
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
        )
        # Убеждаемся, что Form.xml действительно отсутствует
        assert not (form_dir / "Form.xml").exists(), (
            "Form.xml не должен существовать в тестовой фикстуре"
        )

        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        report = check_drift(tmp_path, save_path)
        assert report.structure_modified == [], (
            "Без изменений structure_modified должен быть пустым"
        )
        assert not report.has_drift, "Дрейфа не должно быть без изменений"

    def test_form_xml_presence_ignored_no_extra_drift(self, tmp_path: Path) -> None:
        """Наличие Form.xml рядом с elem.json не влияет на elem_sha256."""
        from v8unpack_agent.scan_forms import _compute_elem_sha256

        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
        )
        hash_without_xml = _compute_elem_sha256(form_dir)

        # Создаём Form.xml с произвольным контентом
        (form_dir / "Form.xml").write_text(
            '<form xmlns="http://v8.1c.ru/8.1/data/form"><autoCommandBar/></form>',
            encoding="utf-8",
        )
        hash_with_xml = _compute_elem_sha256(form_dir)

        assert hash_without_xml is not None
        assert hash_without_xml == hash_with_xml, (
            "Наличие Form.xml не должно влиять на elem_sha256 "
            "(_compute_elem_sha256 использует только *.elem.json)"
        )

    def test_form_xml_change_no_structure_drift(self, tmp_path: Path) -> None:
        """Изменение содержимого Form.xml НЕ приводит к structure_modified."""
        form_dir = _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
        )
        xml_path = form_dir / "Form.xml"
        xml_path.write_text(
            '<form xmlns="http://v8.1c.ru/8.1/data/form"><autoCommandBar/></form>',
            encoding="utf-8",
        )

        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        # Меняем Form.xml — структура elem.json не тронута
        xml_path.write_text(
            '<form xmlns="http://v8.1c.ru/8.1/data/form"><autoCommandBar show="false"/></form>',
            encoding="utf-8",
        )

        report = check_drift(tmp_path, save_path)
        assert report.structure_modified == [], (
            "Изменение Form.xml не должно давать structure_modified — "
            "только *.elem.json участвует в расчёте"
        )


# ---------------------------------------------------------------------------
# Регрессия #38/#40 (smoke): смешанный индекс ordinary + elem-only
# ---------------------------------------------------------------------------

class TestRegressionSmoke:
    """Smoke-регрессии: #38 и #40 не ломаются при добавлении elem-only форм.

    Полные регрессионные наборы — test_drift_content_hash.py (#38) и
    test_elem_structure_hash.py (#40).
    """

    def test_ordinary_form_bsl_sha256_unaffected_by_elem_only(
        self, tmp_path: Path
    ) -> None:
        """Регрессия #38: bsl_sha256 ordinary-формы корректен в смешанном индексе."""
        # Обычная форма с кодом
        bsl_content = "// stable procedure"
        _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            bsl_content=bsl_content,
        )
        # Elem-only форма рядом
        _make_elem_only_form(
            tmp_path, "Catalog", "AlcProd", "CatalogForm", "FormManaged",
            elements=_structural_elements(),
        )

        idx = scan_forms(tmp_path, include_elem_only=True)
        ordinary = next(
            e for e in idx.forms
            if e.form_name == "FormElement" and e.bsl_path.exists()
        )
        assert ordinary.bsl_sha256 is not None, "bsl_sha256 должен быть заполнен"
        import hashlib
        expected = hashlib.sha256(bsl_content.encode("utf-8")).hexdigest()
        assert ordinary.bsl_sha256 == expected, (
            "bsl_sha256 ordinary-формы не должен зависеть от elem-only форм"
        )

    def test_ordinary_form_structure_modified_unaffected_by_elem_only(
        self, tmp_path: Path
    ) -> None:
        """Регрессия #40: structure_modified ordinary-формы не ломается при elem-only."""
        elements_v1 = [
            {"name": "Field1", "type": "InputField", "parent": None,
             "parent_path": None, "path": "Field1", "page": None, "source": "data"},
        ]
        ordinary_dir = _make_ordinary_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormElement",
            elements=elements_v1,
        )
        _make_elem_only_form(
            tmp_path, "Catalog", "AlcProd", "CatalogForm", "FormManaged",
            elements=_structural_elements(),
        )

        idx = scan_forms(tmp_path, include_elem_only=True)
        save_path = tmp_path / "idx.json"
        idx.save(save_path)

        # Меняем структуру только у ordinary-формы
        elements_v2 = elements_v1 + [
            {"name": "Button1", "type": "Button", "parent": None,
             "parent_path": None, "path": "Button1", "page": None, "source": "data"},
        ]
        _make_elem_json(ordinary_dir, "CatalogForm.elem.json", elements_v2)

        report = check_drift(tmp_path, save_path)
        assert any(
            "FormElement" in k for k in report.structure_modified
        ), f"FormElement должна быть в structure_modified, got: {report.structure_modified}"
        # Elem-only форма — структура не изменилась, не должна быть в drift
        assert not any(
            "FormManaged" in k for k in report.structure_modified
        ), "FormManaged не менялась — не должна быть в structure_modified"

    def test_no_second_raw_hash_introduced(self, tmp_path: Path) -> None:
        """Регрессия #58: второй/сырой хэш *.elem.json не введён.

        Проверяет, что в FormEntry нет дополнительного поля
        типа elem_file_sha256 / elem_raw_sha256 — только существующий
        структурный elem_sha256 из #40.
        """
        _make_elem_only_form(
            tmp_path, "Catalog", "Banks", "CatalogForm", "FormManagedElement",
            elements=_structural_elements(),
        )
        idx = scan_forms(tmp_path, include_elem_only=True)
        entry = next(e for e in idx.forms if e.elem_json_path is not None)
        serialized = idx.to_dict()["forms"][0]

        forbidden_fields = {"elem_file_sha256", "elem_raw_sha256", "elem_content_sha256"}
        introduced = forbidden_fields & set(serialized.keys())
        assert not introduced, (
            f"Запрещённые поля сырого хэша найдены в FormEntry: {introduced}"
        )
