"""v8unpack-agent — agent-pipeline adapter for v8unpack.

Public surface
--------------
- :class:`~v8unpack_agent.extractor.ExtractionResult`
- :class:`~v8unpack_agent.extractor.BinaryExtractor`
- :class:`~v8unpack_agent.shadow_tree.ShadowTreeLayout`
- :func:`~v8unpack_agent.shadow_tree.shadow_path_for`
- :class:`~v8unpack_agent.sync_index.ShadowIndex`
- :class:`~v8unpack_agent.sync_index.DriftReport`
- :class:`~v8unpack_agent.sync_index.DriftKind`
"""

from v8unpack_agent.extractor import BinaryExtractor, ExtractionResult
from v8unpack_agent.shadow_tree import ShadowTreeLayout, shadow_path_for
from v8unpack_agent.sync_index import DriftKind, DriftReport, ShadowIndex

__all__ = [
    "BinaryExtractor",
    "ExtractionResult",
    "ShadowTreeLayout",
    "shadow_path_for",
    "ShadowIndex",
    "DriftReport",
    "DriftKind",
]
