# tests/test_skd_extractor_batch.py
"""
Тесты для extract_all_skd_queries (SkdBatchResult) и _guess_report_root.
Все фикстуры синтетические — реальные .erf и конфигурации не используются.
"""
from pathlib import Path

import pytest

from v8unpack_agent.skd_extractor import (
    _guess_report_root,
    extract_all_skd_queries,
    extract_skd_queries,
)


# ---------------------------------------------------------------------------
# Синтетический Template.bin
# ---------------------------------------------------------------------------

def _make_template_bin(dataset_name: str = "Dataset1") -> bytes:
    # XML строится как str (кириллица допустима), затем кодируется в UTF-8
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<DataCompositionSchema>"
        f'<DataSets><DataSet name="{dataset_name}">'
        # ВЫБРАТЬ — кириллица, именно это ищет regex в skd_extractor
        "<Query>ВЫБРАТЬ 1 КАК Поле</Query>"
        "</DataSet></DataSets>"
        "</DataCompositionSchema>"
    )
    payload = b"\xef\xbb\xbf" + xml.encode("utf-8")  # BOM + UTF-8 XML
    return b"\x00" * 8 + payload                      # v8-заголовок 8 байт


def _make_report(base: Path, name: str) -> None:
    """Создать синтетический распакованный отчёт под base/Report/<name>/..."""
    t = base / "Report" / name / "Template" / "SKD" / "Template.bin"
    t.parent.mkdir(parents=True)
    t.write_bytes(_make_template_bin(name))


# ---------------------------------------------------------------------------
# _guess_report_root: нестандартные пути
# ---------------------------------------------------------------------------

class TestGuessReportRoot:
    """Эвристика поиска корня отчёта по пути до Template.bin."""

    def test_standard_report_convention(self, tmp_path):
        # src/Report/<Name>/Template/<SKD>/Template.bin
        t = (
            tmp_path / "src" / "Report" / "StockReport"
            / "Template" / "SKD" / "Template.bin"
        )
        t.parent.mkdir(parents=True)
        t.touch()
        assert _guess_report_root(t) == tmp_path / "src" / "Report" / "StockReport"

    def test_nested_report_segment(self, tmp_path):
        # глубже: a/b/Report/<Name>/...
        t = (
            tmp_path / "a" / "b" / "Report" / "SalesReport"
            / "Template" / "SKD" / "Template.bin"
        )
        t.parent.mkdir(parents=True)
        t.touch()
        assert _guess_report_root(t) == tmp_path / "a" / "b" / "Report" / "SalesReport"

    def test_no_report_segment_fallback_to_grandparent(self, tmp_path):
        # Нет сегмента "Report" — fallback: parents[2]
        # some/custom/ReportName/Template/SKD/Template.bin
        t = (
            tmp_path / "some" / "custom" / "ReportName"
            / "Template" / "SKD" / "Template.bin"
        )
        t.parent.mkdir(parents=True)
        t.touch()
        # parents[2] от Template.bin = some/custom/ReportName ✓
        assert _guess_report_root(t) == tmp_path / "some" / "custom" / "ReportName"

    def test_shallow_path_one_level(self, tmp_path):
        # Минимальная валидная структура без "Report":
        # <Имя>/Template/<СКД>/Template.bin — parents[2] = <Имя>
        t = tmp_path / "ReportRoot" / "Template" / "SKD" / "Template.bin"
        t.parent.mkdir(parents=True)
        t.touch()
        assert _guess_report_root(t) == tmp_path / "ReportRoot"


# ---------------------------------------------------------------------------
# extract_all_skd_queries: несколько отчётов под одним корнем
# ---------------------------------------------------------------------------

def test_batch_extracts_all_reports(tmp_path):
    """При двух отчётах под корнем извлекаются оба, а не только первый."""
    _make_report(tmp_path, "StockReport")
    _make_report(tmp_path, "SalesReport")

    batch = extract_all_skd_queries(tmp_path)

    assert batch.skd_extracted is True
    assert len(batch.results) == 2
    names = {r.datasets[0]["name"] for r in batch.results if r.datasets}
    assert len(names) == 2


def test_batch_single_report_same_as_per_report(tmp_path):
    """Один отчёт под корнем — результат совпадает с покейсовым вызовом."""
    _make_report(tmp_path, "SingleReport")

    batch = extract_all_skd_queries(tmp_path)
    single = extract_skd_queries(tmp_path / "Report" / "SingleReport")

    assert batch.skd_extracted == single.skd_extracted
    assert len(batch.results) == 1
    assert batch.results[0].datasets == single.datasets


def test_batch_one_broken_report_does_not_stop_others(tmp_path):
    """Ошибка одного отчёта не роняет остальные — graceful degradation."""
    _make_report(tmp_path, "WorkingReport")

    broken = (
        tmp_path / "Report" / "BrokenReport"
        / "Template" / "SKD" / "Template.bin"
    )
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"\x00" * 8 + b"not-xml-at-all")

    batch = extract_all_skd_queries(tmp_path)

    assert len(batch.results) == 2
    working = next((r for r in batch.results if r.skd_extracted), None)
    assert working is not None, "Рабочий отчёт должен быть извлечён"
    assert working.datasets


def test_batch_no_template_bin_returns_not_extracted(tmp_path):
    """Нет ни одного Template.bin — skd_extracted=False, без исключения."""
    batch = extract_all_skd_queries(tmp_path)

    assert batch.skd_extracted is False
    assert batch.results == []
    assert batch.warnings


# ---------------------------------------------------------------------------
# Регрессия: str-пути не вызывают AttributeError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path_type", [str, Path], ids=["str", "Path"])
def test_extract_all_skd_queries_accepts_str_and_path(tmp_path, path_type):
    """Регрессия: extract_all_skd_queries не должна бросать
    AttributeError при передаче строки вместо pathlib.Path."""
    _make_report(tmp_path, "TestReport")
    root = path_type(tmp_path)

    # Главное: не должно бросать исключение
    batch = extract_all_skd_queries(root)
    assert batch.skd_extracted is True


@pytest.mark.parametrize("path_type", [str, Path], ids=["str", "Path"])
def test_extract_skd_queries_accepts_str_and_path(tmp_path, path_type):
    """Регрессия: extract_skd_queries не должна бросать
    AttributeError при передаче строки вместо pathlib.Path."""
    _make_report(tmp_path, "TestReport")
    report_root = path_type(tmp_path / "Report" / "TestReport")

    result = extract_skd_queries(report_root)
    assert result.skd_extracted is True
