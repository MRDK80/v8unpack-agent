"""Лексические границы BSL, окна и отдельная поисковая проекция (#324).

Не AST и не проверка синтаксиса BSL. Файлы, сканеры и эмбеддер не вызываются.
Контракт search_contract/1 не меняется; см. docs/search_fragments.md.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerLink,
    SearchDocument,
    SearchDocumentSet,
    TextSpan,
    identity_key,
    to_canonical_json,
)
from v8unpack_agent.search_corpus import CorpusReport, SearchCorpus

__all__ = [
    "SEARCH_FRAGMENTS_SCHEMA",
    "FragmentParameters",
    "FragmentReason",
    "FragmentationItem",
    "FragmentationResult",
    "SearchFragment",
    "fragment_corpus",
    "fragment_module",
    "verify_fragmentation",
]
SEARCH_FRAGMENTS_SCHEMA = "search_fragments/1"


class FragmentReason(str, Enum):
    """Причины; UNKNOWN зарезервирован, не означает успешный разбор."""
    UNKNOWN = "unknown"
    INVALID_DECLARATION = "invalid_declaration"
    NESTED_DECLARATION = "nested_declaration"
    UNEXPECTED_END = "unexpected_end"
    MISMATCHED_END = "mismatched_end"
    INVALID_END = "invalid_end"
    UNCLOSED_PROCEDURE = "unclosed_procedure"
    UNCLOSED_STRING = "unclosed_string"
    INVALID_STRING_CONTINUATION = "invalid_string_continuation"
    DUPLICATE_PROCEDURE = "duplicate_procedure"


@dataclass(frozen=True)
class FragmentParameters:
    """Бюджет исходного тела; перекрытие — верхняя граница в строках."""
    max_chars: int = 2000
    overlap_lines: int = 3

    def __post_init__(self) -> None:
        for name, value, minimum in (
            ("max_chars", self.max_chars, 1),
            ("overlap_lines", self.overlap_lines, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be int")
            if value < minimum:
                raise ValueError(f"{name} must be >= {minimum}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SEARCH_FRAGMENTS_SCHEMA,
            "max_chars": self.max_chars,
            "overlap_lines": self.overlap_lines,
            "normalization": "leading-bom-crlf-cr-to-lf/1",
            "lines": "split-lf-keep-terminator-no-phantom/1",
            "boundaries": "ru-en-line-start-async-lexical/1",
            "attachment": "adjacent-comment-annotation-no-blank/1",
            "windows": "greedy-whole-lines-progress-oversized-single/1",
            "fallback": "whole-module-lexical-error-first-then-boundary/1",
            "duplicates": "nfc-casefold-all-occurrences-line-id/1",
            "identifier_split": "unicode-case-transition-nfc-casefold/1",
            "projection": "artifact-header-plus-normalized-body/1",
            "owner_cards": "unchanged-no-bsl/1",
            "truncation": "none/1",
        }

    @property
    def representation_version(self) -> str:
        return _hash(to_canonical_json(self.to_dict()))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("source text must be str")
    return text.removeprefix(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")


def _lines(text: str) -> list[str]:
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _search_body(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    out: list[str] = []
    for i, char in enumerate(text):
        previous = text[i - 1] if i else ""
        following = text[i + 1] if i + 1 < len(text) else ""
        if char.isupper() and (
            previous.islower() or previous.isdigit()
            or (previous.isupper() and following.islower())
        ):
            out.append(" ")
        out.append(char)
    return "".join(out).casefold()


def _project(document: SearchDocument, procedure: str | None) -> str:
    artifact = document.artifact
    if artifact.artifact_kind == "owner":
        return document.text
    header = [artifact.artifact_kind.upper(), f"artifact: {artifact.artifact_id}"]
    if artifact.module_kind is not None:
        header.append(f"kind: {artifact.module_kind}")
    if artifact.owner is not None:
        owner = artifact.owner
        header.extend([
            f"owner_kind: {owner.owner_kind}",
            f"metadata_type: {owner.metadata_type or ''}",
            f"owner: {owner.owner_name or ''}",
        ])
    if artifact.form_key is not None:
        header.append("form: " + "/".join(artifact.form_key))
    if procedure is not None:
        header.append(f"procedure: {procedure}")
    if document.span is not None:
        header.append(f"lines: {document.span.start_line}-{document.span.end_line}")
    return _search_body("\n".join(header)) + "\nBSL\n" + _search_body(document.text)


FragmentKind = Literal["procedure", "window", "fallback", "owner_card"]


@dataclass(frozen=True)
class SearchFragment:
    """document.text — исходный срез; search_text — отдельный вход поиска."""
    document: SearchDocument
    search_text: str
    kind: FragmentKind
    procedure: str | None = None
    reasons: tuple[FragmentReason, ...] = ()
    oversized_line: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.document, SearchDocument):
            raise TypeError("document must be SearchDocument")
        if not isinstance(self.search_text, str):
            raise TypeError("search_text must be str")
        if self.kind not in ("procedure", "window", "fallback", "owner_card"):
            raise ValueError("unknown fragment kind")
        if self.procedure is not None and not self.procedure.isidentifier():
            raise ValueError("procedure must be an identifier")
        reasons = tuple(self.reasons)
        if any(not isinstance(r, FragmentReason) for r in reasons):
            raise TypeError("reasons must contain FragmentReason")
        object.__setattr__(self, "reasons", reasons)

    @property
    def fallback(self) -> bool:
        return self.kind == "fallback"

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "document": self.document.to_dict(include_text=include_text),
            "kind": self.kind,
            "procedure": self.procedure,
            "fallback": self.fallback,
            "reasons": [r.value for r in self.reasons],
            "oversized_line": self.oversized_line,
            "search_chars": len(self.search_text),
            "search_sha256": _hash(self.search_text),
        }
        if include_text:
            data["search_text"] = self.search_text
        return data


@dataclass(frozen=True)
class FragmentationItem:
    """Баланс строк нормализованного источника, без самого текста."""
    artifact: ArtifactRef
    source_chars: int
    source_sha256: str
    total_lines: int
    covered_lines: int
    repeated_lines: int
    omitted_blank_lines: tuple[int, ...]
    reasons: tuple[FragmentReason, ...] = ()
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "source_chars": self.source_chars,
            "source_sha256": self.source_sha256,
            "total_lines": self.total_lines,
            "covered_lines": self.covered_lines,
            "repeated_lines": self.repeated_lines,
            "omitted_blank_lines": list(self.omitted_blank_lines),
            "reasons": [r.value for r in self.reasons],
            "fallback": self.fallback,
            "truncated": False,
        }


@dataclass(frozen=True)
class FragmentationResult:
    """Новый набор; CorpusReport и OwnerLink #323 сохраняются без изменения."""
    parameters: FragmentParameters
    fragments: tuple[SearchFragment, ...]
    items: tuple[FragmentationItem, ...]
    corpus_report: CorpusReport | None = None
    owner_links: tuple[OwnerLink, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.parameters, FragmentParameters):
            raise TypeError("parameters must be FragmentParameters")
        fragments = tuple(self.fragments)
        SearchDocumentSet(tuple(f.document for f in fragments))
        object.__setattr__(self, "fragments", tuple(sorted(
            fragments, key=lambda f: f.document.sort_key())))
        items = tuple(sorted(self.items, key=lambda i: i.artifact.sort_key()))
        if len({i.artifact.identity for i in items}) != len(items):
            raise ValueError("duplicate fragmentation source")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "owner_links", tuple(self.owner_links))

    @property
    def documents(self) -> SearchDocumentSet:
        return SearchDocumentSet(tuple(f.document for f in self.fragments))

    @property
    def representation_version(self) -> str:
        return self.parameters.representation_version

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "schema": SEARCH_FRAGMENTS_SCHEMA,
            "representation_version": self.representation_version,
            "parameters": self.parameters.to_dict(),
            "fragments": [f.to_dict(include_text=include_text) for f in self.fragments],
            "items": [i.to_dict() for i in self.items],
            "corpus_report": None if self.corpus_report is None else self.corpus_report.to_dict(),
            "owner_links": [link.to_dict() for link in self.owner_links],
        }

    def to_json(self, *, include_text: bool = False) -> str:
        return to_canonical_json(self.to_dict(include_text=include_text))


