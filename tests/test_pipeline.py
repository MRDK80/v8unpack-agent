"""Тесты пайплайна распаковки форм.

После issue #226 распаковщик получает канонический источник формы
(``FormBinSource``), а ключом реестра служит ``form_id`` — относительный
POSIX-путь каталога формы от корня выгрузки. Имя формы остаётся
неуникальным атрибутом.
"""
from pathlib import Path

from v8unpack_agent import (
    FormArtifact,
    discover_form_bins,
    discover_form_sources,
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
    """Распаковщик-заглушка нового протокола: (source, unpacked_root)."""

    def _unpack(source, root: Path) -> FormArtifact:
        artifact = FormArtifact.for_source(root, source)
        form_dir = artifact.paths["object_module"].parent
        form_dir.mkdir(parents=True, exist_ok=True)
        (form_dir / "Form.obj.bsl").write_text("// форма", encoding="utf-8")
        return artifact

    return _unpack


def test_discover_form_bins_extracts_names(tmp_path):
    """Устаревшая карта «имя → путь» сохранена для совместимости."""
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
    assert all(a.form_id.endswith(a.name) for a in arts)
    assert all(a.paths["object_module"].is_file() for a in arts)


def test_unpack_all_forms_selection_by_name(tmp_path):
    """Отбор по уникальному имени формы остаётся рабочим."""
    dump = _make_dump(tmp_path, "ФормаЭлемента", "ФормаСписка")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(
        dump, unpacked, _fake_unpacker(unpacked), form_names=["ФормаСписка"]
    )
    assert [a.name for a in arts] == ["ФормаСписка"]


def test_unpack_all_forms_selection_by_form_id(tmp_path):
    """Канонический отбор идёт по form_id."""
    dump = _make_dump(tmp_path, "ФормаЭлемента", "ФормаСписка")
    unpacked = tmp_path / "unpacked"
    wanted = min(source.form_id for source in discover_form_sources(dump))
    arts = unpack_all_forms(
        dump, unpacked, _fake_unpacker(unpacked), form_ids=[wanted]
    )
    assert [a.form_id for a in arts] == [wanted]


def test_update_forms_index_records_mtimes(tmp_path):
    dump = _make_dump(tmp_path, "ФормаЭлемента")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(dump, unpacked, _fake_unpacker(unpacked))
    idx = update_forms_index(dump, unpacked, arts)
    (artifact,) = arts
    entry = idx.get(artifact.form_id)
    assert entry is not None
    assert entry.form_name == "ФормаЭлемента"
    assert entry.bin_path.endswith("Forms/ФормаЭлемента/Ext/Form.bin")
    assert not Path(entry.bin_path).is_absolute()
    assert not Path(entry.unpacked_root).is_absolute()
    assert entry.extraction_ok is True


def test_pipeline_is_fault_tolerant_on_partial(tmp_path):
    """Частичная форма не валит пайплайн — индекс честно её помечает."""
    dump = _make_dump(tmp_path, "ФормаСписка")
    unpacked = tmp_path / "unpacked"

    def partial_unpacker(source, root: Path) -> FormArtifact:
        artifact = FormArtifact.for_source(
            root,
            source,
            extraction_ok=False,
            extraction_warnings=["вложенная панель не распакована"],
        )
        artifact.paths["object_module"].parent.mkdir(parents=True, exist_ok=True)
        return artifact

    arts = unpack_all_forms(dump, unpacked, partial_unpacker)
    idx = update_forms_index(dump, unpacked, arts)
    (artifact,) = arts
    entry = idx.get(artifact.form_id)
    assert entry.extraction_ok is False
    assert "вложенная панель не распакована" in entry.warnings


def test_idempotent_rerun_keeps_fresh(tmp_path):
    """Повторный прогон без изменений Form.bin не делает форму устаревшей."""
    dump = _make_dump(tmp_path, "ФормаЭлемента")
    unpacked = tmp_path / "unpacked"
    arts = unpack_all_forms(dump, unpacked, _fake_unpacker(unpacked))
    idx = update_forms_index(dump, unpacked, arts)
    (artifact,) = arts
    assert is_form_stale(idx.get(artifact.form_id)) is False
