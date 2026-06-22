from pathlib import Path

from v8unpack_agent import (
    FormArtifact,
    discover_form_bins,
    is_form_stale,
    unpack_all_forms,
    update_forms_index,
)


def _make_dump(tmp_path: Path, *form_names: str) -> Path:
    """Синтетическая выгрузка с .../Forms/<имя>/Ext/Form.bin."""
    dump = tmp_path / "dump"
    for name in form_names:
        ext = dump / "Catalog" / "Номенклатура" / "Forms" / name / "Ext"
        ext.mkdir(parents=True)
        (ext / "Form.bin").write_bytes(b"\x00binary\x00")
    return dump


def _fake_unpacker(unpacked_root: Path):
    """Распаковщик-заглушка: пишет Form.obj.bsl и отдаёт FormArtifact."""

    def _unpack(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
        form_dir = root / "Form" / form_name
        form_dir.mkdir(parents=True, exist_ok=True)
        (form_dir / "Form.obj.bsl").write_text("// форма", encoding="utf-8")
        return FormArtifact.for_form(root, form_name)

    return _unpack


def test_discover_form_bins_extracts_names(tmp_path):
    dump = _make_dump(tmp_path, "ФормаЭлемента", "ФормаСписка")
    bins = discover_form_bins(dump)
    assert set(bins) == {"ФормаЭлемента", "ФормаСписка"}
    assert bins["ФормаЭлемента"].name == "Form.bin"


def test_unpack_all_forms_returns_artifacts(tmp_path):
    dump = _make_dump(tmp_path, "ФормаЭлемента", "ФормаСписка")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(dump, unpacked, _fake_unpacker(unpacked))
    assert {a.name for a in arts} == {"ФормаЭлемента", "ФормаСписка"}
    assert all(a.extraction_ok for a in arts)
    assert (unpacked / "Form" / "ФормаЭлемента" / "Form.obj.bsl").is_file()


def test_unpack_all_forms_selection(tmp_path):
    dump = _make_dump(tmp_path, "ФормаЭлемента", "ФормаСписка")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(
        dump, unpacked, _fake_unpacker(unpacked), form_names=["ФормаСписка"]
    )
    assert [a.name for a in arts] == ["ФормаСписка"]


def test_update_forms_index_records_mtimes(tmp_path):
    dump = _make_dump(tmp_path, "ФормаЭлемента")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(dump, unpacked, _fake_unpacker(unpacked))
    idx = update_forms_index(dump, unpacked, arts)
    entry = idx.get("ФормаЭлемента")
    assert entry is not None
    import os
    assert entry.bin_path.endswith(os.path.join("Forms", "ФормаЭлемента", "Ext", "Form.bin"))
    assert entry.extraction_ok is True


def test_pipeline_is_fault_tolerant_on_partial(tmp_path):
    """Частичная форма не валит пайплайн — индекс честно её помечает."""
    dump = _make_dump(tmp_path, "ФормаСписка")
    unpacked = tmp_path / "unpacked"

    def partial_unpacker(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
        form_dir = root / "Form" / form_name
        form_dir.mkdir(parents=True, exist_ok=True)
        return FormArtifact.for_form(
            root,
            form_name,
            extraction_ok=False,
            extraction_warnings=["вложенная панель не распакована"],
        )

    arts = unpack_all_forms(dump, unpacked, partial_unpacker)
    idx = update_forms_index(dump, unpacked, arts)
    entry = idx.get("ФормаСписка")
    assert entry.extraction_ok is False
    assert entry.warnings == ["вложенная панель не распакована"]


def test_idempotent_rerun_keeps_fresh(tmp_path):
    """Повторный прогон без изменений Form.bin не делает форму устаревшей."""
    dump = _make_dump(tmp_path, "ФормаЭлемента")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(dump, unpacked, _fake_unpacker(unpacked))
    idx = update_forms_index(dump, unpacked, arts)
    entry = idx.get("ФормаЭлемента")
    assert is_form_stale(entry) is False
