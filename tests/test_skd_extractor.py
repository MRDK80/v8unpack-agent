"""Тесты для skd_extractor.

Фикстуры создают Template.bin в реальном формате v8-контейнера:
24 байта нулевого заголовка + UTF-8 BOM + XML.
Это соответствует структуре, которую генерирует v8unpack при распаковке .erf.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v8unpack_agent.skd_extractor import extract_skd_queries


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_v8_container(xml_body: str) -> bytes:
    """24-байтный заголовок + BOM + XML — реальный формат v8unpack."""
    header = b"\x00" * 24
    payload = b"\xef\xbb\xbf" + xml_body.encode("utf-8")
    return header + payload


def _make_v8_container_no_bom(xml_body: str) -> bytes:
    """Контейнер без BOM — fallback через <?xml."""
    return b"\x00" * 24 + xml_body.encode("utf-8")


def _xml_with_query(query: str, dataset_name: str = "MainDataset") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<DataCompositionSchema>"
        f'<DataSet Name="{dataset_name}">'
        f"<Query>{query}</Query>"
        "</DataSet>"
        "</DataCompositionSchema>"
    )


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def report_dir(tmp_path: Path) -> Path:
    """Корень распакованного .erf с подпапкой Template/ОсновнаяСхемаКомпоновкиДанных."""
    skd_dir = tmp_path / "Template" / "ОсновнаяСхемаКомпоновкиДанных"
    skd_dir.mkdir(parents=True)
    return tmp_path


def _write_template(report_root: Path, content: bytes) -> None:
    skd_dir = report_root / "Template" / "ОсновнаяСхемаКомпоновкиДанных"
    (skd_dir / "Template.bin").write_bytes(content)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_single_dataset_extracted(self, report_dir: Path) -> None:
        xml = _xml_with_query(
            "ВЫБРАТЬ Ссылка, Наименование ИЗ Справочник.Номенклатура",
            dataset_name="Номенклатура",
        )
        _write_template(report_dir, _make_v8_container(xml))

        result = extract_skd_queries(report_dir)

        assert result.skd_extracted is True
        assert len(result.datasets) == 1
        assert result.datasets[0]["name"] == "Номенклатура"
        assert "ВЫБРАТЬ" in result.datasets[0]["query"]
        assert result.warnings == []

    def test_json_written_to_report_dir(self, report_dir: Path) -> None:
        """skd_queries.json пишется непосредственно в report_dir."""
        xml = _xml_with_query("ВЫБРАТЬ 1 ИЗ Справочник.Валюты")
        _write_template(report_dir, _make_v8_container(xml))

        extract_skd_queries(report_dir)

        json_path = report_dir / "skd_queries.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert isinstance(data, list) and len(data) == 1

    def test_no_bom_fallback(self, report_dir: Path) -> None:
        """Контейнер без BOM — fallback через <?xml работает."""
        xml = '<?xml version="1.0"?><root><q>ВЫБРАТЬ Ссылка ИЗ Справочник.Контрагенты</q></root>'
        _write_template(report_dir, _make_v8_container_no_bom(xml))

        result = extract_skd_queries(report_dir)

        assert result.skd_extracted is True
        assert len(result.datasets) == 1

    def test_multiple_datasets(self, report_dir: Path) -> None:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Schema>"
            '<DataSet Name="DS1"><Query>ВЫБРАТЬ Ссылка ИЗ Справочник.Номенклатура</Query></DataSet>'
            '<DataSet Name="DS2"><Query>ВЫБРАТЬ Ссылка ИЗ Справочник.Контрагенты</Query></DataSet>'
            "</Schema>"
        )
        _write_template(report_dir, _make_v8_container(xml))

        result = extract_skd_queries(report_dir)

        assert result.skd_extracted is True
        assert len(result.datasets) == 2


class TestFallback:
    def test_no_template_bin(self, tmp_path: Path) -> None:
        result = extract_skd_queries(tmp_path)

        assert result.skd_extracted is False
        assert len(result.warnings) > 0

    def test_no_queries_in_xml(self, report_dir: Path) -> None:
        xml = '<?xml version="1.0"?><Schema><Info>Нет запросов</Info></Schema>'
        _write_template(report_dir, _make_v8_container(xml))

        result = extract_skd_queries(report_dir)

        assert result.skd_extracted is False
        assert any("не найдены" in w for w in result.warnings)

    def test_corrupt_file_no_bom_no_xml(self, report_dir: Path) -> None:
        """Бинарный мусор без BOM и <?xml — skd_extracted=False."""
        _write_template(report_dir, bytes(range(256)))

        result = extract_skd_queries(report_dir)

        assert result.skd_extracted is False
        assert len(result.warnings) > 0
