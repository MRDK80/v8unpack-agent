"""LLM-проекция BSL-модуля по записи ``ModuleEntry`` (#345).

Чистая библиотечная функция: файлы не читаются, LLM-провайдер не
вызывается, BSL-текст передаёт вызывающий код. Контракт описан в
``docs/modules.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from v8unpack_agent.modules import ModuleEntry, ModuleReadStatus
from v8unpack_agent.untrusted_diagnostics import detect_untrusted_chars, diag_line
from v8unpack_agent.untrusted_transport import encode_block

Representation = Literal["legacy", "framed"]

__all__ = [
    "ModuleProjection",
    "Representation",
    "to_llm_module_fragment",
]

_NO_LIMIT = -1
_LF = chr(10)
_CR = chr(13)
_CRLF = _CR + _LF
_BOM = chr(0xFEFF)


@dataclass(frozen=True)
class ModuleProjection:
    """Результат проекции модуля.

    ``text`` пуст для статусов без текста и при бюджете, в который не
    помещается даже заголовок с маркером усечения. ``truncated`` — факт
    усечения. ``original_chars`` — длина тела после нормализации переводов
    строк; для статусов без текста равна 0.
    """

    status: ModuleReadStatus
    text: str
    truncated: bool
    original_chars: int
    representation: Representation = "legacy"
    diagnostics: dict[str, Any] | None = None
    """Только в режиме ``framed`` (#460): диагностика Cf/BOM по ADR #457."""


def _normalize(text: str) -> str:
    if text.startswith(_BOM):
        text = text[1:]
    return text.replace(_CRLF, _LF).replace(_CR, _LF)


def _header(entry: ModuleEntry) -> str:
    lines = [
        "MODULE",
        f"kind: {entry.module_kind}",
        f"owner_kind: {entry.owner_kind}",
    ]
    if entry.metadata_type is not None:
        lines.append(f"metadata_type: {entry.metadata_type}")
    if entry.owner_name is not None:
        lines.append(f"owner: {entry.owner_name}")
    lines.append(f"path: {entry.relative_path}")
    lines.append("BSL")
    return _LF.join(lines) + _LF


def _framed_header(entry: ModuleEntry) -> tuple[str, int]:
    lines = [
        "MODULE",
        f"kind: {entry.module_kind}",
        f"owner_kind: {entry.owner_kind}",
    ]
    if entry.metadata_type is not None:
        lines.append(f"metadata_type: {entry.metadata_type}")
    text = _LF.join(lines) + _LF
    number = 1
    if entry.owner_name is not None:
        text += "owner:" + _LF + encode_block(entry.owner_name, n=number, kind="name")
        number += 1
    text += "path:" + _LF + encode_block(entry.relative_path, n=number, kind="path")
    return text + "BSL" + _LF, number + 1


def _framed(
    entry: ModuleEntry, bsl_text: str, max_chars: int, raw: bytes | None
) -> ModuleProjection:
    header, number = _framed_header(entry)
    diagnostics = detect_untrusted_chars(bsl_text, raw=raw)
    diag = diag_line(diagnostics)
    pieces = bsl_text.split(_LF)
    lines = [p + _LF for p in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])
    total = len(lines)

    def render(count: int) -> str:
        body = "".join(lines[:count])
        block = encode_block(
            body, n=number, kind="bsl", total_lines=total, diag=diag
        )
        return header + block

    full = render(total)

    def result(text: str, truncated: bool) -> ModuleProjection:
        return ModuleProjection(
            status=entry.read_status,
            text=text,
            truncated=truncated,
            original_chars=len(bsl_text),
            representation="framed",
            diagnostics=diagnostics.to_dict(),
        )

    if max_chars == _NO_LIMIT or len(full) <= max_chars:
        return result(full, False)
    for count in range(total - 1, -1, -1):
        text = render(count)
        if len(text) <= max_chars:
            return result(text, True)
    return result("", True)


def _marker(included: int, total: int) -> str:
    return _LF + f"[TRUNCATED: {included} of {total} chars]"


def to_llm_module_fragment(
    entry: ModuleEntry,
    bsl_text: str | None,
    *,
    max_chars: int = _NO_LIMIT,
    representation: Representation = "legacy",
    bsl_bytes: bytes | None = None,
) -> ModuleProjection:
    """Построить детерминированный текст модуля для LLM.

    Для статусов ``empty``, ``whitespace_only``, ``missing`` и
    ``read_error`` текст пуст, ``bsl_text`` игнорируется. Для ``ok``
    результат — заголовок (вид модуля, владелец, относительный путь) и
    тело. ``max_chars=-1`` отключает лимит; иначе ``len(text) <= max_chars``,
    а усечение отражено в ``truncated`` и маркере в конце текста.

    ``representation="framed"`` (#460, ADR #457): имя владельца, путь и тело —
    обратимые блоки недоверенного текста; тело берётся без нормализации,
    усечение — по целым строкам с ``truncated=1``/``omitted`` в заголовке
    блока, маркер ``[TRUNCATED: …]`` не выводится. По умолчанию ``legacy``.
    ``bsl_bytes`` — исходные байты файла для признака ``file_bom``; в
    прежнем режиме не используется.
    """
    if representation not in ("legacy", "framed"):
        raise ValueError("representation must be 'legacy' or 'framed'")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be int")
    if max_chars < _NO_LIMIT:
        raise ValueError("max_chars must be -1 or non-negative")
    if not isinstance(entry, ModuleEntry):
        raise TypeError("entry must be ModuleEntry")
    status = entry.read_status
    if status != "ok":
        return ModuleProjection(
            status=status, text="", truncated=False, original_chars=0,
            representation=representation,
        )
    if bsl_text is None:
        raise ValueError("bsl_text is required for status 'ok'")
    if not isinstance(bsl_text, str):
        raise TypeError("bsl_text must be str")
    if representation == "framed":
        return _framed(entry, bsl_text, max_chars, bsl_bytes)
    body = _normalize(bsl_text)
    header = _header(entry)
    total = len(body)
    full = header + body
    if max_chars == _NO_LIMIT or len(full) <= max_chars:
        return ModuleProjection(
            status=status, text=full, truncated=False, original_chars=total
        )
    included = min(total - 1, max_chars - len(header))
    while included >= 0 and (
        len(header) + included + len(_marker(included, total)) > max_chars
    ):
        included -= 1
    text = ""
    if included >= 0:
        text = header + body[:included] + _marker(included, total)
    return ModuleProjection(
        status=status, text=text, truncated=True, original_chars=total
    )
