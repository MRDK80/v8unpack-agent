# v8unpack-agent

Agent-pipeline adapter for [v8unpack](https://github.com/saby-integration/v8unpack).

Provides three building blocks for LLM-agent pipelines that wrap [v8unpack](https://github.com/saby-integration/v8unpack)
when reading 1C form binaries (`Form.bin`) in an automated, drift-aware way:

| Module | What it does |
|---|---|
| `extractor` | `BinaryExtractor` protocol + `ExtractionResult` (with explicit `extraction_ok` flag) |
| `shadow_tree` | OS-neutral path factory: maps `<source_root>/…/Form.bin` → `<shadow_root>/…/Form/` |
| `sync_index` | `ShadowIndex` — drift detection via `mtime + size`; `DriftReport` |

## Scope

`v8unpack` already unpacks `cf/cfe/epf` into human-readable JSON and extracts
form element code into separate files. This package does **not** re-implement
that. It adds a thin, agent-friendly layer on top:

- a stable `BinaryExtractor` protocol (so a concrete v8unpack-backed extractor
  can be injected without leaking failure modes),
- an OS-neutral shadow-path factory,
- a `mtime + size` drift index so an agent can tell when a previously extracted
  shadow is stale and must be rebuilt.

It does not solve the readability of form layout / object properties — that is a
known limitation of `v8unpack` itself.

## Install

Not yet published to PyPI. Install from the repository:

```bash
pip install "v8unpack>=1.2"
pip install git+https://github.com/MRDK80/v8unpack-agent.git
```

or, from a local checkout:

```bash
pip install .
```

## Reference extractor

The package ships a concrete, optional extractor backed by `v8unpack`,
`V8UnpackExtractor`, that satisfies the `BinaryExtractor` protocol and honours
the no-silent-failure contract (a failed or empty run returns
`extraction_ok=False` with notes instead of raising):

```python
from pathlib import Path
from v8unpack_agent import V8UnpackExtractor, ShadowTreeLayout, shadow_path_for

extractor = V8UnpackExtractor()
layout = ShadowTreeLayout(
    binary_source_root=Path("unpacked_cf/"),
    shadow_tree_root=Path(".shadow/"),
)

for binary in layout.binary_source_root.rglob("*.bin"):
    target = shadow_path_for(binary, layout)
    result = extractor.extract(binary, target)
    if not result.extraction_ok:
        print("degraded:", result.notes)
```

## Quick start

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import ShadowTreeLayout, shadow_path_for, ShadowIndex

# 0. v8unpack first turns the container into a tree of files
#    (this is where the *.bin form binaries live).
source_cf = Path("demo.cf")
unpacked_root = Path("unpacked_cf/")
v8unpack.extract(str(source_cf), str(unpacked_root))

# 1. Set up layout: the unpacked tree is the binary source; shadows live apart.
layout = ShadowTreeLayout(
    binary_source_root=unpacked_root,
    shadow_tree_root=Path(".shadow/"),
)

# 2. shadow_path_for maps each *.bin to its target shadow directory.
binaries = list(layout.binary_source_root.rglob("*.bin"))
for binary in binaries:
    target = shadow_path_for(binary, layout)
    # ... hand `target` to your concrete BinaryExtractor here ...

# 3. Build a drift index over the *same* binaries we just located.
index = ShadowIndex.build(layout.binary_source_root, binaries)
index.save(Path(".shadow_index.json"))

# 4. Later: check for drift against the recorded snapshot.
report = index.check_drift(layout.binary_source_root, binaries)
if not report.is_clean:
    print("Stale shadows:", report.changed)
```

## Tests

```bash
pip install -e ".[test]"
pytest
```

The suite is fully synthetic — `V8UnpackExtractor` is exercised against an
injected fake `v8unpack`, so no real 1C container is needed.

## Related

- Article: *Обычные формы 1С в агентном пайплайне: пошаговая распаковка* (Инфостарт)
- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — the underlying extractor

## License

MIT
