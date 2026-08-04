#!/usr/bin/env python3
"""
patch_docs.py — патч документации v8unpack-agent после PR #110 и PR #111.

Что исправляет:
  1. elem_parser.md  — таблица "Связанное": #107 open→closed PR #110,
                       #109 open→closed PR #111;
                       #108 — уточнение формулировки (4 формы, closed PR #110);
                       таблица причин — добавлена строка PLATFORM_DYNAMIC (B3).
  2. form_classifier.md — раздел "Связь с диагностикой": исправляем устаревшие
                          числа "77 форм" → 47, "48 с непопадающими UUID" → 26,
                          "12 с пустой картой" → 2; добавляем упоминание #107/#108.
  3. IMPLEMENTATION_STATUS.md — #108 "(12 форм)" → "(4 формы)";
                                 убираем осиротевший хвост после #107 closed.
  4. CHANGELOG.md — объединяем все лишние блоки "### Added" / "### Changed" / "### Fixed"
                    в [Unreleased] в первые соответствующие.

Запуск:
    python patch_docs.py [--dry-run] [--docs-dir PATH]

    --dry-run   показать diff, не писать файлы
    --docs-dir  путь к папке docs/ (по умолчанию ./docs)
"""

import argparse
import re
import sys
from pathlib import Path


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        import difflib
        orig = read(p).splitlines(keepends=True)
        new  = text.splitlines(keepends=True)
        diff = list(difflib.unified_diff(orig, new, fromfile=str(p), tofile=str(p) + " [patched]"))
        if diff:
            sys.stdout.writelines(diff)
        else:
            print(f"[no change] {p}")
    else:
        p.write_text(text, encoding="utf-8")
        print(f"[patched]   {p}")


def sub1(pattern: str, repl: str, text: str, flags: int = 0) -> str:
    """re.sub с проверкой ровно одного совпадения."""
    result, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise ValueError(f"Pattern not found (expected 1 match):\n  {pattern!r}")
    return result


# ---------------------------------------------------------------------------
# Патч 1: elem_parser.md
# ---------------------------------------------------------------------------

def patch_elem_parser(text: str) -> str:

    # 1a. Таблица "Связанное": #107 open → closed, PR #110
    text = sub1(
        r'(\| \[#107\]\(https://github\.com/MRDK80/v8unpack-agent/issues/107\) '
        r'\| Категория B: UUID колонок в реквизитах табличных частей \| )open( \|)',
        r'\1closed, PR #110\2',
        text
    )

    # 1b. Таблица "Связанное": #109 open → closed, PR #111
    text = sub1(
        r'(\| \[#109\]\(https://github\.com/MRDK80/v8unpack-agent/issues/109\) '
        r'\| Категория C: формы без виджетов данных → `service` \| )open( \|)',
        r'\1closed, PR #111\2',
        text
    )

    # 1c. Таблица "Связанное": #108 — уточнить формулировку
    text = sub1(
        r'\| \[#108\]\(https://github\.com/MRDK80/v8unpack-agent/issues/108\) '
        r'\| Категория A: `ChartOfCharacteristicType` закрыт \(issue #108\); '
        r'`CommonForm` → `NO_OWNER_OBJECT` by design \| closed \|',
        '| [#108](https://github.com/MRDK80/v8unpack-agent/issues/108) '
        '| Категория A (4 формы): `ChartOfCharacteristicType` (2) и `CommonForm` (2) — '
        '`NO_OWNER_OBJECT` by design | closed, PR #110 |',
        text
    )

    # 1d. Добавить PLATFORM_DYNAMIC если отсутствует
    if 'TABULAR_FIELD_PLATFORM_DYNAMIC' not in text:
        old_row = (
            '| 3 | `load_owner_attribute_map()` → `{}` | `TABULAR_FIELD_EMPTY_ATTR_MAP` (A) '
            '| `TabularField` есть, но карта реквизитов владельца пуста |'
        )
        new_rows = old_row + (
            '\n| 3a | платформенный источник (СКД / диаграмма) | `TABULAR_FIELD_PLATFORM_DYNAMIC` (B3) '
            '| Колонки формирует платформа динамически; статический разбор невозможен (#107) |'
        )
        text = text.replace(old_row, new_rows, 1)

    return text


# ---------------------------------------------------------------------------
# Патч 2: form_classifier.md
# ---------------------------------------------------------------------------

def patch_form_classifier(text: str) -> str:

    text = sub1(
        r'(На УТ 10\.3 это )77( форм)',
        r'\g<1>47\2',
        text
    )
    text = sub1(
        r'48 с непопадающими UUID колонок `TabularField`',
        '26 с непопадающими UUID колонок `TabularField` (26 форм исправлено в #107)',
        text
    )
    text = sub1(
        r'12 с пустой картой реквизитов владельца',
        '2 с пустой картой реквизитов владельца',
        text
    )
    text = sub1(
        r'(Перевод 17 форм категории `NO_TABULAR_NO_WIDGETS` из `unknown` в `service`\n'
        r'реализован в #109 \(PR #111\) через новую функцию `classify_no_widgets_form\(\)`\.)',
        r'Категория B (26 форм с непопадающими UUID) исправлена в #107 (PR #110).\n'
        r'Категория A (2 оставшиеся формы `CommonForm`) получила диагноз `NO_OWNER_OBJECT` by design (#108).\n'
        r'\1',
        text
    )
    return text


