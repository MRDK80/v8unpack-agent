"""Обезличенные ссылки на пути файловой системы (issue #123).

Предупреждения парсера регулярно публикуются: в отчётах, логах CI, теле
issue и PR, промптах к модели. Абсолютный путь несёт имя пользователя и
структуру локальной ФС, поэтому в текст предупреждения он попадать не
должен. При этом предупреждение обязано оставаться полезным: по нему
нужно понять, о какой форме речь.

Модуль решает ровно эту задачу одной функцией :func:`safe_path_ref`.
Он намеренно не зависит ни от чего внутри пакета, чтобы его могли
использовать и ``elem_parser``, и ``object_decoder`` без циклических
импортов.

Почему не :mod:`pathlib`
------------------------

На POSIX ``Path`` не считает ``\\`` разделителем, поэтому строка вида
``C:\\dump\\Catalog\\Объект\\CatalogForm\\Форма`` осталась бы единым
сегментом и утечка сохранилась бы. Разбор идёт регулярным выражением по
обоим разделителям, поэтому результат не зависит от ОС, на которой
запущен код или тесты.

Гарантии
--------

* результат никогда не является абсолютным путём: ни ведущего
  разделителя, ни диска Windows, ни хоста UNC;
* у абсолютного входа сохраняются только последние значимые сегменты —
  для выгрузки 1С это «тип / объект / контейнер / форма»;
* если абсолютный путь короче хвоста, слоя-ориентира нет, поэтому
  остаётся только последний сегмент — безопасность важнее контекста;
* относительный путь не искажается, пока он не длиннее хвоста;
* в выводе всегда posix-разделитель ``/``;
* функция детерминирована и не обращается к файловой системе.
"""

from __future__ import annotations

import re

__all__ = [
    "SAFE_TAIL_SEGMENTS",
    "TRUNCATION_MARKER",
    "UNKNOWN_REF",
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

    Parameters
    ----------
    value:
        Путь в любом виде: ``Path``, ``str`` с ``/`` или ``\\``, ``None``.
    tail:
        Сколько хвостовых сегментов сохранять. ``tail <= 0`` оставляет
        только последний сегмент.

    Examples
    --------
    >>> safe_path_ref("/dump/Catalog/Объект/CatalogForm/Форма")
    '.../Catalog/Объект/CatalogForm/Форма'
    >>> safe_path_ref("Catalog/Объект/CatalogForm/Форма")
    'Catalog/Объект/CatalogForm/Форма'
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
