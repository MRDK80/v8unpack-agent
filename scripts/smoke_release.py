"""Exercise the installed CLI on a synthetic form in both representations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    with TemporaryDirectory(prefix="v8unpack-release-smoke-") as directory:
        root = Path(directory)
        form = root / "export" / "Catalog" / "SampleObject" / "ItemForm" / "Variant1"
        form.mkdir(parents=True)
        (form / "ItemForm.obj.bsl").write_bytes(
            "\ufeff// SOURCE TEXT\n## BSL\nПроцедура Тест()\n"
            '    Сообщить("a\u200bb");\nКонецПроцедуры\n'.encode("utf-8")
        )
        reports = []
        prompt_sizes = []
        for representation in ("legacy", "framed"):
            report_path = root / f"{representation}.json"
            result = subprocess.run(
                [
                    sys.executable, "-m", "v8unpack_agent.cli", "export", "--json",
                    "--report-path", str(report_path), "--representation", representation,
                ],
                cwd=root,
                capture_output=True,
                check=False,
            )
            # This fixture supplies BSL without an element index.
            assert result.returncode == 3, (result.stdout, result.stderr)
            envelope = json.loads(result.stdout)
            assert envelope["data"]["report"]["written"]
            prompt_sizes.append(envelope["data"]["prompt_chars"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["schema_version"] == 2
            assert report["summary"]["partial"] == 1
            assert report["objects"][0]["reason_code"] == "elem_index_unavailable"
            del report["run"]["started_at"]
            del report["run"]["finished_at"]
            reports.append(report)
        assert reports[0] == reports[1]
        assert 0 < prompt_sizes[0] < prompt_sizes[1], prompt_sizes
        print("legacy and framed smoke passed")


if __name__ == "__main__":
    main()
