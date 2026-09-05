from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
VALIDATOR = ROOT / "scripts" / "validate_release.py"


def _project() -> dict[str, object]:
    with PYPROJECT.open("rb") as stream:
        return tomllib.load(stream)["project"]


def test_packaging_metadata_contract() -> None:
    project = _project()
    assert project["name"] == "v8unpack-agent"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"] == {
        "v8unpack-agent-run": "v8unpack_agent.cli:main"
    }


def test_vcs_dependency_is_an_explicit_release_blocker() -> None:
    dependencies = _project()["dependencies"]
    assert isinstance(dependencies, list)
    has_direct = any(
        isinstance(item, str)
        and (" @ " in item or "git+" in item.casefold() or "file:" in item.casefold())
        for item in dependencies
    )

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
