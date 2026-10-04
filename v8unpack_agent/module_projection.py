"""LLM-проекция BSL-модуля по записи ``ModuleEntry`` (#345).

Чистая библиотечная функция: файлы не читаются, LLM-провайдер не
вызывается, BSL-текст передаёт вызывающий код. Контракт описан в
``docs/modules.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from v8unpack_agent.modules import ModuleEntry, ModuleReadStatus

__all__ = [
    "ModuleProjection",
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


def _marker(included: int, total: int) -> str:
    return _LF + f"[TRUNCATED: {included} of {total} chars]"


def to_llm_module_fragment(
    entry: ModuleEntry,
    bsl_text: str | None,
    *,
    max_chars: int = _NO_LIMIT,
) -> ModuleProjection:
    """Построить детерминированный текст модуля для LLM.

    Для статусов ``empty``, ``whitespace_only``, ``missing`` и
    ``read_error`` текст пуст, ``bsl_text`` игнорируется. Для ``ok``
    результат — заголовок (вид модуля, владелец, относительный путь) и
    тело. ``max_chars=-1`` отключает лимит; иначе ``len(text) <= max_chars``,
    а усечение отражено в ``truncated`` и маркере в конце текста.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be int")
    if max_chars < _NO_LIMIT:
        raise ValueError("max_chars must be -1 or non-negative")
    if not isinstance(entry, ModuleEntry):
        raise TypeError("entry must be ModuleEntry")
    status = entry.read_status
    if status != "ok":
        return ModuleProjection(
            status=status, text="", truncated=False, original_chars=0
        )
    if bsl_text is None:
        raise ValueError("bsl_text is required for status 'ok'")
    if not isinstance(bsl_text, str):
        raise TypeError("bsl_text must be str")
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
