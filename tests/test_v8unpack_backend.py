"""Tests for the reference V8UnpackExtractor.

A fake ``v8unpack`` module is injected via ``sys.modules`` so these tests run
without the real upstream package and without any real 1C container.
"""
import sys
import types
from pathlib import Path

import pytest

from v8unpack_agent import V8UnpackExtractor


@pytest.fixture
def fake_v8unpack(monkeypatch):
    """Install a controllable fake ``v8unpack`` module for the duration of a test."""

    def install(extract_impl):
        mod = types.ModuleType("v8unpack")
        mod.extract = extract_impl
        monkeypatch.setitem(sys.modules, "v8unpack", mod)
        return mod

    return install


def test_success_collects_written_files(tmp_path, fake_v8unpack):
    source = tmp_path / "demo.cf"
    source.write_bytes(b"\x00demo\x00")
    shadow = tmp_path / "shadow"

    def fake_extract(in_name, out_name, *, options=None):
        out = Path(out_name)
        (out / "Forms" / "Item").mkdir(parents=True, exist_ok=True)
        (out / "Forms" / "Item" / "module.bsl").write_text("// code", encoding="utf-8")
        (out / "Forms" / "Item" / "form.json").write_text("{}", encoding="utf-8")

    fake_v8unpack(fake_extract)

    result = V8UnpackExtractor().extract(source, shadow)
    assert result.extraction_ok is True
    names = {p.name for p in result.text_files}
    assert names == {"module.bsl", "form.json"}


def test_extract_exception_becomes_failure_result(tmp_path, fake_v8unpack):
    source = tmp_path / "demo.cf"
    source.write_bytes(b"\x00demo\x00")

    def boom(in_name, out_name, *, options=None):
        raise RuntimeError("corrupt container")

    fake_v8unpack(boom)

    result = V8UnpackExtractor().extract(source, tmp_path / "shadow")
    assert result.extraction_ok is False
    assert result.notes  # non-empty, per contract
    assert "corrupt container" in result.notes[0]


def test_missing_source_is_failure(tmp_path, fake_v8unpack):
    fake_v8unpack(lambda *a, **k: None)
    result = V8UnpackExtractor().extract(tmp_path / "nope.cf", tmp_path / "shadow")
    assert result.extraction_ok is False
    assert "does not exist" in result.notes[0]


def test_empty_output_is_degraded(tmp_path, fake_v8unpack):
    source = tmp_path / "demo.cf"
    source.write_bytes(b"\x00demo\x00")

    fake_v8unpack(lambda in_name, out_name, *, options=None: None)  # writes nothing

    result = V8UnpackExtractor().extract(source, tmp_path / "shadow")
    assert result.extraction_ok is False
    assert "no files" in result.notes[0]


def test_options_are_forwarded(tmp_path, fake_v8unpack):
    source = tmp_path / "demo.cf"
    source.write_bytes(b"\x00demo\x00")
    shadow = tmp_path / "shadow"
    seen = {}

    def fake_extract(in_name, out_name, *, options=None):
        seen["options"] = options
        Path(out_name).mkdir(parents=True, exist_ok=True)
        (Path(out_name) / "f.txt").write_text("x", encoding="utf-8")

    fake_v8unpack(fake_extract)

    V8UnpackExtractor(extract_options={"descent": 1}).extract(source, shadow)
    assert seen["options"] == {"descent": 1}
