"""OS-neutral path factory for the shadow tree of extracted binaries.

The shadow tree mirrors the relative layout of the binary source tree under
a separate root, so each opaque binary at ``<source_root>/<rel>`` has a
corresponding directory of readable files at ``<shadow_root>/<rel_without_ext>/``.

No environment variables are read here — roots are always passed explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShadowTreeLayout:
    """A pair of resolved roots: the binary source tree and the shadow tree.

    Parameters
    ----------
    binary_source_root:
        Directory that contains (or will contain) the binary artifacts.
    shadow_tree_root:
        Directory where extracted, human-readable shadows will be written.

    Example
    -------
    >>> layout = ShadowTreeLayout(
    ...     binary_source_root=Path("/repo/src"),
    ...     shadow_tree_root=Path("/repo/.shadow"),
    ... )
    """

    binary_source_root: Path
    shadow_tree_root: Path


def _relative_segments(source: Path, source_root: Path) -> tuple[str, ...]:
    """Return ``source`` relative to ``source_root`` as path segments.

    Raises :class:`ValueError` if ``source`` is not inside ``source_root``.
    """
    try:
        rel = source.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Refusing to compute a shadow path: {source} is not inside "
            f"the declared binary source root {source_root}."
        ) from exc
    return rel.parts


def shadow_path_for(source: Path, layout: ShadowTreeLayout) -> Path:
    """Compute the shadow directory path for a given binary *source* file.

    The mapping is::

        <source_root>/<rel_dir>/<name><ext>
            ->
        <shadow_root>/<rel_dir>/<name>/

    The file extension is dropped because one binary expands to a *directory*
    of extracted files. The rest of the relative path is preserved verbatim.

    Parameters
    ----------
    source:
        Path to the binary file (need not exist on disk yet).
    layout:
        Pre-constructed :class:`ShadowTreeLayout` with both roots.

    Returns
    -------
    pathlib.Path
        Absolute path to the directory that will hold the shadow.
    """
    segments = _relative_segments(source, layout.binary_source_root)
    if not segments:
        raise ValueError(
            "Cannot compute a shadow path for the source root itself; "
            "pass a binary file inside the source root."
        )
    *dir_parts, filename = segments
    stem = Path(filename).stem or filename
    return layout.shadow_tree_root.joinpath(*dir_parts, stem)
