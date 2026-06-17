import os
from pathlib import Path

import pytest

from v8unpack_agent import ShadowIndex
from v8unpack_agent.sync_index import DriftKind


def _make_bin(root: Path, rel: str, data: bytes = b"demo") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_build_then_check_is_clean(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin")
    index = ShadowIndex.build(root, [b])
    report = index.check_drift(root, [b])
    assert report.is_clean
    assert report.changed == ()


def test_size_change_detected(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin", b"short")
    index = ShadowIndex.build(root, [b])
    b.write_bytes(b"a-much-longer-payload")
    report = index.check_drift(root, [b])
    assert not report.is_clean
    assert DriftKind.SIZE_CHANGED in report.kinds()


def test_mtime_change_detected(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin", b"same-size")
    index = ShadowIndex.build(root, [b])
    # rewrite identical-size content with a different mtime
    os.utime(b, (1_000_000_000, 1_000_000_000))
    report = index.check_drift(root, [b])
    assert DriftKind.MTIME_CHANGED in report.kinds()


def test_missing_on_disk_detected(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin")
    index = ShadowIndex.build(root, [b])
    b.unlink()
    report = index.check_drift(root, [])
    assert DriftKind.MISSING_ON_DISK in report.kinds()


def test_not_in_index_detected(tmp_path):
    root = tmp_path / "src"
    b1 = _make_bin(root, "Forms/Item/Form.bin")
    index = ShadowIndex.build(root, [b1])
    b2 = _make_bin(root, "Forms/New/Form.bin")
    report = index.check_drift(root, [b1, b2])
    assert DriftKind.NOT_IN_INDEX in report.kinds()


def test_save_load_roundtrip(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin")
    index = ShadowIndex.build(root, [b])
    path = index.save(tmp_path / ".shadow_index.json")
    loaded = ShadowIndex.load(path)
    assert loaded.entries() == index.entries()
    assert loaded.check_drift(root, [b]).is_clean


def test_load_missing_file_is_empty(tmp_path):
    loaded = ShadowIndex.load(tmp_path / "nope.json")
    assert loaded.entries() == ()


def test_load_unknown_schema_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"schema_version": 999, "entries": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        ShadowIndex.load(p)


def test_relative_paths_are_posix(tmp_path):
    root = tmp_path / "src"
    b = _make_bin(root, "Forms/Item/Form.bin")
    index = ShadowIndex.build(root, [b])
    assert index.entries()[0].relative_path == "Forms/Item/Form.bin"
