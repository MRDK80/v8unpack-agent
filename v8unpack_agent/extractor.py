"""Abstract contract for binary-artifact extractors.

The package deliberately does **not** ship a concrete extractor —
it provides the protocol and the result type. Concrete implementations
(e.g. a v8unpack-backed extractor) satisfy :class:`BinaryExtractor`
and are injected at composition time.

Two invariants enforced by this contract:

1. **No silent fallback on failure.** If extraction is incomplete or partial,
   the result must carry ``extraction_ok=False`` and a non-empty ``notes``
   field.
2. **The extractor never decides where the shadow lives.** Output paths are
   handed in by the caller (computed from :mod:`v8unpack_agent.shadow_tree`).
   This keeps the extractor stateless and the path policy single-sourced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of one extraction call.

    Attributes
    ----------
    extraction_ok:
        ``True`` only when the extractor produced a complete, readable shadow.
        ``False`` for any partial, degraded, or failed run.
    text_files:
        Absolute paths to the human-readable files written under the shadow
        root.  May be empty when ``extraction_ok`` is ``False``.
    notes:
        Free-form diagnostic messages. Required (non-empty) when
        ``extraction_ok`` is ``False``; optional otherwise.
    """

    extraction_ok: bool
    text_files: tuple[Path, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.extraction_ok and not self.notes:
            raise ValueError(
                "ExtractionResult with extraction_ok=False must carry at least "
                "one note explaining why. Silent failure is forbidden."
            )


@runtime_checkable
class BinaryExtractor(Protocol):
    """Protocol any concrete binary-artifact extractor must satisfy."""

    def extract(self, source: Path, shadow_root: Path) -> ExtractionResult:
        """Extract *source* into the directory *shadow_root*.

        Parameters
        ----------
        source:
            The opaque binary artifact to read (read-only by contract).
        shadow_root:
            Directory where extracted, human-readable files will be written.

        Returns
        -------
        ExtractionResult
            Never raise for expected, recoverable problems — use
            ``extraction_ok=False`` + ``notes`` instead.
        """
        ...
