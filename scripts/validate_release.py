from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
_VERSION_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+){2}(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?$"
)


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[|\Z)",
        text,
    )
    if match is None:
        raise ValueError(f"pyproject.toml has no [{name}] table")
    return match.group(1)


def _string_value(section: str, key: str) -> str | None:
    match = re.search(
        rf'(?m)^{re.escape(key)}\s*=\s*"([^"]+)"\s*$',
        section,
    )
    return match.group(1) if match is not None else None


def _dependencies(section: str) -> list[str] | None:
    match = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)^\]\s*$", section)
    if match is None:
        return None
    return re.findall(r'"([^"]+)"', match.group(1))


def validate_release(tag: str | None = None) -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    project = _section(text, "project")
    scripts = _section(text, "project.scripts")
    errors: list[str] = []

    name = _string_value(project, "name")
    version = _string_value(project, "version")
    requires_python = _string_value(project, "requires-python")
    console_script = _string_value(scripts, "v8unpack-agent-run")
    dependencies = _dependencies(project)

    if name != "v8unpack-agent":
        errors.append("project.name must be v8unpack-agent")
    if version is None or not _VERSION_RE.fullmatch(version):
        errors.append("project.version must use the supported PEP 440 subset")
    if requires_python != ">=3.10":
        errors.append("project.requires-python must match the CI baseline >=3.10")
    if console_script != "v8unpack_agent.cli:main":
        errors.append("console script v8unpack-agent-run is missing or invalid")

    if dependencies is None:
        errors.append("project.dependencies must be a list of strings")
    else:
        direct = [
            item
            for item in dependencies
            if " @ " in item
            or "git+" in item.casefold()
            or "file:" in item.casefold()
        ]
        if direct:
            errors.append(
                "direct/VCS runtime dependencies block release: "
                + ", ".join(direct)
            )

    if tag is not None and version is not None and tag != f"v{version}":
        errors.append(f"tag {tag!r} does not match project version {version!r}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate release metadata")
    parser.add_argument("--tag", help="Git tag expected to equal v<project.version>")
    args = parser.parse_args(argv)

    try:
        errors = validate_release(args.tag)
    except (OSError, ValueError) as exc:
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