_START = re.compile(r"(?i)^\s*(?:(?:Асинх|Async)\s+)?(Процедура|Функция|Procedure|Function)\b")
_DECL = re.compile(r"(?i)^\s*(?:(?:Асинх|Async)\s+)?(Процедура|Функция|Procedure|Function)\s+([^\s(]+)\s*\(")
_END = re.compile(r"(?i)^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b")
_END_FULL = re.compile(r"(?i)^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\s*;?\s*$")
_PROC = frozenset({"процедура", "procedure", "конецпроцедуры", "endprocedure"})


def _masked(lines: list[str]) -> tuple[list[str], FragmentReason | None]:
    result: list[str] = []
    quoted = False
    for line in lines:
        start = 0
        mask = list(line)
        if quoted:
            prefix = len(line) - len(line.lstrip(" \t"))
            if prefix >= len(line) or line[prefix] != "|":
                return result, FragmentReason.INVALID_STRING_CONTINUATION
            mask[:prefix + 1] = ["@"] * (prefix + 1)
            start = prefix + 1
        i = start
        while i < len(line):
            char = line[i]
            if quoted:
                mask[i] = "@" if char != "\n" else "\n"
                if char == '"':
                    if i + 1 < len(line) and line[i + 1] == '"':
                        mask[i + 1] = "@"
                        i += 2
                        continue
                    quoted = False
            elif line.startswith("//", i):
                mask[i:] = ["\n" if c == "\n" else " " for c in line[i:]]
                break
            elif char == '"':
                quoted = True
                mask[i] = "@"
            i += 1
        result.append("".join(mask))
    return result, FragmentReason.UNCLOSED_STRING if quoted else None


