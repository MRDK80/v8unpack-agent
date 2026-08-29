"""Обезличенный агрегат общих модулей конфигурации (issue #151).

Требуется путь к распакованной выгрузке. Имена модулей, BSL-текст и
абсолютные пути не печатаются.

Запуск:

python examples/common_modules.py /path/to/cf_export --runs 2
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from v8unpack_agent.common_modules import (
    build_common_module_context,
    scan_common_modules,
)


def build_report(root: Path) -> dict[str, Any]:
    """Построить обезличенный агрегат одной выгрузки."""
    index = scan_common_modules(root)
    contexts = [
        build_common_module_context(entry, root)
        for entry in index.modules
    ]
    statuses = Counter(context.read_status for context in contexts)
    paths = [entry.bsl_path.as_posix() for entry in index.modules]

    return {
        "modules": index.total,
        "read_status": dict(sorted(statuses.items())),
        "duplicate_relative_paths": len(paths) - len(set(paths)),
        "absolute_entry_paths": sum(
            entry.bsl_path.is_absolute()
            for entry in index.modules
        ),
        "absolute_metadata_paths": sum(
            Path(context.metadata["bsl_path"]).is_absolute()
            for context in contexts
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Обезличенный агрегат CommonModule",
    )
    parser.add_argument(
        "export_root",
        type=Path,
        help="корень распакованной выгрузки конфигурации",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="число повторов для проверки детерминированности",
    )
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    try:
        reports = [
            build_report(args.export_root)
            for _ in range(args.runs)
        ]
    except (FileNotFoundError, NotADirectoryError) as exc:
        parser.error(str(exc))

    deterministic = all(report == reports[0] for report in reports[1:])
    output = {
        **reports[0],
        "runs": args.runs,
        "deterministic": deterministic,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())
