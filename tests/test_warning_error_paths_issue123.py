"""Пути внутри текстов исключений в warnings (issue #123).

Явный ``Path`` может быть обезличен, но ``PermissionError``/``OSError``
часто повторяет тот же путь внутри ``str(exc)``. Эти тесты синтетически
фиксируют такой сценарий для обоих источников предупреждений.
"""

from __future__ import annotations

from pathlib import Path

from v8unpack_agent.elem_parser import parse_elem_json
from v8unpack_agent.object_decoder import DecodeError, decode_object_attributes


def _permission_error(path: Path) -> PermissionError:
    return PermissionError(13, "Permission denied", str(path))


def test_parse_elem_json_anonymizes_path_inside_read_error(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "private-root"
    form_dir = root / "Catalog" / "Справочник1" / "CatalogForm" / "ФормаЭлемента"
    form_dir.mkdir(parents=True)
    elem_path = form_dir / "Form.elem.json"
    elem_path.write_text("{}", encoding="utf-8")

    original = Path.read_text

    def denied(self: Path, *args, **kwargs):
        if self == elem_path:
            raise _permission_error(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    result = parse_elem_json(form_dir)
    text = "\n".join(result.warnings)

    assert result.elem_index_ok is False
    assert str(root) not in text
    assert str(elem_path) not in text
    assert "Permission denied" in text
    assert "ФормаЭлемента/Form.elem.json" in text


def test_object_decoder_anonymizes_path_inside_read_error(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "private-root"
    object_json = root / "Catalog" / "Справочник1" / "Catalog.json"
    object_json.parent.mkdir(parents=True)
    object_json.write_text("{}", encoding="utf-8")

    original = Path.read_text

    def denied(self: Path, *args, **kwargs):
        if self == object_json:
            raise _permission_error(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    result = decode_object_attributes(object_json)
    text = "\n".join(result.warnings)

    assert result.ok is False
    assert result.error is DecodeError.JSON_PARSE_ERROR
    assert str(root) not in text
    assert str(object_json) not in text
    assert "Permission denied" in text
    assert text.count(".../Catalog/Справочник1/Catalog.json") == 2


def test_safe_error_text_handles_synthetic_windows_separator() -> None:
    from v8unpack_agent._safe_paths import safe_error_text

    path = r"C:\Users\synthetic-user\dump\Catalog\Справочник1\Catalog.json"
    error = PermissionError(f"Permission denied: {path}")

    text = safe_error_text(error, path)

    assert "C:" not in text
    assert "synthetic-user" not in text
    assert "\\" not in text
    assert "Permission denied" in text
    assert ".../dump/Catalog/Справочник1/Catalog.json" in text


def test_safe_error_text_handles_escaped_windows_filename() -> None:
    from v8unpack_agent._safe_paths import safe_error_text

    path = r"C:\Users\synthetic-user\dump\Catalog\Справочник1\Catalog.json"
    escaped = path.replace("\\", "\\\\")
    error = PermissionError(f"[Errno 13] Permission denied: '{escaped}'")

    text = safe_error_text(error, path, tail=3)

    assert "C:" not in text
    assert "synthetic-user" not in text
    assert "\\" not in text
    assert "Permission denied" in text
    assert text.count(".../Catalog/Справочник1/Catalog.json") == 1
