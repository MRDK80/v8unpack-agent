"""Транспортное представление недоверенного текста (решение 2 ADR #457).

Блок данных::

    <<<UNTRUSTED_BEGIN n=1 kind=text lines=1-5/5 chars=71 final_newline=1 truncated=0 sha256=<hex>>>>
    | строка данных
    <<<UNTRUSTED_END n=1>>>

Каждая строка данных начинается с ``| ``; служебные строки начинаются с
``<<<``. Внутри строки данных ``\\`` экранируется как ``\\\\``, символы
категорий Cf и Cc (кроме ``\\t``) — как ``\\u{XXXX}``. ``sha256``, ``chars`` и
диапазон строк относятся к каноническому включённому тексту. Модуль не
изменяет канонический текст и ничего не сохраняет.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "BLOCK_BEGIN",
    "BLOCK_DIAG",
    "BLOCK_END",
    "DATA_PREFIX",
    "DecodedBlock",
    "TransportError",
    "decode_block",
    "encode_block",
    "escape_line",
    "unescape_line",
]

BLOCK_BEGIN = "<<<UNTRUSTED_BEGIN"
BLOCK_END = "<<<UNTRUSTED_END"
BLOCK_DIAG = "<<<UNTRUSTED_DIAG"
DATA_PREFIX = "| "
_KINDS = frozenset({"bsl", "text", "name", "path"})
_HEADER_RE = re.compile(
    r"^<<<UNTRUSTED_BEGIN n=(?P<n>[1-9][0-9]*) kind=(?P<kind>[a-z]+)"
    r" lines=(?P<a>[0-9]+)-(?P<b>[0-9]+)/(?P<t>[0-9]+) chars=(?P<chars>[0-9]+)"
    r" final_newline=(?P<nl>[01]) truncated=(?P<tr>[01])"
    r"(?: omitted=(?P<c>[0-9]+)-(?P<d>[0-9]+))? sha256=(?P<sha>[0-9a-f]{64})>>>$"
)
_ESCAPE_RE = re.compile(r"\\(?:\\|u\{([0-9A-F]{4,6})\})")


class TransportError(ValueError):
    """Блок не соответствует контракту транспорта."""


def _needs_escape(char: str) -> bool:
    if char == "\t":
        return False
    return unicodedata.category(char) in ("Cf", "Cc")


def escape_line(line: str) -> str:
    """Экранировать одну строку без ``\\n``."""
    out: list[str] = []
    for char in line:
        if char == "\\":
            out.append("\\\\")
        elif _needs_escape(char):
            out.append(f"\\u{{{ord(char):04X}}}")
        else:
            out.append(char)
    return "".join(out)


def unescape_line(line: str) -> str:
    """Обратное к :func:`escape_line`; неизвестная последовательность — ошибка."""
    out: list[str] = []
    pos = 0
    while True:
        index = line.find("\\", pos)
        if index < 0:
            out.append(line[pos:])
            return "".join(out)
        out.append(line[pos:index])
        match = _ESCAPE_RE.match(line, index)
        if match is None:
            raise TransportError("unknown escape sequence")
        out.append("\\" if match.group(1) is None else chr(int(match.group(1), 16)))
        pos = match.end()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_block(
    text: str,
    *,
    n: int,
    kind: str,
    first_line: int = 1,
    total_lines: int | None = None,
    diag: str | None = None,
) -> str:
    """Закодировать канонический ``text`` как блок; результат оканчивается ``\\n``.

    ``first_line`` — номер первой строки ``text`` в исходном тексте;
    ``total_lines`` — число строк исходного текста. Если включённый диапазон
    заканчивается раньше, блок помечается ``truncated=1`` с опущенным
    диапазоном. Пустой ``text`` даёт блок без строк данных (``lines=0-0``).
    ``diag`` — содержимое служебной строки диагностики
    ``<<<UNTRUSTED_DIAG n=… <diag>>>>`` сразу после заголовка (решение 5 ADR).
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n must be positive int")
    if kind not in _KINDS:
        raise ValueError("unknown kind")
    if not isinstance(text, str):
        raise TypeError("text must be str")
    final_newline = text.endswith("\n")
    body = text[:-1] if final_newline else text
    lines = body.split("\n") if text else []
    count = len(lines)
    if count == 0:
        start = end = 0
    else:
        start = first_line
        end = first_line + count - 1
    total = end if total_lines is None else total_lines
    if total < end:
        raise ValueError("total_lines is less than the included range")
    truncated = total > end
    header = (
        f"{BLOCK_BEGIN} n={n} kind={kind} lines={start}-{end}/{total}"
        f" chars={len(text)} final_newline={int(final_newline)}"
        f" truncated={int(truncated)}"
    )
    if truncated:
        header += f" omitted={end + 1}-{total}"
    header += f" sha256={_sha(text)}>>>"
    parts = [header]
    if diag is not None:
        if "\n" in diag or ">>>" in diag:
            raise ValueError("diag must be a single service line")
        parts.append(f"{BLOCK_DIAG} n={n} {diag}>>>")
    parts.extend(DATA_PREFIX + escape_line(line) for line in lines)
    parts.append(f"{BLOCK_END} n={n}>>>")
    return "\n".join(parts) + "\n"


@dataclass(frozen=True)
class DecodedBlock:
    """Результат декодирования блока: канонический включённый текст и заголовок."""

    n: int
    kind: str
    text: str
    first_line: int
    last_line: int
    total_lines: int
    truncated: bool
    omitted: tuple[int, int] | None


def decode_block(block: str) -> DecodedBlock:
    """Декодировать один блок и проверить ``sha256``, ``chars`` и диапазоны."""
    rows = block.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    if len(rows) < 2:
        raise TransportError("block is too short")
    match = _HEADER_RE.match(rows[0])
    if match is None:
        raise TransportError("bad header")
    n = int(match.group("n"))
    if rows[-1] != f"{BLOCK_END} n={n}>>>":
        raise TransportError("bad end marker")
    kind = match.group("kind")
    if kind not in _KINDS:
        raise TransportError("unknown kind")
    data = rows[1:-1]
    if data and data[0].startswith(f"{BLOCK_DIAG} n={n} ") and data[0].endswith(">>>"):
        data = data[1:]
    if any(not row.startswith(DATA_PREFIX) for row in data):
        raise TransportError("data line without prefix")
    lines = [unescape_line(row[len(DATA_PREFIX):]) for row in data]
    start, end, total = (int(match.group(k)) for k in ("a", "b", "t"))
    expected = 0 if start == end == 0 else end - start + 1
    if len(lines) != expected:
        raise TransportError("line count mismatch")
    text = "\n".join(lines)
    if match.group("nl") == "1":
        text += "\n"
    if len(text) != int(match.group("chars")) or _sha(text) != match.group("sha"):
        raise TransportError("checksum mismatch")
    truncated = match.group("tr") == "1"
    omitted = None
    if match.group("c") is not None:
        omitted = (int(match.group("c")), int(match.group("d")))
    if truncated != (omitted is not None) or (truncated and omitted != (end + 1, total)):
        raise TransportError("truncation fields mismatch")
    if not truncated and total != end:
        raise TransportError("range mismatch")
    return DecodedBlock(n, kind, text, start, end, total, truncated, omitted)