@dataclass(frozen=True)
class _Region:
    start: int
    end: int
    procedure: str | None = None


def _regions(lines: list[str]) -> tuple[list[_Region], FragmentReason | None]:
    masked, reason = _masked(lines)
    if reason is not None:
        return [], reason
    regions: list[_Region] = []
    active: tuple[int, str, bool] | None = None
    outside = 0
    for i, line in enumerate(masked):
        opening = _START.match(line)
        closing = _END.match(line)
        if opening:
            if active is not None:
                return [], FragmentReason.NESTED_DECLARATION
            declaration = _DECL.match(line)
            if declaration is None or not declaration[2].isidentifier():
                return [], FragmentReason.INVALID_DECLARATION
            start = i
            while start > outside:
                preceding = lines[start - 1].strip()
                if not preceding or not preceding.startswith(("//", "&")):
                    break
                start -= 1
            if start > outside:
                regions.append(_Region(outside, start))
            active = (start, declaration[2], declaration[1].casefold() in _PROC)
        elif closing:
            if active is None:
                return [], FragmentReason.UNEXPECTED_END
            if not _END_FULL.fullmatch(line):
                return [], FragmentReason.INVALID_END
            if (closing[1].casefold() in _PROC) != active[2]:
                return [], FragmentReason.MISMATCHED_END
            regions.append(_Region(active[0], i + 1, active[1]))
            outside = i + 1
            active = None
    if active is not None:
        return [], FragmentReason.UNCLOSED_PROCEDURE
    if outside < len(lines):
        regions.append(_Region(outside, len(lines)))
    return regions, None