# ---------------------------------------------------------------------------
# Патч 3: IMPLEMENTATION_STATUS.md
# ---------------------------------------------------------------------------

def patch_impl_status(text: str) -> str:

    text = sub1(
        r'(#108 — категория A \()12( форм\))',
        r'\g<1>4\2',
        text
    )
    text = sub1(
        r'\n  реквизитов владельца\. Гипотеза — колонки ссылаются на реквизиты табличных\n'
        r'  частей \(`TabularSections\[\]\.Properties`\) или на независимые значения\.',
        '',
        text
    )
    return text


# ---------------------------------------------------------------------------
# Патч 4: CHANGELOG.md
# Объединяем дублирующие блоки ### Added / ### Changed / ### Fixed
# в секции [Unreleased].
#
# CHANGELOG может не иметь ни одной версионной секции — обрабатываем оба случая.
# ---------------------------------------------------------------------------

def _extract_unreleased(text: str) -> tuple[str, str, str]:
    """Возвращает (prefix, section, suffix).
    prefix — всё до начала заголовка ## [Unreleased].
    section — сам блок [Unreleased] (до следующей ## [ или до конца файла).
    suffix — всё после section.
    """
    # С lookahead: есть следующая версионная секция
    m = re.search(r'(## \[Unreleased\].*?)(?=\n## \[)', text, re.DOTALL)
    if m:
        return text[:m.start()], m.group(1), text[m.end():]
    # Без lookahead: [Unreleased] — единственная секция в файле
    m2 = re.search(r'(## \[Unreleased\].*)', text, re.DOTALL)
    if m2:
        return text[:m2.start()], m2.group(1), ""
    raise ValueError("Секция [Unreleased] не найдена в CHANGELOG")


def _merge_duplicate_subheadings(section: str) -> str:
    """  Объединяет повторные ### Added / ### Changed / ### Fixed.

    Алгоритм:
    1. Разбиваем section на блоки (разделитель — заголовки ### или начало секции).
    2. Для каждой метки (Added/Changed/Fixed) собираем все строки-буллеты в один блок.
    3. Порядок: Added, Changed, Fixed, остальные по порядку первого вхождения.
    """
    header_re = re.compile(r'^(### .+)$', re.MULTILINE)
    parts = header_re.split(section)  # [текстдо1гозаг, заг, тело, заг, тело, ...]

    if len(parts) <= 1:
        return section  # нет подзаголовков

    preamble = parts[0]  # текст до первой ### заголовки
    blocks: dict[str, list[str]] = {}  # метка → [список тел блоков]
    order: list[str] = []

    it = iter(parts[1:])
    for heading, body in zip(it, it):
        key = heading.strip()  # "### Added"
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        # Выделяем буллеты: строки, начинающиеся с '- ' или продолжающие абзац (с пробелом)
        stripped = body.strip('\n')
        if stripped:
            blocks[key].append(stripped)

    # Предпочтительный порядок заголовков
    preferred = ["### Added", "### Changed", "### Fixed"]
    ordered_keys = [k for k in preferred if k in blocks] + \
                   [k for k in order if k not in preferred]

    result = preamble
    for key in ordered_keys:
        result += key + "\n\n"
        result += "\n\n".join(blocks[key])
        result += "\n\n"

    return result.rstrip('\n') + '\n'


def patch_changelog(text: str) -> str:
    prefix, section, suffix = _extract_unreleased(text)
    new_section = _merge_duplicate_subheadings(section)
    if new_section == section:
        print("[CHANGELOG] Дублирующих блоков не найдено — пропускаем")
        return text
    return prefix + new_section + suffix


# ---------------------------------------------------------------------------
# Диспетчер
# ---------------------------------------------------------------------------

PATCHES = {
    "elem_parser.md":           patch_elem_parser,
    "form_classifier.md":       patch_form_classifier,
    "IMPLEMENTATION_STATUS.md": patch_impl_status,
    "CHANGELOG.md":             patch_changelog,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch v8unpack-agent docs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show diff only, do not write files")
    parser.add_argument("--docs-dir", default="docs",
                        help="Path to docs/ directory (default: ./docs)")
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir)
    errors = []

    for filename, patch_fn in PATCHES.items():
        candidates = [docs_dir / filename, Path(filename)]
        path = next((p for p in candidates if p.exists()), None)

        if path is None:
            errors.append(f"[MISSING] {filename} — не найден в {docs_dir}/ и ./")
            continue

        try:
            original = read(path)
            patched  = patch_fn(original)
            write(path, patched, dry_run=args.dry_run)
        except ValueError as exc:
            errors.append(f"[ERROR] {filename}: {exc}")

    if errors:
        print("\n--- ОШИБКИ ---")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print("\nВсе патчи применены успешно.")


if __name__ == "__main__":
    main()
