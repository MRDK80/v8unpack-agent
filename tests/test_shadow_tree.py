from pathlib import Path

import pytest

from v8unpack_agent import ShadowTreeLayout, shadow_path_for


def _layout(tmp_path: Path) -> ShadowTreeLayout:
    return ShadowTreeLayout(
        binary_source_root=tmp_path / "src",
        shadow_tree_root=tmp_path / "shadow",
    )


def test_maps_bin_to_dir_dropping_extension(tmp_path):
    layout = _layout(tmp_path)
    src = layout.binary_source_root / "Catalogs" / "Demo" / "Form.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")

    target = shadow_path_for(src, layout)
    assert target == layout.shadow_tree_root / "Catalogs" / "Demo" / "Form"


def test_preserves_nested_relative_path(tmp_path):
    layout = _layout(tmp_path)
    src = layout.binary_source_root / "a" / "b" / "c" / "Form.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")

    target = shadow_path_for(src, layout)
    assert target.relative_to(layout.shadow_tree_root) == Path("a/b/c/Form")


def test_rejects_source_outside_root(tmp_path):
    layout = _layout(tmp_path)
    layout.binary_source_root.mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "Form.bin"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")

    with pytest.raises(ValueError):
        shadow_path_for(outside, layout)


def test_rejects_source_root_itself(tmp_path):
    layout = _layout(tmp_path)
    layout.binary_source_root.mkdir(parents=True)
    with pytest.raises(ValueError):
        shadow_path_for(layout.binary_source_root, layout)
