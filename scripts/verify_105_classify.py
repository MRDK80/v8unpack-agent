#!/usr/bin/env python
"""Прогон классификатора неиндексированных форм на живых данных.

Использование:
    python scripts/verify_105_classify.py <путь к распакованной базе> [--examples N]

Пример:
    python scripts/verify_105_classify.py ~/1C_LLM/cf_export --examples 3

VERBOSE режим:
    python scripts/verify_105_classify.py ~/1C_LLM/cf_export --verbose
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Позволяем запускать из корня проекта
brе = Path(__file__).parent.parent
if str(brе) not in sys.path:
    sys.path.insert(0, str(brе))

from v8unpack_agent.elem_parser import (
    ElemIndexResult,
    UnindexedReason,
    classify_unindexed_form,
    parse_elem_json,
)


# ---------------------------------------------------------------------------
# Поиск форм
# ---------------------------------------------------------------------------

def find_form_dirs(base: Path) -> list[Path]:
    """Все директории, содержащие *.elem.json."""
    result: list[Path] = []
    for p in base.rglob("*.elem.json"):
        result.append(p.parent)
    # дедупликация: одна директория может вмещать несколько .elem.json
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in result:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------

def run(base: Path, examples_n: int = 3, verbose: bool = False) -> None:
    form_dirs = find_form_dirs(base)
    print(f"Найдено форм: {len(form_dirs)}")

    counters: Counter[str] = Counter()
    buckets: defaultdict[str, list[tuple[Path, str]]] = defaultdict(list)
    errors: list[tuple[Path, str]] = []
    indexed_ok = 0

    for form_dir in form_dirs:
        try:
            result: ElemIndexResult = parse_elem_json(form_dir)
        except Exception as exc:
            errors.append((form_dir, str(exc)))
            continue

        if result.elem_index_ok and result.elements:
            indexed_ok += 1
            continue

        # неиндексированая форма
        unindexed = classify_unindexed_form(form_dir, result)
        key = unindexed.reason.value
        counters[key] += 1
        buckets[key].append((form_dir, unindexed.detail))

        if verbose:
            print(f"  [{key}] {form_dir.relative_to(base)}")
            print(f"          {unindexed.detail}")

    # ---------------------------------------------------------------------------
    # Итог
    # ---------------------------------------------------------------------------
    total_unindexed = sum(counters.values())
    print()
    print("=" * 64)
    print(f"Индексировано успешно : {indexed_ok}")
    print(f"Неиндексировано   : {total_unindexed}")
    if errors:
        print(f"Ошибки parse   : {len(errors)}")
    print("=" * 64)

    label_map = {
        UnindexedReason.NO_TABULAR_NO_WIDGETS.value:       "C  no_tabular_no_widgets   ",
        UnindexedReason.TABULAR_FIELD_NO_UUID_HITS.value:  "B  tabular_no_uuid_hits    ",
        UnindexedReason.TABULAR_FIELD_EMPTY_ATTR_MAP.value:"A  tabular_empty_attr_map  ",
        UnindexedReason.NO_LEGACY_JSON.value:              "D  no_legacy_json          ",
        UnindexedReason.UNKNOWN.value:                     "?  unknown                 ",
    }

    for reason_value, label in label_map.items():
        cnt = counters.get(reason_value, 0)
        bar = "█" * min(cnt, 60)
        print(f"  {label}: {cnt:5d}  {bar}")

    # Примеры
    if examples_n > 0:
        print()
        for reason_value, label in label_map.items():
            bucket = buckets.get(reason_value, [])
            if not bucket:
                continue
            print(f"--- {label.strip()} ({len(bucket)} всего) ---")
            for form_dir, detail in bucket[:examples_n]:
                try:
                    rel = form_dir.relative_to(base)
                except ValueError:
                    rel = form_dir
                print(f"  {rel}")
                if detail:
                    # обрезаем длинные detail для читаемости
                    short = detail[:120] + ("..." if len(detail) > 120 else "")
                    print(f"    └─ {short}")
            print()

    if errors:
        print("--- parse errors ---")
        for form_dir, exc_msg in errors[:5]:
            try:
                rel = form_dir.relative_to(base)
            except ValueError:
                rel = form_dir
            print(f"  {rel}: {exc_msg[:100]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Прогон классификатора issue #105 на живых данных"
    )
    parser.add_argument(
        "base",
        type=Path,
        help="Корневая директория распакованной базы 1С",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=3,
        metavar="N",
        help="Кол-во примеров на категорию (default=3, 0=не печатать)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Печатать каждую неиндексированную форму по мере обработки",
    )
    args = parser.parse_args()

    if not args.base.is_dir():
        print(f"Ошибка: директория не найдена: {args.base}", file=sys.stderr)
        sys.exit(1)

    run(args.base, examples_n=args.examples, verbose=args.verbose)


if __name__ == "__main__":
    main()
