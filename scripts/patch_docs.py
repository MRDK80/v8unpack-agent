#!/usr/bin/env python3
"""
patch_docs.py v3 — патч документации v8unpack-agent после PR #110 и PR #111.

Что исправляет:
  1. elem_parser.md  — таблица "Связанное": #107 open→closed PR #110,
                       #109 open→closed PR #111;
                       #108 — уточнение формулировки (4 формы, closed PR #110);
                       таблица причин — добавлена строка PLATFORM_DYNAMIC (B3).
  2. form_classifier.md — раздел "Связь с диагностикой": 77→47, 48→26, 12→2.
  3. IMPLEMENTATION_STATUS.md — #108 "(12 форм)" → "(4 формы)"; хвост #107.
  4. CHANGELOG.md — объединение дублирующих ### Added / ### Changed / ### Fixed
                    в секции [Unreleased].

Скрипт идемпотентен: повторный запуск не падает с ошибкой.

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
        sys.stdout.writelines(diff) if diff else print(f"[no change] {p}")
    else:
        p.write_text(text, encoding="utf-8")
        print(f"[patched]   {p}")


def sub(pattern: str, repl: str, text: str, flags: int = 0, required: bool = True) -> str:
    """re.sub c=1, optional проверка наличия.
    required=True  — бросает ValueError если не нашло.
    required=False — тихо пропускает (идемпотентность).
    """
    result, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if required and n == 0:
        raise ValueError(f"Pattern not found:\n  {pattern!r}")
    return result


# ---------------------------------------------------------------------------
# Патч 1: elem_parser.md
# ---------------------------------------------------------------------------

def patch_elem_parser(text: str) -> str:

    # 1a. #107 open → closed, PR #110 (если ещё open)
    text = sub(
        r'(\| \[#107\]\(https://github\.com/MRDK80/v8unpack-agent/issues/107\) '
        r'\| Категория B: UUID колонок в реквизитах табличных частей \| )open( \|)',
        r'\1closed, PR #110\2',
        text, required=False
    )

    # 1b. #109 open → closed, PR #111
    text = sub(
        r'(\| \[#109\]\(https://github\.com/MRDK80/v8unpack-agent/issues/109\) '
        r'\| Категория C: формы без виджетов данных → `service` \| )open( \|)',
        r'\1closed, PR #111\2',
        text, required=False
    )

    # 1c. #108 — уточнить формулировку (old style → new style)
    text = sub(
        r'\| \[#108\]\(https://github\.com/MRDK80/v8unpack-agent/issues/108\) '
        r'\| Категория A: `ChartOfCharacteristicType` закрыт \(issue #108\); '
        r'`CommonForm` → `NO_OWNER_OBJECT` by design \| closed \|',
        '| [#108](https://github.com/MRDK80/v8unpack-agent/issues/108) '
        '| Категория A (4 формы): `ChartOfCharacteristicType` (2) и `CommonForm` (2) — '
        '`NO_OWNER_OBJECT` by design | closed, PR #110 |',
        text, required=False
    )

    # 1d. PLATFORM_DYNAMIC в таблице причин (если отсутствует)
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

    text = sub(r'(На УТ 10\.3 это )77( форм)', r'\g<1>47\2', text, required=False)
    text = sub(
        r'48 с непопадающими UUID колонок `TabularField`',
        '26 с непопадающими UUID колонок `TabularField` (26 форм исправлено в #107)',
        text, required=False
    )
    text = sub(
        r'12 с пустой картой реквизитов владельца',
        '2 с пустой картой реквизитов владельца',
        text, required=False
    )
    text = sub(
        r'(Перевод 17 форм категории `NO_TABULAR_NO_WIDGETS` из `unknown` в `service`\n'
        r'реализован в #109 \(PR #111\) через новую функцию `classify_no_widgets_form\(\)`\.)',
        r'Категория B (26 форм с непопадающими UUID) исправлена в #107 (PR #110).\n'
        r'Категория A (2 оставшиеся формы `CommonForm`) получила диагноз `NO_OWNER_OBJECT` by design (#108).\n'
        r'\1',
        text, required=False
    )
    return text


# ---------------------------------------------------------------------------
# Патч 3: IMPLEMENTATION_STATUS.md
# ---------------------------------------------------------------------------

def patch_impl_status(text: str) -> str:

    text = sub(
        r'(#108 — категория A \()12( форм\))',
        r'\g<1>4\2',
        text, required=False
    )
    text = sub(
        r'\n  реквизитов владельца\. Гипотеза — колонки ссылаются на реквизиты табличных\n'
        r'  частей \(`TabularSections\[\]\.Properties`\) или на независимые значения\.',
        '',
        text, required=False
    )
    return text


# ---------------------------------------------------------------------------
# Патч 4: CHANGELOG.md
# Объединяем дублирующие ### Added / ### Changed / ### Fixed
# в секции [Unreleased].
# Работает как при наличии версионных секций внизу, так и без них.
# ---------------------------------------------------------------------------

def _extract_unreleased(text: str) -> tuple[str, str, str]:
    """(prefix, section, suffix) — section это весь блок [Unreleased]."""
    # Случай 1: есть следующая версионная секция ## [x.y.z]
    m = re.search(r'(## \[Unreleased\].*?)(?=\n## \[)', text, re.DOTALL)
    if m:
        return text[:m.start()], m.group(1), text[m.end():]
    # Случай 2: [Unreleased] — единственная секция
    m2 = re.search(r'(## \[Unreleased\].*)', text, re.DOTALL)
    if m2:
        return text[:m2.start()], m2.group(1), ""
    raise ValueError("Секция [Unreleased] не найдена в CHANGELOG")


def _merge_subheadings(section: str) -> str:
    """Объединяет повторные ### Added / ### Changed / ### Fixed.
    Порядок в результате: Added → Changed → Fixed.
    """
    header_re = re.compile(r'^(### .+)$', re.MULTILINE)
    parts = header_re.split(section)  # [preamble, h1, body1, h2, body2, ...]

    if len(parts) <= 1:
        return section

    preamble = parts[0]
    blocks: dict[str, list[str]] = {}
    order: list[str] = []

    for heading, body in zip(parts[1::2], parts[2::2]):
        key = heading.strip()
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        stripped = body.strip('\n')
        if stripped:
            blocks[key].append(stripped)

    preferred = ["### Added", "### Changed", "### Fixed"]
    ordered_keys = [k for k in preferred if k in blocks] + \
                   [k for k in order if k not in preferred]

    out = preamble
    for key in ordered_keys:
        out += key + "\n\n"
        out += "\n\n".join(blocks[key])
        out += "\n\n"

    return out.rstrip('\n') + '\n'


def patch_changelog(text: str) -> str:
    prefix, section, suffix = _extract_unreleased(text)
    new_section = _merge_subheadings(section)
    if new_section == section:
        print("[CHANGELOG] Дублей нет — пропускаем")
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
    ap = argparse.ArgumentParser(description="Patch v8unpack-agent docs (idempotent)")
    ap.add_argument("--dry-run", action="store_true", help="Show diff, do not write")
    ap.add_argument("--docs-dir", default="docs", help="Path to docs/ (default: ./docs)")
    args = ap.parse_args()

    docs_dir = Path(args.docs_dir)
    errors = []

    for filename, patch_fn in PATCHES.items():
        candidates = [docs_dir / filename, Path(filename)]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            errors.append(f"[MISSING] {filename} — не найден в {docs_dir}/ и ./")
            continue
        try:
            write(path, patch_fn(read(path)), dry_run=args.dry_run)
        except ValueError as exc:
            errors.append(f"[ERROR] {filename}: {exc}")

    if errors:
        print("\n--- ОШИБКИ ---")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print("\nВсе патчи применены / уже актуальны.")


if __name__ == "__main__":
    main()
