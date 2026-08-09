"""Обезличенные ссылки на пути файловой системы (issue #123).

Предупреждения парсера регулярно публикуются: в отчётах, логах CI, теле
issue и PR, промптах к модели. Абсолютный путь несёт имя пользователя и
структуру локальной ФС, поэтому в текст предупреждения он попадать не
должен. При этом предупреждение обязано оставаться полезным: по нему
нужно понять, о какой форме речь.

Модуль решает эту задачу функциями :func:`safe_path_ref` и
:func:`safe_error_text`. Он намеренно не зависит ни от чего внутри пакета,
чтобы его могли использовать ``elem_parser`` и ``object_decoder`` без
циклических импортов.

Почему не :mod:`pathlib`
------------------------

На POSIX ``Path`` не считает ``\\`` разделителем, поэтому строка вида
``C:\\dump\\Catalog\\Объект\\CatalogForm\\Форма`` осталась бы единым
сегментом и утечка сохранилась бы. Разбор идёт регулярным выражением по
обоим разделителям, поэтому результат не зависит от ОС, на которой
запущен код или тесты.

Гарантии
--------

* результат ``safe_path_ref`` никогда не является абсолютным путём;
* у абсолютного входа сохраняются последние значимые сегменты — для
  выгрузки 1С это «тип / объект / контейнер / форма»;
* относительный путь не искажается, пока он не длиннее хвоста;
* в выводе всегда posix-разделитель ``/``;
* ``safe_error_text`` сохраняет смысл ошибки, но заменяет переданные
  связанные пути, включая варианты с ``/`` и ``\\``;
* функции детерминированы и не обращаются к файловой системе.
"""

from __future__ import annotations

import re

__all__ = [
    "SAFE_TAIL_SEGMENTS",
    "TRUNCATION_MARKER",
    "UNKNOWN_REF",
    "safe_error_text",
    "safe_path_ref",
]

#: Сколько хвостовых сегментов сохраняется по умолчанию.
#: Для выгрузки 1С это тип объекта / объект / контейнер форм / форма.
SAFE_TAIL_SEGMENTS = 4

#: Признак того, что начало пути отброшено осознанно.
TRUNCATION_MARKER = "..."

#: Замена для пустого или отсутствующего пути.
UNKNOWN_REF = "<путь не указан>"

_SEPARATORS_RE = re.compile(r"[\\/]+")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
_ABSOLUTE_RE = re.compile(r"^(?:[\\/]|[A-Za-z]:[\\/])")


def safe_path_ref(value: object, tail: int = SAFE_TAIL_SEGMENTS) -> str:
    """Вернуть обезличенную ссылку на путь для текста предупреждения.

    ``value`` может быть ``Path``, строкой с ``/`` или ``\\`` либо ``None``.
    ``tail`` задаёт число сохраняемых хвостовых сегментов; ``tail <= 0``
    оставляет только последний сегмент.
    """
    if value is None:
        return UNKNOWN_REF

    text = str(value).strip().strip('"')
    if not text:
        return UNKNOWN_REF

    absolute = bool(_ABSOLUTE_RE.match(text))
    segments = [
        segment
        for segment in _SEPARATORS_RE.split(text)
        if segment not in ("", ".", "..") and not _WINDOWS_DRIVE_RE.match(segment)
    ]
    if not segments:
        return UNKNOWN_REF

    if tail <= 0:
        return segments[-1]

    if not absolute:
        if len(segments) <= tail:
            return "/".join(segments)
        return f"{TRUNCATION_MARKER}/" + "/".join(segments[-tail:])

    # Абсолютный путь: начало отбрасывается всегда. Если сегментов меньше
    # хвоста, надёжного слоя-ориентира нет — остаётся только имя.
    kept = segments[-tail:] if len(segments) > tail else segments[-1:]
    return f"{TRUNCATION_MARKER}/" + "/".join(kept)


def safe_error_text(error: BaseException | object, *paths: object) -> str:
    """Вернуть текст ошибки без известных абсолютных путей.

    ``OSError`` и производные часто повторяют имя файла внутри ``str(exc)``.
    Поэтому очистка только явного ``{path}`` рядом с ``{exc}`` недостаточна.
    Для каждого связанного пути заменяются исходная запись и варианты с
    обоими разделителями. Остальной текст исключения сохраняется.
    """
    text = str(error)
    for path in paths:
        if path is None:
            continue
        raw = str(path)
        if not raw:
            continue
        replacement = safe_path_ref(raw)
        variants = {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                text = text.replace(variant, replacement)
    return text
