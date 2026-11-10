"""Bounded opt-in Unicode metadata, preserving the historical Cf contract (#517).

No normalization, decoding, file IO, policy verdict or transport modification.
Selected ranges are not a complete Default_Ignorable or invisible-character set.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .untrusted_diagnostics import FILE_BOM_BYTES, cf_group

__all__ = [
    "INSPECTION_GROUPS", "MAX_OCCURRENCES", "MAX_SCAN_CHARS", "InspectionReason",
    "UnicodeInspection", "UnicodeOccurrence", "inspect_unicode",
]

MAX_SCAN_CHARS = 1_000_000
MAX_OCCURRENCES = 1000
InspectionGroup = Literal[
    "bidi", "zero_width", "soft_hyphen", "other_cf", "tag",
    "variation_selector", "combining_grapheme_joiner", "invalid_scalar",
]
INSPECTION_GROUPS: tuple[InspectionGroup, ...] = (
    "bidi", "zero_width", "soft_hyphen", "other_cf", "tag",
    "variation_selector", "combining_grapheme_joiner", "invalid_scalar",
)


class InspectionReason(str, Enum):
    """Reasons are ordered: invalid scalar, scan budget, retention budget."""
    UNKNOWN = "unknown"
    INVALID_SCALAR = "invalid_scalar"
    CHAR_BUDGET = "char_budget"
    OCCURRENCE_BUDGET = "occurrence_budget"


@dataclass(frozen=True)
class UnicodeOccurrence:
    codepoint: str
    line: int
    column: int
    group: InspectionGroup
    category: str

    def to_dict(self) -> dict[str, object]:
        return {"codepoint": self.codepoint, "line": self.line, "column": self.column,
                "group": self.group, "category": self.category}


@dataclass(frozen=True)
class UnicodeInspection:
    """An immutable observation, never a safe/attack or authorization verdict."""
    status: Literal["complete", "truncated", "invalid"]
    reasons: tuple[InspectionReason, ...]
    file_bom: bool | None
    unidata_version: str
    input_chars: int
    scanned_chars: int
    occurrences: tuple[UnicodeOccurrence, ...]
    _counts: tuple[tuple[InspectionGroup, int], ...]

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def observed_count(self) -> int:
        return sum(value for _, value in self._counts)

    @property
    def unscanned_chars(self) -> int:
        return self.input_chars - self.scanned_chars

    @property
    def omitted_occurrences(self) -> int:
        return self.observed_count - len(self.occurrences)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "unicode_inspection/1", "status": self.status,
            "reasons": [reason.value for reason in self.reasons],
            "coverage": "cf_and_selected_controls_not_all_invisible",
            "file_bom": self.file_bom, "unidata_version": self.unidata_version,
            "input_chars": self.input_chars, "scanned_chars": self.scanned_chars,
            "unscanned_chars": self.unscanned_chars,
            "counts_scope": "scanned_prefix_only", "counts": self.counts,
            "observed_count": self.observed_count,
            "retained_count": len(self.occurrences),
            "omitted_occurrences": self.omitted_occurrences,
            "occurrences": [item.to_dict() for item in self.occurrences],
        }


def _selected_group(char: str, category: str) -> InspectionGroup | None:
    code = ord(char)
    if 0xD800 <= code <= 0xDFFF:
        return "invalid_scalar"
    if code == 0xE0001 or 0xE0020 <= code <= 0xE007F:
        return "tag"
    if (0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
            or code in {0x180B, 0x180C, 0x180D, 0x180F}):
        return "variation_selector"
    if code == 0x034F:
        return "combining_grapheme_joiner"
    if category == "Cf":
        return cf_group(char)
    return None


def inspect_unicode(
    text: str, *, raw: bytes | bytearray | None = None,
    max_chars: int = MAX_SCAN_CHARS, max_occurrences: int = MAX_OCCURRENCES,
) -> UnicodeInspection:
    """Inspect a bounded prefix without changing or trusting the supplied text.

    Budgets can only decrease hard limits. Prefix counts remain complete even
    when occurrence retention is exhausted. Unknown tail is never declared safe.
    ``raw`` supplies only the BOM marker; it does not authenticate ``text``.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if raw is not None and not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes, bytearray or None")
    for name, value, ceiling in (("max_chars", max_chars, MAX_SCAN_CHARS),
                                  ("max_occurrences", max_occurrences, MAX_OCCURRENCES)):
        if type(value) is not int or not 0 <= value <= ceiling:
            raise ValueError(name + " must be an integer within the hard limit")
    file_bom = None if raw is None else raw.startswith(FILE_BOM_BYTES)
    skip_first = bool(file_bom) and text.startswith("\ufeff")
    scanned = min(len(text), max_chars)
    counts: dict[InspectionGroup, int] = dict.fromkeys(INSPECTION_GROUPS, 0)
    occurrences: list[UnicodeOccurrence] = []
    line, column = 1, 0
    for index in range(scanned):
        char = text[index]
        if char == "\n":
            line, column = line + 1, 0
            continue
        column += 1
        if index == 0 and skip_first:
            continue
        category = unicodedata.category(char)
        group = _selected_group(char, category)
        if group is None:
            continue
        counts[group] += 1
        if len(occurrences) < max_occurrences:
            occurrences.append(UnicodeOccurrence(
                f"U+{ord(char):04X}", line, column, group, category))
    reasons: list[InspectionReason] = []
    if counts["invalid_scalar"]:
        reasons.append(InspectionReason.INVALID_SCALAR)
    if scanned < len(text):
        reasons.append(InspectionReason.CHAR_BUDGET)
    if sum(counts.values()) > len(occurrences):
        reasons.append(InspectionReason.OCCURRENCE_BUDGET)
    status: Literal["complete", "truncated", "invalid"] = (
        "invalid" if counts["invalid_scalar"] else "truncated" if reasons else "complete")
    return UnicodeInspection(status, tuple(reasons), file_bom, unicodedata.unidata_version,
                             len(text), scanned, tuple(occurrences), tuple(counts.items()))
