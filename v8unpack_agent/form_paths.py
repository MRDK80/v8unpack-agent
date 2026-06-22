"""Фабрика путей к текстам обычной формы по конвенции выгрузки.

Цель — по имени формы вычислить путь к её текстам без обхода файловой
системы: это убирает зависимость от конкретной структуры выгрузки и упрощает
тесты. Расположение файлов зафиксировано в одном месте — здесь. Если структура
выгрузки изменится, правка нужна только в этом модуле; индекс и агент трогать
не придётся.

Конвенция (см. статью «Обычные формы 1С в агентном пайплайне»):

    <unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
    <unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
    <unpacked_root>/Form/<имя>/Form.json             # метаданные формы
    <unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
"""
from __future__ import annotations

from pathlib import Path


def form_root(unpacked_root: Path, form_name: str) -> Path:
    """Каталог распакованной формы ``<unpacked_root>/Form/<имя>/``."""
    return unpacked_root / "Form" / form_name


def form_paths(unpacked_root: Path, form_name: str) -> dict[str, Path]:
    """Пути к текстам формы по конвенции.

    Возвращает словарь с ключами:

    - ``object_module`` — ``Form/<имя>/Form.obj.bsl`` (код самой формы);
    - ``ext_module``    — ``Form/<имя>/Ext/ObjectModule.bsl``;
    - ``metadata``      — ``Form/<имя>/Form.json``.

    Пути могут ещё не существовать на диске — это чистая арифметика путей.
    """
    base = form_root(unpacked_root, form_name)
    return {
        "object_module": base / "Form.obj.bsl",
        "ext_module": base / "Ext" / "ObjectModule.bsl",
        "metadata": base / "Form.json",
    }


def item_modules(unpacked_root: Path, form_name: str) -> tuple[Path, ...]:
    """Код вложенных панелей/групп формы из ``Form/<имя>/Items/``.

    Самая частая причина частичной распаковки — вложенные группы и страницы.
    Распаковщик кладёт их код в отдельные файлы внутри ``Items/``; чтобы агент
    видел эти вложенные обработчики, а не только корневой ``Form.obj.bsl``,
    их нужно собирать отдельно.

    Возвращает отсортированный кортеж путей к ``*.bsl`` внутри ``Items/``
    (рекурсивно). Пустой кортеж, если каталога ``Items/`` нет.
    """
    items_dir = form_root(unpacked_root, form_name) / "Items"
    if not items_dir.is_dir():
        return ()
    return tuple(sorted(p for p in items_dir.rglob("*.bsl") if p.is_file()))


def all_module_paths(unpacked_root: Path, form_name: str) -> tuple[Path, ...]:
    """Все файлы кода формы: корневой модуль формы, модуль объекта и панели.

    Удобный сбор всех ``*.bsl``-входов формы для индексации/RAG. В кортеж
    попадают только реально существующие на диске файлы.
    """
    paths = form_paths(unpacked_root, form_name)
    candidates = [paths["object_module"], paths["ext_module"]]
    candidates.extend(item_modules(unpacked_root, form_name))
    return tuple(p for p in candidates if p.is_file())