def _windows(lines: list[str], start: int, end: int, parameters: FragmentParameters) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    pos = start
    frontier = start
    while pos < end:
        stop, size = pos, 0
        while stop < end and (stop == pos or size + len(lines[stop]) <= parameters.max_chars):
            size += len(lines[stop])
            stop += 1
        if stop <= frontier:
            pos = frontier
            continue
        windows.append((pos, stop))
        frontier = stop
        if stop == end:
            break
        pos = max(pos + 1, stop - parameters.overlap_lines)
    return windows


def _coverage(lines: list[str], fragments: list[SearchFragment]) -> Counter[int]:
    covered: Counter[int] = Counter()
    for fragment in fragments:
        span = fragment.document.span
        if span is None and fragment.kind == "owner_card":
            covered.update(range(1, len(lines) + 1))
        elif span is not None:
            covered.update(range(span.start_line, span.end_line + 1))
    return covered


def fragment_module(artifact: ArtifactRef, text: str, *, parameters: FragmentParameters | None = None) -> FragmentationResult:
    """Чистая функция. Пустой текст даёт отчёт, не фиктивный документ."""
    if not isinstance(artifact, ArtifactRef):
        raise TypeError("artifact must be ArtifactRef")
    if artifact.artifact_kind == "owner":
        raise ValueError("owner cards are handled by fragment_corpus")
    parameters = parameters if parameters is not None else FragmentParameters()
    if not isinstance(parameters, FragmentParameters):
        raise TypeError("parameters must be FragmentParameters")
    text = _normalize(text)
    lines = _lines(text)
    regions, reason = _regions(lines)
    if reason is not None:
        regions = [_Region(0, len(lines))]
    counts = Counter(identity_key(r.procedure) for r in regions if r.procedure is not None)
    fragments: list[SearchFragment] = []
    all_reasons = [reason] if reason is not None else []
    for region in regions:
        duplicate = region.procedure is not None and counts[identity_key(region.procedure)] > 1
        reasons = (reason,) if reason is not None else ((FragmentReason.DUPLICATE_PROCEDURE,) if duplicate else ())
        if duplicate and FragmentReason.DUPLICATE_PROCEDURE not in all_reasons:
            all_reasons.append(FragmentReason.DUPLICATE_PROCEDURE)
        body = "".join(lines[region.start:region.end])
        whole = region.procedure is not None and not duplicate and len(body) <= parameters.max_chars
        windows = [(region.start, region.end)] if whole else _windows(lines, region.start, region.end, parameters)
        for start, end in windows:
            chunk = "".join(lines[start:end])
            if not chunk.strip():
                continue
            document = SearchDocument(artifact, chunk,
                procedure=region.procedure if whole else None,
                span=TextSpan(start + 1, end))
            kind: FragmentKind = "fallback" if reason is not None else ("procedure" if whole else "window")
            fragments.append(SearchFragment(document, _project(document, region.procedure), kind,
                region.procedure, reasons, end == start + 1 and len(chunk) > parameters.max_chars))
    covered = _coverage(lines, fragments)
    item = FragmentationItem(artifact, len(text), _hash(text), len(lines), len(covered),
        len([n for n in covered.values() if n > 1]),
        tuple(i for i in range(1, len(lines) + 1) if i not in covered),
        tuple(all_reasons), reason is not None)
    return FragmentationResult(parameters, tuple(fragments), (item,))


def fragment_corpus(corpus: SearchCorpus, *, parameters: FragmentParameters | None = None) -> FragmentationResult:
    """Поверх #323: новые сканеры, чтение файлов и мутация отсутствуют."""
    if not isinstance(corpus, SearchCorpus):
        raise TypeError("corpus must be SearchCorpus")
    parameters = parameters if parameters is not None else FragmentParameters()
    if not isinstance(parameters, FragmentParameters):
        raise TypeError("parameters must be FragmentParameters")
    fragments: list[SearchFragment] = []
    items: list[FragmentationItem] = []
    for document in corpus.documents.documents:
        if document.span is not None or document.procedure is not None:
            raise ValueError("corpus must contain whole artifacts only")
        if document.artifact.artifact_kind == "owner":
            fragments.append(SearchFragment(document, document.text, "owner_card"))
            total = len(_lines(document.text))
            items.append(FragmentationItem(document.artifact, len(document.text), document.text_sha256,
                total, total, 0, ()))
        else:
            result = fragment_module(document.artifact, document.text, parameters=parameters)
            fragments.extend(result.fragments)
            items.extend(result.items)
    return FragmentationResult(parameters, tuple(fragments), tuple(items), corpus.report, corpus.owner_links)


