# v8unpack-agent

Agent-pipeline adapter for [v8unpack](https://github.com/saby-integration/v8unpack).

Provides three building blocks for LLM-agent pipelines that need to read
1C ordinary-form binaries (`Form.bin`):

| Module | What it does |
|---|---|
| `extractor` | `BinaryExtractor` protocol + `ExtractionResult` (with explicit `extraction_ok` flag) |
| `shadow_tree` | OS-neutral path factory: maps `<source_root>/…/Form.bin` → `<shadow_root>/…/Form/` |
| `sync_index` | `ShadowIndex` — drift detection via `mtime + size`; `DriftReport` |

## Install

```bash
pip install v8unpack-agent
```

## Quick start

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import ShadowTreeLayout, shadow_path_for, ShadowIndex

# 1. Set up layout
layout = ShadowTreeLayout(
    binary_source_root=Path("unpacked_cf/"),
    shadow_tree_root=Path(".shadow/"),
)

# 2. Unpack (v8unpack extracts to a temp dir; shadow_path_for gives the target)
source_cf = Path("demo.cf")
shadow_root = layout.shadow_tree_root
v8unpack.extract(str(source_cf), str(shadow_root))

# 3. Build drift index
binaries = list(layout.binary_source_root.rglob("*.bin"))
index = ShadowIndex.build(layout.binary_source_root, binaries)
index.save(Path(".shadow_index.json"))

# 4. Later: check for drift
report = index.check_drift(layout.binary_source_root, binaries)
if not report.is_clean:
    print("Stale shadows:", report.changed)
```

## Related

- Article: *Бинарные формы 1С в агентном пайплайне: пошаговая распаковка* (Инфостарт)
- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — the underlying extractor

## License

MIT
