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

Единая граница санитизации (issue #142)
---------------------------------------

:func:`sanitize_diagnostic` — единственный санитайзер внешнего
диагностического текста. Он не требует знать путь заранее: абсолютные
POSIX-, Windows- и UNC-пути, ``~`` и домашние каталоги находятся по форме
записи. :func:`safe_error_text` пропускает свой результат через него же.
Сбой санитизации никогда не возвращает исходную строку — только
:data:`SANITIZER_FAILED_TEXT`. Перечень каналов, которые проходят через
границу, — ``docs/diagnostic_sanitizer.md``.
"""

from __future__ import annotations

import re

__all__ = [
    "SAFE_TAIL_SEGMENTS",
    "SANITIZER_FAILED_CODE",
    "SANITIZER_FAILED_TEXT",
    "TRUNCATION_MARKER",
    "UNKNOWN_REF",
    "safe_error_text",
    "safe_path_ref",
    "sanitize_diagnostic",
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


def safe_error_text(
    error: BaseException | object,
    *paths: object,
    tail: int = SAFE_TAIL_SEGMENTS,
) -> str:
    """Вернуть текст ошибки без известных абсолютных путей.

    ``OSError`` и производные часто повторяют имя файла внутри ``str(exc)``.
    На Windows это имя может быть записано с удвоенными обратными слешами,
    поскольку форматируется через строковое представление ``filename``.
    Для каждого связанного пути заменяются исходная запись, варианты с обоими
    разделителями и escaped Windows-вариант. Остальной текст сохраняется.

    ``tail`` передаётся в :func:`safe_path_ref`: object JSON использует
    три сегмента «тип / объект / файл», пути форм сохраняют default из
    четырёх сегментов.

    Итог проходит через :func:`sanitize_diagnostic` (issue #142): пути,
    которые не переданы явно, тоже не выходят наружу.
    """
    try:
        text = str(error)
    except Exception:  # noqa: BLE001 — fail-closed: str/repr объекта не раскрывается
        return SANITIZER_FAILED_TEXT
    for path in paths:
        if path is None:
            continue
        raw = str(path)
        if not raw:
            continue
        replacement = safe_path_ref(raw, tail=tail)
        backslash = raw.replace("/", "\\")
        variants = {
            raw,
            raw.replace("\\", "/"),
            backslash,
            backslash.replace("\\", "\\\\"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                text = text.replace(variant, replacement)
    return sanitize_diagnostic(text)


#: Стабильный машинный код отказа санитайзера (issue #142).
SANITIZER_FAILED_CODE = "sanitizer_failed"

#: Нейтральный текст, который возвращается вместо исходной строки,
#: если санитизация не удалась. Исходный текст наружу не выдаётся.
SANITIZER_FAILED_TEXT = f"<диагностика скрыта: {SANITIZER_FAILED_CODE}>"

#: Первые сегменты абсолютного пути, за которыми идёт имя пользователя.
_HOME_CONTAINERS = frozenset({"home", "users"})
#: Первые сегменты, которые сами являются домашним корнем.
_HOME_ROOTS = frozenset({"root", "~"})

_PATH_CHARS = r"""[^\s'"`<>|,;()\[\]{}:]"""
_LOCAL_PATH_RE = re.compile(
    # UNC: два (или экранированные четыре) обратных слэша либо //.
    r"(?P<unc>(?<![\w:/\\])(?:\\{2,4}|//)(?![\\/])" + _PATH_CHARS + r"+)"
    # Windows: буква диска с любым разделителем.
    r"|(?P<drive>(?<![\w])[A-Za-z]:[\\/]" + _PATH_CHARS + r"*)"
    # Домашний каталог: тильда и разделитель.
    r"|(?P<home>(?<![\w.~$\-/\\])~[\\/]" + _PATH_CHARS + r"*)"
    # POSIX absolute или корень текущего диска NT.
    r"|(?P<rooted>(?<![\w.~$\-/\\])[\\/](?![\\/])" + _PATH_CHARS + r"+)"
)


def _collapse_local_path(match: re.Match[str]) -> str:
    """Заменить абсолютный путь обезличенным хвостом.

    Отбрасываются корень, диск, сервер и share UNC, домашний контейнер
    вместе с именем пользователя. Сохраняются последние
    :data:`SAFE_TAIL_SEGMENTS` сегментов остатка.
    """
    token = match.group(0)
    segments = [
        segment
        for segment in _SEPARATORS_RE.split(token)
        if segment not in ("", ".", "..", "?") and not _WINDOWS_DRIVE_RE.match(segment)
    ]
    if match.lastgroup == "unc":
        segments = segments[2:]
    if segments and segments[0].lower() in _HOME_ROOTS:
        segments = segments[1:]
    elif segments and segments[0].lower() in _HOME_CONTAINERS:
        segments = segments[2:]
    kept = segments[-SAFE_TAIL_SEGMENTS:]
    if not kept:
        return TRUNCATION_MARKER
    return f"{TRUNCATION_MARKER}/" + "/".join(kept)


def _sanitize_text(text: str) -> str:
    """Чистая замена локальных путей без fail-closed обёртки."""
    return _LOCAL_PATH_RE.sub(_collapse_local_path, text)


def sanitize_diagnostic(value: object) -> str:
    """Единая fail-closed граница диагностического текста (issue #142).

    Принимает строку, исключение или иной объект. Абсолютные POSIX-,
    Windows- и UNC-пути, ``~`` и домашние каталоги ``home/<имя>`` и
    ``Users/<имя>`` заменяются обезличенным хвостом ``.../<сегменты>``.
    Относительные пути, машинные коды и прочий текст не меняются.

    Функция детерминирована, не обращается к файловой системе и
    идемпотентна: повторный вызов на результате возвращает его же.
    Любой сбой, включая сбой ``str(value)``, даёт
    :data:`SANITIZER_FAILED_TEXT` — исходная строка наружу не выдаётся.
    """
    try:
        text = value if isinstance(value, str) else str(value)
        return _sanitize_text(text)
    except Exception:  # noqa: BLE001 — fail-closed: наружу только нейтральный текст
        return SANITIZER_FAILED_TEXT
