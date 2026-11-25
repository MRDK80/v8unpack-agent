"""Replace a query in the binary template of a normalized external report."""
from __future__ import annotations

import codecs
import os
from pathlib import Path
from struct import Struct
from tempfile import TemporaryDirectory
from xml.parsers import expat
from xml.sax.saxutils import escape

_HEADER = Struct("<IIQQ")
_SCHEMA = "http://v8.1c.ru/8.1/data-composition-system/schema"
_DATASET = f"{_SCHEMA} dataSet"
_NAME = f"{_SCHEMA} name"
_QUERY = f"{_SCHEMA} query"


def _query_span(xml: bytes, dataset: str) -> tuple[int, int]:
    parser = expat.ParserCreate(namespace_separator=" ")
    stack: list[str] = []
    spans: list[tuple[int, int]] = []
    names: list[str] = []
    query: tuple[int, int] | None = None
    start_index: int | None = None
    matching_count = 0

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal start_index, query
        if not stack and tag != "SchemaFile":
            raise ValueError("expected a SchemaFile root")
        if len(stack) == 3 and stack[-1] == _DATASET and tag == _QUERY:
            index = parser.CurrentByteIndex
            if attrs or query is not None or not xml.startswith(b"<query>", index):
                raise ValueError("unsupported SKD query element")
            start_index = index + len(b"<query>")
        elif start_index is not None:
            raise ValueError("nested SKD query content is unsupported")
        if stack == ["SchemaFile", f"{_SCHEMA} dataCompositionSchema"] and tag == _DATASET:
            names.clear()
            query = None
        stack.append(tag)

    def end(tag: str) -> None:
        nonlocal start_index, query, matching_count
        if start_index is not None and tag == _QUERY:
            query = start_index, parser.CurrentByteIndex
            start_index = None
        if len(stack) == 3 and tag == _DATASET and "".join(names) == dataset:
            matching_count += 1
            if query is not None:
                spans.append(query)
        stack.pop()

    def characters(value: str) -> None:
        if len(stack) == 4 and stack[-2:] == [_DATASET, _NAME]:
            names.append(value)

    def reject_doctype(*args: object) -> None:
        raise ValueError("DOCTYPE in an SKD template is unsupported")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject_doctype
    try:
        parser.Parse(xml, True)
    except expat.ExpatError as exc:
        raise ValueError("invalid SKD schema XML") from exc
    if matching_count != 1 or len(spans) != 1:
        raise ValueError("expected one query for the specified SKD dataset")
    return spans[0]


def replace_skd_query(template: Path, destination: Path, *, dataset: str, query: str) -> None:
    """Write a new Template.bin preserving all bytes outside the query and schema length."""
    if not dataset.strip() or not query.strip():
        raise ValueError("expected a dataset name and query")
    raw = Path(template).read_bytes()
    if len(raw) < _HEADER.size:
        raise ValueError("invalid SKD template header")
    version, sections, schema_size, settings_size = _HEADER.unpack_from(raw)
    end_schema = _HEADER.size + schema_size
    end_settings = end_schema + settings_size
    if (
        (version, sections) != (0, 1) or not _HEADER.size < end_schema < end_settings < len(raw)
        or any(not raw.startswith(codecs.BOM_UTF8, offset) for offset in (_HEADER.size, end_schema, end_settings))
    ):
        raise ValueError("unsupported SKD template layout")
    schema = raw[_HEADER.size:end_schema]
    start, end = _query_span(schema, dataset)
    updated = schema[:start] + escape(query).encode("utf-8") + schema[end:]
    _query_span(updated, dataset)
    header = _HEADER.pack(version, sections, len(updated), settings_size)
    destination = Path(destination)
    with TemporaryDirectory(dir=destination.parent) as work:
        staged = Path(work) / "Template.bin"
        staged.write_bytes(header + updated + raw[end_schema:])
        os.link(staged, destination)
