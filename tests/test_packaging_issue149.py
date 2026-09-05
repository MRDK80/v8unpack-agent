from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
VALIDATOR = ROOT / "scripts" / "validate_release.py"


def _pyproject_text() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def _quoted_value(text: str, key: str) -> str:
    match = re.search(
        rf'(?m)^{re.escape(key)}\s*=\s*"([^"]+)"\s*$',
        text,
    )
    assert match is not None, key
    return match.group(1)


def test_packaging_metadata_contract() -> None:
    text = _pyproject_text()
    assert _quoted_value(text, "name") == "v8unpack-agent"
    assert _quoted_value(text, "version") == "0.1.0"
    assert _quoted_value(text, "requires-python") == ">=3.10"
    assert (
        _quoted_value(text, "v8unpack-agent-run")
        == "v8unpack_agent.cli:main"
    )


def test_vcs_dependency_is_an_explicit_release_blocker() -> None:
    text = _pyproject_text()
    has_direct = "git+" in text or " @ " in text or "file:" in text.casefold()

    completed = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if has_direct:
        assert completed.returncode == 1
        assert "direct/VCS runtime dependencies block release" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr


def test_tag_must_match_static_project_version() -> None:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), "--tag", "v9.9.9"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "does not match project version" in completed.stderr


def test_release_workflow_has_minimal_publishing_permissions() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" not in workflow
    assert workflow.count("id-token: write") == 1
    assert "contents: write" not in workflow
    assert "PYPI_API_TOKEN" not in workflow
    assert "password:" not in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "workflow_dispatch:" in workflow
    assert 'tags:\n      - "v*"' in workflow
    assert "testpypi" in workflow
    assert "environment:" in workflow


def test_publish_job_uses_prebuilt_artifact() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split("  publish:", maxsplit=1)[1]
    assert "actions/download-artifact@v4" in publish
    assert "python -m build" not in publish
    assert "actions/checkout" not in publish
