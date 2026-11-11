"""Check the public recorded-response demo without models or network."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_reports_proposals_and_actual_effects():
    result = subprocess.run(
        [sys.executable, "scripts/demo_unicode_image_issue523.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    output = result.stdout
    assert "Recorded responses from synthetic fixtures. No new model calls." in output
    assert "legacy_guard: 269/336 correct" in output
    assert "framed_guard: 97/144 correct" in output
    assert output.count("blocked; changed files: []") == 2
    assert output.count("committed; changed files: ['consumer/report.json']") == 2
    assert 'report: {"query": "SELECT 1"}' in output
