"""Issue #191: структурная типизация ``form_entry`` в ``build_form_context``.

Контракт: функция принимает любую запись, структурно совместимую с приватным
``_FormEntryProtocol``. Наследование от ``scan_forms.FormEntry`` не требуется,
runtime-валидации протокола нет, а толерантность к старым индексам без
``elem_json_path`` сохраняется.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest

from v8unpack_agent.form_context import build_form_context
from v8unpack_agent.scan_forms import FormEntry

METADATA_KEYS = {
    "form_path",
    "elem_json_path",
    "bsl_sha256",
    "elem_sha256",
    "has_bsl",
    "warnings",
}


@dataclass
class _StructuralEntry:
    """Запись без наследования от ``FormEntry``: только читаемые поля."""

    form_name: str = "ФормаЭлемента"
    container_name: str = "Form"
    object_type: str = "Catalog"
    object_name: str = "Номенклатура"
    form_path: str = ""
    bsl_path: str | None = None
    elem_json_path: str | None = None
    bsl_sha256: str | None = None
    elem_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _LegacyEntry:
    """Старый индекс: атрибута ``elem_json_path`` нет вовсе."""

    form_name: str = "ФормаСписка"
    container_name: str = "Form"
    object_type: str = "Catalog"
    object_name: str = "Номенклатура"
    form_path: str = ""
    bsl_path: str | None = None
    bsl_sha256: str | None = None
    elem_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)


def _make_form_dir(tmp_path: Path) -> Path:
    """Каталог формы в 4-уровневом layout, без файла объекта метаданных."""
    form_dir = tmp_path / "Catalog" / "Номенклатура" / "Form" / "ФормаЭлемента"
    form_dir.mkdir(parents=True)
    return form_dir


def test_real_form_entry_is_accepted(tmp_path):
    form_dir = _make_form_dir(tmp_path)
    bsl_path = form_dir / "Form.obj.bsl"
    bsl_path.write_text(
        "Процедура ПриОткрытии(Отказ)\nКонецПроцедуры\n", encoding="utf-8"
    )
    entry = FormEntry(
        object_type="Catalog",
        object_name="Номенклатура",
        container_name="Form",
        form_name="ФормаЭлемента",
        form_path=form_dir.resolve(),
        bsl_path=bsl_path.resolve(),
        json_path=(form_dir / "Form.json").resolve(),
    )

    context = build_form_context(entry, tmp_path)

    assert context.form_name == "ФормаЭлемента"
    assert context.bsl_text is not None
    assert context.metadata["has_bsl"] is True


def test_structural_entry_accepted_without_inheritance(tmp_path):
    entry = _StructuralEntry(form_path=str(_make_form_dir(tmp_path)))

    assert not isinstance(entry, FormEntry)

    context = build_form_context(entry, tmp_path)

    assert context.form_name == "ФормаЭлемента"
    assert context.container_name == "Form"
    assert context.object_type == "Catalog"


def test_entry_is_not_mutated(tmp_path):
    entry = _StructuralEntry(form_path=str(_make_form_dir(tmp_path)))
    before = asdict(entry)

    build_form_context(entry, tmp_path)

    assert asdict(entry) == before


def test_missing_required_field_is_not_silently_defaulted(tmp_path):
    @dataclass
    class _Broken:
        """Нет ``container_name``: раньше getattr тихо подставлял пустую строку."""

        form_name: str = "ФормаЭлемента"
        object_type: str = "Catalog"
        object_name: str = "Номенклатура"
        form_path: str = ""
        bsl_path: str | None = None
        bsl_sha256: str | None = None
        elem_sha256: str | None = None
        warnings: list[str] = field(default_factory=list)

    entry = _Broken(form_path=str(_make_form_dir(tmp_path)))

    with pytest.raises(AttributeError):
        build_form_context(entry, tmp_path)


def test_empty_object_name_keeps_issue172_contract(tmp_path):
    form_dir = tmp_path / "CommonForm" / "ФормаОбщая"
    form_dir.mkdir(parents=True)
    entry = _StructuralEntry(
        container_name="CommonForm",
        object_name="",
        form_path=str(form_dir),
    )

    context = build_form_context(entry, tmp_path)

    assert context.object_name == ""
    assert context.object_attributes is None
    assert any("layout" in warning for warning in context.metadata["warnings"])


def test_bsl_path_none_keeps_absent_bsl(tmp_path):
    entry = _StructuralEntry(form_path=str(_make_form_dir(tmp_path)), bsl_path=None)

    context = build_form_context(entry, tmp_path)

    assert context.bsl_text is None
    assert context.metadata["has_bsl"] is False


def test_empty_bsl_stays_empty_string(tmp_path):
    form_dir = _make_form_dir(tmp_path)
    bsl_path = form_dir / "Form.obj.bsl"
    bsl_path.write_text("", encoding="utf-8")
    entry = _StructuralEntry(form_path=str(form_dir), bsl_path=str(bsl_path))

    context = build_form_context(entry, tmp_path)

    assert context.bsl_text == ""
    assert context.metadata["has_bsl"] is True


def test_legacy_entry_without_elem_json_path(tmp_path):
    entry = _LegacyEntry(form_path=str(_make_form_dir(tmp_path)))

    assert not hasattr(entry, "elem_json_path")

    context = build_form_context(entry, tmp_path)

    assert context.metadata["elem_json_path"] is None


def test_metadata_keys_and_relations_format_are_unchanged(tmp_path):
    entry = _StructuralEntry(form_path=str(_make_form_dir(tmp_path)))

    context = build_form_context(entry, tmp_path)

    assert set(context.metadata) == METADATA_KEYS
    assert isinstance(context.metadata["warnings"], list)
    assert isinstance(context.resolved_relations, list)


def test_build_form_context_signature_is_unchanged():
    signature = inspect.signature(build_form_context)

    assert list(signature.parameters) == [
        "form_entry",
        "unpacked_root",
        "type_resolver",
    ]

    type_resolver = signature.parameters["type_resolver"]

    assert type_resolver.kind is inspect.Parameter.KEYWORD_ONLY
    assert type_resolver.default is None


def test_form_context_does_not_import_scan_forms_at_runtime():
    code = (
        "import sys\n"
        "import v8unpack_agent.form_context\n"
        "assert 'v8unpack_agent.scan_forms' not in sys.modules\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