def verify_fragmentation(result: FragmentationResult, sources: Mapping[str, str]) -> None:
    """Проверка срезов, метаданных и баланса. ValueError на расхождение.

    sources: artifact_id -> исходный текст (BOM/CRLF допустимы). Не доказывает
    семантическую корректность процедур и не заменяет анализатор BSL.
    """
    if not isinstance(result, FragmentationResult):
        raise TypeError("result must be FragmentationResult")
    if set(sources) != {i.artifact.artifact_id for i in result.items}:
        raise ValueError("source artifacts do not match result")
    by_artifact: dict[str, list[SearchFragment]] = {}
    for fragment in result.fragments:
        by_artifact.setdefault(fragment.document.artifact.artifact_id, []).append(fragment)
    for item in result.items:
        text = sources[item.artifact.artifact_id]
        if item.artifact.artifact_kind != "owner":
            text = _normalize(text)
        lines = _lines(text)
        fragments = by_artifact.pop(item.artifact.artifact_id, [])
        if _hash(text) != item.source_sha256 or len(text) != item.source_chars or len(lines) != item.total_lines:
            raise ValueError("source fingerprint mismatch")
        reasons: set[FragmentReason] = set()
        for fragment in fragments:
            document = fragment.document
            if document.artifact != item.artifact:
                raise ValueError("fragment artifact mismatch")
            span = document.span
            if fragment.kind == "owner_card":
                if item.artifact.artifact_kind != "owner" or span is not None or document.procedure is not None:
                    raise ValueError("invalid owner card coordinates")
                if fragment.procedure is not None or fragment.reasons or fragment.oversized_line:
                    raise ValueError("invalid owner card metadata")
                expected = text
            else:
                if item.artifact.artifact_kind == "owner" or span is None or span.end_line > len(lines):
                    raise ValueError("invalid source coordinates")
                expected = "".join(lines[span.start_line - 1:span.end_line])
                oversized = span.start_line == span.end_line and len(expected) > result.parameters.max_chars
                if fragment.oversized_line != oversized or (len(expected) > result.parameters.max_chars and not oversized):
                    raise ValueError("invalid window budget")
                if fragment.kind == "procedure":
                    if document.procedure is None or document.procedure != fragment.procedure or fragment.reasons:
                        raise ValueError("invalid procedure metadata")
                elif document.procedure is not None:
                    raise ValueError("window must use line id")
                if fragment.fallback != item.fallback:
                    raise ValueError("fallback marker mismatch")
                if fragment.fallback and (fragment.procedure is not None or not fragment.reasons):
                    raise ValueError("invalid fallback metadata")
            reasons.update(fragment.reasons)
            if document.text != expected:
                raise ValueError("fragment text mismatch")
            if fragment.search_text != _project(document, fragment.procedure):
                raise ValueError("search projection mismatch")
        if reasons != set(item.reasons):
            raise ValueError("reason report mismatch")
        covered = _coverage(lines, fragments)
        omitted = tuple(i for i in range(1, len(lines) + 1) if i not in covered)
        if any(lines[i - 1].strip() for i in omitted):
            raise ValueError("nonblank source text lost")
        if (len(covered), len([n for n in covered.values() if n > 1]), omitted) != (
            item.covered_lines, item.repeated_lines, item.omitted_blank_lines):
            raise ValueError("coverage report mismatch")
    if by_artifact:
        raise ValueError("fragment without source")
