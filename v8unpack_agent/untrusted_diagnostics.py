"""Диагностика символов Unicode категории Cf и файлового BOM (#458).

Реализует решение 4 ADR ``docs/research/untrusted_content_contract_issue457.md``.
Модуль только обнаруживает и описывает символы; канонический текст, файлы
выгрузки и сохраняемые данные не меняются. Подключение диагностики к выводам
нового режима выполняют T4–T6 эпика #455.

Группы задаются фиксированной таблицей, а не свойствами версии Unicode, чтобы
на разных версиях Python совпадали группы обнаруженных символов. Версия
базы Unicode интерпретатора выводится отдельным полем ``unidata_version``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "FILE_BOM_BYTES",
    "GROUPS",
    "CfGroup",
    "CfOccurrence",
    "UntrustedDiagnostics",
    "cf_group",
    "detect_untrusted_chars",
]

FILE_BOM_BYTES = b"\xef\xbb\xbf"
_BOM_CHAR = "\ufeff"

CfGroup = Literal["bidi", "zero_width", "soft_hyphen", "other_cf"]
GROUPS: tuple[CfGroup, ...] = ("bidi", "zero_width", "soft_hyphen", "other_cf")

_BIDI = frozenset(
    {0x061C, 0x200E, 0x200F}
    | set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
)
_ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})
_SOFT_HYPHEN = frozenset({0x00AD})


def cf_group(char: str) -> CfGroup:
    """Группа символа категории Cf по фиксированной таблице ADR #457."""
    code = ord(char)
    if code in _BIDI:
        return "bidi"
    if code in _ZERO_WIDTH:
        return "zero_width"
    if code in _SOFT_HYPHEN:
        return "soft_hyphen"
    return "other_cf"


@dataclass(frozen=True)
class CfOccurrence:
    """Одно вхождение символа Cf: код, строка и столбец (с 1), группа."""

    codepoint: str
    line: int
    column: int
    group: CfGroup

    def to_dict(self) -> dict[str, object]:
        return {
            "codepoint": self.codepoint,
            "line": self.line,
            "column": self.column,
            "group": self.group,
        }


@dataclass(frozen=True)
class UntrustedDiagnostics:
    """Диагностика текста: файловый BOM, вхождения Cf, счётчики по группам.

    ``file_bom`` — ``None``, если исходные байты не переданы и признак
    неизвестен.
    """

    file_bom: bool | None
    occurrences: tuple[CfOccurrence, ...]
    unidata_version: str

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = dict.fromkeys(GROUPS, 0)
        for item in self.occurrences:
            result[item.group] += 1
        return result

    @property
    def total(self) -> int:
        return len(self.occurrences)

    def to_dict(self) -> dict[str, object]:
        """Детерминированное представление для сериализации."""
        return {
            "file_bom": self.file_bom,
            "cf": [item.to_dict() for item in self.occurrences],
            "counts": self.counts,
            "total": self.total,
            "unidata_version": self.unidata_version,
        }


def detect_untrusted_chars(text: str, *, raw: bytes | None = None) -> UntrustedDiagnostics:
    """Найти символы категории Cf в ``text`` и файловый BOM в ``raw``.

    Строки разделяются только ``\\n``; столбец считается в кодовых точках.
    Если ``raw`` начинается с UTF-8 BOM и ``text`` начинается с U+FEFF, этот
    первый символ отражён признаком ``file_bom`` и в Cf-вхождения повторно не
    включается. Текст не изменяется.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if raw is not None and not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes or None")
    file_bom = None if raw is None else bytes(raw).startswith(FILE_BOM_BYTES)
    skip_first = bool(file_bom) and text.startswith(_BOM_CHAR)
    occurrences: list[CfOccurrence] = []
    line = 1
    column = 0
    for index, char in enumerate(text):
        if char == "\n":
            line += 1
            column = 0
            continue
        column += 1
        if index == 0 and skip_first:
            continue
        if unicodedata.category(char) == "Cf":
            occurrences.append(
                CfOccurrence(
                    codepoint=f"U+{ord(char):04X}",
                    line=line,
                    column=column,
                    group=cf_group(char),
                )
            )
    return UntrustedDiagnostics(
        file_bom=file_bom,
        occurrences=tuple(occurrences),
        unidata_version=unicodedata.unidata_version,
    )
