from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
_VERSION_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+){2}(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?$"
)


def _project_metadata() -> dict[str, Any]:
    with PYPROJECT.open("rb") as stream:
        data = tomllib.load(stream)
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml has no [project] table")
    return project


def validate_release(tag: str | None = None) -> list[str]:
    project = _project_metadata()
    errors: list[str] = []

    name = project.get("name")
    version = project.get("version")
    scripts = project.get("scripts")
    dependencies = project.get("dependencies")

    if name != "v8unpack-agent":
        errors.append("project.name must be v8unpack-agent")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        errors.append("project.version must use the supported PEP 440 subset")
    if project.get("requires-python") != ">=3.10":
        errors.append("project.requires-python must match the CI baseline >=3.10")
    if not isinstance(scripts, dict) or scripts.get("v8unpack-agent-run") != (
        "v8unpack_agent.cli:main"
    ):
        errors.append("console script v8unpack-agent-run is missing or invalid")

    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        errors.append("project.dependencies must be a list of strings")
    else:
        direct = [
            item
            for item in dependencies
            if " @ " in item or "git+" in item.casefold() or "file:" in item.casefold()
        ]
        if direct:
            errors.append(
                "direct/VCS runtime dependencies block release: " + ", ".join(direct)
            )

    if tag is not None and isinstance(version, str) and tag != f"v{version}":
        errors.append(f"tag {tag!r} does not match project version {version!r}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate release metadata")
    parser.add_argument("--tag", help="Git tag expected to equal v<project.version>")
    args = parser.parse_args(argv)

    try:
        errors = validate_release(args.tag)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"release validation failed: {error}", file=sys.stderr)
        return 1

    print("release metadata validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
