"""Reference :class:`~v8unpack_agent.extractor.BinaryExtractor` implementation
backed by the upstream `v8unpack <https://github.com/saby-integration/v8unpack>`_
package.

This is intentionally thin: it adapts ``v8unpack.extract`` (which returns
nothing and raises on failure) to the package's no-silent-failure contract:

- on success → :class:`ExtractionResult` with ``extraction_ok=True`` and the
  list of files written under ``shadow_root``;
- on any failure → ``extraction_ok=False`` with a non-empty ``notes`` field,
  instead of letting the exception escape.

The dependency on ``v8unpack`` is imported lazily so the rest of the package
(protocol, shadow-tree, drift index) stays importable without it installed.
"""
from __future__ import annotations

from pathlib import Path

from v8unpack_agent.extractor import ExtractionResult


class V8UnpackExtractor:
    """Concrete :class:`BinaryExtractor` that delegates to ``v8unpack.extract``.

    Parameters
    ----------
    extract_options:
        Optional dict forwarded to ``v8unpack.extract(..., options=...)``.
        Left as ``None`` by default.
    """

    def __init__(self, extract_options: dict | None = None) -> None:
        self._options = extract_options

    def extract(self, source: Path, shadow_root: Path) -> ExtractionResult:
        """Extract *source* into *shadow_root* using ``v8unpack``.

        Never raises for an extraction problem — returns an
        ``extraction_ok=False`` result with diagnostic notes instead.
        """
        try:
            import v8unpack  # lazy: keep the dep optional for the rest of the pkg
        except ImportError as exc:  # pragma: no cover - environment dependent
            return ExtractionResult(
                extraction_ok=False,
                notes=(f"v8unpack is not installed: {exc}",),
            )

        if not source.exists():
            return ExtractionResult(
                extraction_ok=False,
                notes=(f"Source binary does not exist: {source}",),
            )

        shadow_root.mkdir(parents=True, exist_ok=True)
        try:
            v8unpack.extract(
                str(source), str(shadow_root), options=self._options
            )
        except Exception as exc:  # noqa: BLE001 - contract: no exception escapes
            return ExtractionResult(
                extraction_ok=False,
                notes=(f"v8unpack.extract failed for {source}: {exc!r}",),
            )

        text_files = tuple(
            p.resolve() for p in sorted(shadow_root.rglob("*")) if p.is_file()
        )
        if not text_files:
            return ExtractionResult(
                extraction_ok=False,
                notes=(
                    f"v8unpack.extract produced no files under {shadow_root}; "
                    "treating as a degraded run.",
                ),
            )

        return ExtractionResult(extraction_ok=True, text_files=text_files)
