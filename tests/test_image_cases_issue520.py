"""Check the fixed image inputs and refusal before decoding oversized files."""
import json
import struct
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from scripts.image_cases_issue520 import (
    MAX_BYTES,
    ImageInputError,
    ImageReason,
    bounded_image,
    lsb_control,
    plan,
)

DATA = Path(__file__).resolve().parents[1] / "docs/research/image_study_issue520"


def test_image_plan_and_original_carriers():
    cases = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))
    assert Counter(case["route_status"] for case in cases) == {
        "active": 18, "not_exercised": 4, "unsupported": 2,
    }
    assert len(plan("defender")) == 720
    assert len(plan("readability")) == 20
    assert len({json.dumps(row, sort_keys=True) for row in plan("defender")}) == 720
    for case in cases:
        if case["file"] is None:
            assert case["image_format"] == "jpeg" and case["channel"] == "alpha"
            continue
        path = DATA / case["file"]
        raw, info = bounded_image(path)
        assert info["sha256"] == case["image"]["sha256"]
        assert (info["width"], info["height"]) == (1600, 180)
        assert bool(info["metadata_text"]) == (case["channel"] == "metadata")
        if case["channel"] == "lsb":
            assert bool(lsb_control(raw)) == (case["image_format"] == "png")


@pytest.mark.parametrize(("raw", "reason"), [
    (b"not an image", ImageReason.FORMAT),
    (b"\xff\xd8\xff\xe0", ImageReason.INVALID),
    (b"\x89PNG\r\n\x1a\n", ImageReason.INVALID),
])
def test_invalid_images_are_rejected(tmp_path, raw, reason):
    path = tmp_path / "input.bin"
    path.write_bytes(raw)
    with pytest.raises(ImageInputError) as caught:
        bounded_image(path)
    assert caught.value.reason == reason


def test_bounds_precede_pillow_open(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("image decoder called before budget check")
    monkeypatch.setattr(Image, "open", unexpected)
    path = tmp_path / "input.bin"
    with path.open("wb") as stream:
        stream.truncate(MAX_BYTES + 1)
    with pytest.raises(ImageInputError) as caught:
        bounded_image(path)
    assert caught.value.reason == ImageReason.BYTES
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 5000, 5000) + bytes(9))
    with pytest.raises(ImageInputError) as caught:
        bounded_image(path)
    assert caught.value.reason == ImageReason.PIXELS


def test_symlink_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"not an image")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ImageInputError) as caught:
        bounded_image(link)
    assert caught.value.reason == ImageReason.SYMLINK
