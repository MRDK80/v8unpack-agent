"""Drift index for the binary / shadow pair.

The index records ``(mtime, size)`` for each binary artifact at the moment
its shadow was produced, and later reports any drift between the current
filesystem state and that snapshot.

Why ``mtime + size`` and not ``mtime`` alone
--------------------------------------------
``mtime`` alone is unreliable: copy/rsync/git-checkout may reset it, and on
NTFS several files copied in the same operation can collapse to the same
second. Adding ``size`` catches "same mtime, different bytes" cases at O(1)
cost per file, without reading content.

Why not a content hash
----------------------
A content hash would mean re-reading every binary on every drift check —
prohibitive for large containers.  A ``content_hash`` field is reserved in
:class:`ShadowIndexEntry` for future opt-in use.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable


class DriftKind(str, Enum):
    """Reason a single binary differs from its indexed snapshot."""

    MTIME_CHANGED = "mtime_changed"
    SIZE_CHANGED = "size_changed"
    MISSING_ON_DISK = "missing_on_disk"
    NOT_IN_INDEX = "not_in_index"


@dataclass(frozen=True)
class ShadowIndexEntry:
    """One row of the shadow index.

    ``relative_path`` is stored as a POSIX-style string (``"a/b/c.bin"``)
    so the same index file is portable between Linux and Windows.
    """

    relative_path: str
    mtime: float
    size: int
    content_hash: str | None = None  # reserved; not used by default drift check


@dataclass(frozen=True)
class DriftReport:
    """Outcome of a drift check across the whole binary source tree."""

    changed: tuple[tuple[str, DriftKind], ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        """``True`` when every indexed binary matches the current filesystem."""
        return not self.changed

    def kinds(self) -> set[DriftKind]:
        return {kind for _, kind in self.changed}


def _posix_relative(source: Path, root: Path) -> str:
    """Return ``source`` relative to ``root`` as a portable forward-slash string."""
    try:
        rel = source.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Path {source} is not inside the binary source root {root}."
        ) from exc
    return rel.as_posix()


class ShadowIndex:
    """In-memory representation of the shadow drift index.

    Disk format: a single JSON document written by :meth:`save` and read by
    :meth:`load`. The format is a sorted list of entries — readable in VCS diffs.

    Example
    -------
    >>> from pathlib import Path
    >>> source_root = Path("/repo/src")
    >>> binaries = list(source_root.rglob("*.bin"))
    >>> index = ShadowIndex.build(source_root, binaries)
    >>> index.save(Path("/repo/.shadow_index.json"))
    PosixPath('/repo/.shadow_index.json')
    >>> report = index.check_drift(source_root, binaries)
    >>> report.is_clean
    True
    """

    SCHEMA_VERSION = 1

    def __init__(self, entries: Iterable[ShadowIndexEntry] = ()) -> None:
        self._entries: dict[str, ShadowIndexEntry] = {
            e.relative_path: e for e in entries
        }

    @classmethod
    def build(cls, source_root: Path, binaries: Iterable[Path]) -> "ShadowIndex":
        """Snapshot ``(mtime, size)`` for every file in *binaries*.

        All paths in *binaries* must be existing regular files inside *source_root*.
        """
        entries: list[ShadowIndexEntry] = []
        for path in binaries:
            if not path.is_file():
                raise ValueError(
                    f"Cannot index {path}: it is not an existing regular file."
                )
            stat = path.stat()
            entries.append(
                ShadowIndexEntry(
                    relative_path=_posix_relative(path, source_root),
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                )
            )
        return cls(entries)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "entries": [
                {
                    "relative_path": e.relative_path,
                    "mtime": e.mtime,
                    "size": e.size,
                    "content_hash": e.content_hash,
                }
                for e in sorted(self._entries.values(), key=lambda x: x.relative_path)
            ],
        }

    def save(self, index_path: Path) -> Path:
        """Write the index as UTF-8 JSON to *index_path*."""
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False, ensure_ascii=False),
            encoding="utf-8",
        )
        return index_path

    @classmethod
    def load(cls, index_path: Path) -> "ShadowIndex":
        """Load an index previously written by :meth:`save`.

        A missing file is treated as an empty index — every binary will appear
        as ``NOT_IN_INDEX`` on the next drift check.
        """
        if not index_path.exists():
            return cls()
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError(
                f"Shadow index at {index_path} has unknown schema; refusing to load. "
                "Rebuild it explicitly."
            )
        entries = [
            ShadowIndexEntry(
                relative_path=row["relative_path"],
                mtime=float(row["mtime"]),
                size=int(row["size"]),
                content_hash=row.get("content_hash"),
            )
            for row in raw.get("entries", [])
        ]
        return cls(entries)

    def entries(self) -> tuple[ShadowIndexEntry, ...]:
        return tuple(sorted(self._entries.values(), key=lambda x: x.relative_path))

    def check_drift(
        self,
        source_root: Path,
        current_binaries: Iterable[Path],
    ) -> DriftReport:
        """Compare the recorded snapshot to the current filesystem state."""
        current_by_rel: dict[str, Path] = {
            _posix_relative(p, source_root): p for p in current_binaries
        }
        changes: list[tuple[str, DriftKind]] = []

        for rel, indexed in self._entries.items():
            current_path = current_by_rel.get(rel)
            if current_path is None or not current_path.exists():
                changes.append((rel, DriftKind.MISSING_ON_DISK))
                continue
            stat = current_path.stat()
            if stat.st_size != indexed.size:
                changes.append((rel, DriftKind.SIZE_CHANGED))
                continue
            if stat.st_mtime != indexed.mtime:
                changes.append((rel, DriftKind.MTIME_CHANGED))

        for rel in current_by_rel:
            if rel not in self._entries:
                changes.append((rel, DriftKind.NOT_IN_INDEX))

        return DriftReport(changed=tuple(sorted(changes)))
