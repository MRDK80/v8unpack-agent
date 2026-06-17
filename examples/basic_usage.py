"""Minimal end-to-end demo: unpack a .cf and build a drift index.

Requires v8unpack to be installed: pip install v8unpack v8unpack-agent

Usage:
    python examples/basic_usage.py <path_to.cf> <output_dir>
"""
import sys
from pathlib import Path

import v8unpack
from v8unpack_agent import ShadowTreeLayout, ShadowIndex, shadow_path_for

def main(cf_path: str, out_dir: str) -> None:
    source = Path(cf_path)
    shadow_root = Path(out_dir)
    shadow_root.mkdir(parents=True, exist_ok=True)

    print(f"Unpacking {source} → {shadow_root}")
    v8unpack.extract(str(source), str(shadow_root))

    layout = ShadowTreeLayout(
        binary_source_root=source.parent,
        shadow_tree_root=shadow_root,
    )

    binaries = list(source.parent.glob("*.cf")) + list(source.parent.glob("*.epf"))
    if binaries:
        index = ShadowIndex.build(layout.binary_source_root, binaries)
        index_path = shadow_root / "shadow_index.json"
        index.save(index_path)
        print(f"Index saved: {index_path}")

        report = index.check_drift(layout.binary_source_root, binaries)
        print(f"Drift clean: {report.is_clean}")
    else:
        print("No binaries found for indexing.")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: basic_usage.py <path_to.cf> <output_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
