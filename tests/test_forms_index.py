from pathlib import Path

from v8unpack_agent import FormsIndex, FormsIndexEntry, is_form_stale


def _entry(bin_mtime: float, unpacked_mtime: float, **kw) -> FormsIndexEntry:
    return FormsIndexEntry(
        bin_path="Forms/Ф/Ext/Form.bin",
        unpacked_root="unpacked/Form/Ф/",
        bin_mtime=bin_mtime,
        unpacked_mtime=unpacked_mtime,
        **kw,
    )


def test_is_form_stale_when_bin_newer():
    assert is_form_stale(_entry(200.0, 100.0)) is True
    assert is_form_stale(_entry(100.0, 200.0)) is False
    assert is_form_stale(_entry(100.0, 100.0)) is False


def test_is_form_stale_accepts_dict():
    assert is_form_stale({"bin_mtime": 5, "unpacked_mtime": 4}) is True


def test_entry_fields_match_article():
    e = _entry(1.0, 1.0)
    assert set(e.__dataclass_fields__) == {
        "bin_path",
        "unpacked_root",
        "bin_mtime",
        "unpacked_mtime",
        "extraction_ok",
        "warnings",
    }


def test_stale_forms_listing():
    idx = FormsIndex()
    idx.upsert("Свежая", _entry(100.0, 200.0))
    idx.upsert("Устаревшая", _entry(300.0, 100.0))
    assert idx.stale_forms() == ("Устаревшая",)


def test_save_load_roundtrip(tmp_path):
    idx = FormsIndex()
    idx.upsert("ФормаЭлемента", _entry(100.0, 150.0, extraction_ok=True, warnings=[]))
    idx.upsert(
        "ФормаСписка",
        _entry(400.0, 100.0, extraction_ok=False, warnings=["частичная"]),
    )
    path = idx.save(tmp_path / "forms_index.json")
    loaded = FormsIndex.load(path)
    assert loaded.entries() == idx.entries()
    assert loaded.get("ФормаСписка").warnings == ["частичная"]
    assert loaded.stale_forms() == ("ФормаСписка",)


def test_load_missing_is_empty(tmp_path):
    assert FormsIndex.load(tmp_path / "nope.json").entries() == {}
