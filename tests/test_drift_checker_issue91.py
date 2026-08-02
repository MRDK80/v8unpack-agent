"""Тесты для issue #91: check_drift must raise NotADirectoryError when
cf_export_root is not a directory, and warn when scan yields zero forms.

До фикса:
- test_check_drift_raises_when_root_is_file         → FAIL (нет исключения)
- test_check_drift_raises_when_root_is_missing      → FAIL (нет исключения)
- test_check_drift_warns_zero_forms                 → FAIL (нет предупреждения)

После фикса все три должны быть зелёными.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from v8unpack_agent.drift_checker import check_drift


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _make_index(tmp_path: Path, forms: list[dict]) -> Path:
    """Сохранить минимальный forms_index.json и вернуть путь."""
    index_path = tmp_path / "forms_index.json"
    index_path.write_text(
        json.dumps({"forms": forms}, ensure_ascii=False),
        encoding="utf-8",
    )
    return index_path


def _one_form_entry() -> dict:
    """Одна запись формы без BSL-пути — достаточно для baseline."""
    return {
        "object_type": "Catalog",
        "object_name": "Товары",
        "container_name": "CatalogForm",
        "form_name": "ФормаЭлемента",
        "bsl_path": "",
        "bsl_mtime": 0.0,
        "bsl_sha256": None,
        "elem_sha256": None,
        "elem_json_path": "",
    }


# ---------------------------------------------------------------------------
# Тест 1: cf_export_root — файл, а не директория → NotADirectoryError
# ---------------------------------------------------------------------------

class TestCheckDriftRaisesNotADirectory:
    """check_drift должен бросить NotADirectoryError, если cf_export_root
    указывает на существующий файл, а не на директорию.
    """

    def test_root_is_file_raises(self, tmp_path: Path) -> None:
        """Передача файла вместо директории должна дать NotADirectoryError."""
        index_path = _make_index(tmp_path, [_one_form_entry()])

        # Используем сам index_path как cf_export_root (это файл, не директория)
        with pytest.raises(NotADirectoryError, match="cf_export_root"):
            check_drift(index_path, index_path=index_path)

    def test_root_is_missing_raises(self, tmp_path: Path) -> None:
        """Несуществующий путь должен дать NotADirectoryError."""
        index_path = _make_index(tmp_path, [_one_form_entry()])
        missing = tmp_path / "does_not_exist"

        with pytest.raises(NotADirectoryError, match="cf_export_root"):
            check_drift(missing, index_path=index_path)

    def test_error_message_contains_path(self, tmp_path: Path) -> None:
        """Сообщение об ошибке должно содержать переданный путь."""
        index_path = _make_index(tmp_path, [_one_form_entry()])
        bad_root = index_path  # файл

        with pytest.raises(NotADirectoryError) as exc_info:
            check_drift(bad_root, index_path=index_path)

        assert str(bad_root) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Тест 2: cf_export_root — валидная директория, но форм ноль → WARNING
# ---------------------------------------------------------------------------

class TestCheckDriftZeroFormsWarning:
    """check_drift должен выдать WARNING через logging, если скан валидной
    директории вернул ноль форм (пустой cf_export_root).
    """

    def test_empty_dir_emits_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Пустая директория + существующий baseline → warning в логах."""
        cf_root = tmp_path / "cf_export"
        cf_root.mkdir()
        index_path = _make_index(tmp_path, [_one_form_entry()])

        with caplog.at_level(logging.WARNING, logger="v8unpack_agent.drift_checker"):
            check_drift(cf_root, index_path=index_path)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("zero" in m.lower() or "форм" in m.lower() or "0" in m for m in warning_messages), (
            f"Expected a zero-forms warning, got: {warning_messages}"
        )

    def test_empty_dir_still_returns_drift_report(self, tmp_path: Path) -> None:
        """Даже при пустой директории check_drift должен вернуть DriftReport,
        а не бросить исключение.
        """
        cf_root = tmp_path / "cf_export"
        cf_root.mkdir()
        index_path = _make_index(tmp_path, [_one_form_entry()])

        from v8unpack_agent.drift_checker import DriftReport
        result = check_drift(cf_root, index_path=index_path)
        assert isinstance(result, DriftReport)


# ---------------------------------------------------------------------------
# Регрессионный тест: нормальный сценарий не ломается
# ---------------------------------------------------------------------------

class TestCheckDriftNormalCase:
    """Убедиться, что после фикса штатный вызов с корректной директорией
    по-прежнему работает без исключений.
    """

    def test_valid_dir_no_exception(self, tmp_path: Path) -> None:
        """Валидная пустая директория + пустой baseline → нет исключения."""
        cf_root = tmp_path / "cf_export"
        cf_root.mkdir()
        index_path = _make_index(tmp_path, [])

        from v8unpack_agent.drift_checker import DriftReport
        result = check_drift(cf_root, index_path=index_path)
        assert isinstance(result, DriftReport)
        assert not result.has_drift
