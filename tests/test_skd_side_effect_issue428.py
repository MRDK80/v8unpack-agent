"""Побочная запись ``skd_queries.json`` в выгрузку и её отражение в каталоге (#428).

Синтетическая выгрузка содержит ``Template.bin`` с запросом СКД; реальный
вызов ``cli.main`` сравнивается с записью каталога ``--describe-cli``.
"""

from __future__ import annotations

from pathlib import Path

from v8unpack_agent import _cli_catalog as catalog
from v8unpack_agent import cli

SKD_FILE = "skd_queries.json"


def _template_bin() -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<DataCompositionSchema>"
        '<DataSets><DataSet name="Набор">'
        "<Query>ВЫБРАТЬ 1 КАК Поле</Query>"
        "</DataSet></DataSets>"
        "</DataCompositionSchema>"
    )
    return b"\x00" * 8 + b"\xef\xbb\xbf" + xml.encode("utf-8")


def _export(root: Path) -> Path:
    template = root / "Report" / "Образец" / "Template" / "SKD" / "Template.bin"
    template.parent.mkdir(parents=True)
    template.write_bytes(_template_bin())
    return root


def _written(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob(SKD_FILE))


def _run_effects() -> list[dict[str, str]]:
    for command in catalog.build_catalog()["commands"]:
        if command["name"] == "v8unpack-agent-run":
            return list(command["side_effects"])
    raise AssertionError("v8unpack-agent-run")


def test_run_writes_skd_queries_into_export(tmp_path: Path) -> None:
    root = _export(tmp_path / "export")
    cli.main([str(root), "--report-path", str(tmp_path / "r.json")])
    assert _written(root) == [f"Report/Образец/{SKD_FILE}"]


def test_skip_skd_leaves_export_unchanged(tmp_path: Path) -> None:
    root = _export(tmp_path / "export")
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    cli.main([str(root), "--report-path", str(tmp_path / "r.json"), "--skip-skd"])
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_catalog_declares_export_write_and_skip_flag() -> None:
    writes = [e for e in _run_effects() if e["action"] == "write" and e["parameter"] == "export_root"]
    assert len(writes) == 1
    assert SKD_FILE in writes[0]["target"]
    assert "--skip-skd" in writes[0]["target"]


def test_catalog_write_targets_match_observed(tmp_path: Path) -> None:
    """Каждый каталог, куда команда фактически пишет, объявлен в каталоге."""
    root = _export(tmp_path / "export")
    report = tmp_path / "out" / "r.json"
    report.parent.mkdir()
    cli.main([str(root), "--report-path", str(report)])
    declared = {e["parameter"] for e in _run_effects() if e["action"] == "write"}
    assert _written(root)
    assert "export_root" in declared
    assert report.is_file() and "--report-path" in declared
