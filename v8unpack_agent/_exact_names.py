"""Точное сопоставление имён в каталоге выгрузки (issue #208).

Сканеры модулей ищут каталоги типов, контейнеры и файлы по каноничным именам.
Прямое обращение ``parent / name`` зависит от файловой системы: на Windows и
macOS оно находит ``catalog`` по имени ``Catalog``, на Linux нет. Здесь имя
сравнивается побайтово с тем, что вернул ``os.scandir()``, поэтому результат
не зависит от ОС. Нормализация Юникода не выполняется.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["exact_child", "exact_child_or_none"]


def exact_child(parent: Path, name: str) -> Path | None:
    """Вернуть ``parent / name``, если запись с точно таким именем существует.

    ``None`` означает, что точного имени в каталоге нет. ``OSError`` при
    чтении каталога не подавляется: вызывающий сканер сам выбирает статус.
    """
    with os.scandir(parent) as entries:
        for entry in entries:
            if entry.name == name:
                return parent / entry.name
    return None


def exact_child_or_none(parent: Path, name: str) -> Path | None:
    """То же, но нечитаемый или отсутствующий каталог даёт ``None``."""
    try:
        return exact_child(parent, name)
    except OSError:
        return None
