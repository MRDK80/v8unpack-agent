"""Documentation contract для корневого AGENTS.md (issue #257).

Проверяются устойчивые смысловые условия, а не дословные формулировки,
пробелы или порядок строк.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS = REPO_ROOT / "AGENTS.md"
MAX_LINES = 500


def _pyproject() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _section(name: str) -> str:
    """Тело секции TOML до следующего заголовка (tomllib недоступен в 3.10)."""
    match = re.search(rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[|\Z)", _pyproject(), re.MULTILINE | re.DOTALL)
    assert match, f"секция [{name}] не найдена в pyproject.toml"
    return match.group(1)


def _text() -> str:
    return AGENTS.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def test_agents_md_exists_in_root() -> None:
    assert AGENTS.is_file()


def test_agents_md_within_line_limit() -> None:
    lines = _text().splitlines()
    assert len(lines) <= MAX_LINES, f"AGENTS.md: {len(lines)} строк при пределе {MAX_LINES}"


@pytest.mark.parametrize("command", ["ruff check .", "mypy", "pytest"])
def test_local_gate_commands_present(command: str) -> None:
    assert command in _text()


def test_mypy_runs_without_explicit_arguments() -> None:
    text = _text()
    files = re.search(r"^files\s*=\s*\[(.*?)\]", _section("tool.mypy"), re.MULTILINE)
    assert files
    for target in re.findall(r'"([^"]+)"', files.group(1)):
        assert target in text, f"область mypy {target} не названа"
    assert not re.search(r"^[ \t]*mypy[ \t]+\S", text, flags=re.MULTILINE), (
        "mypy в AGENTS.md запускается с явными аргументами"
    )


def test_branching_rules_and_main_push_ban() -> None:
    norm = _norm(_text())
    for token in ("roadmap/", "task/", "hotfix/", "docs/branching.md"):
        assert token in norm
    assert re.search(r"прям\w* push в `?main`?[^.]*запрещ", norm)


def test_anonymization_rule_present() -> None:
    norm = _norm(_text())
    assert "обезличен" in norm
    for token in ("хост", "ip", "строки подключения", "абсолютн"):
        assert token in norm


@pytest.mark.parametrize(
    "target",
    [
        "README.md",
        "CONTRIBUTING.md",
        "docs/BRANCHING.md",
        "docs/RELEASING.md",
        "docs/search_cli.md",
        "docs/run_report.md",
        "docs/diagnostic_sanitizer.md",
    ],
)
def test_canonical_docs_linked_and_exist(target: str) -> None:
    assert f"]({target})" in _text(), f"нет ссылки на {target}"
    assert (REPO_ROOT / target).is_file()


def test_all_relative_links_resolve() -> None:
    for link in re.findall(r"\]\(([^)#\s]+)\)", _text()):
        if "://" in link:
            continue
        assert (REPO_ROOT / link).exists(), f"битая ссылка {link}"


def test_console_script_matches_pyproject() -> None:
    names = re.findall(r"^([\w.-]+)\s*=", _section("project.scripts"), re.MULTILINE)
    assert names
    for name in names:
        assert name in _text()


def test_exit_codes_match_cli_constants() -> None:
    from v8unpack_agent import cli

    norm = _norm(_text())
    for name in cli.__all__:
        if name.startswith("EXIT_"):
            value = getattr(cli, name)
            assert re.search(rf"\|\s*{value}\s*\|", norm), f"код {value} ({name}) не описан"


def test_supported_python_matches_pyproject_and_ci() -> None:
    text = _text()
    required = re.search(r'^requires-python\s*=\s*"([^"]+)"', _section("project"), re.MULTILINE)
    assert required
    assert required.group(1) in text
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    for version in re.findall(r'"(3\.\d+)"', workflow):
        assert version in text
    for os_name in ("Ubuntu", "Windows"):
        assert os_name in text


def test_absent_capabilities_only_denied() -> None:
    norm = _norm(_text())
    for token in ("mcp", "fastapi", "langchain", "llamaindex"):
        for line in _norm_lines():
            if token in line:
                assert re.search(r"не (заявля|использ|встро)|отсутству|нет", line), (
                    f"{token} упомянут не как отсутствующая возможность"
                )
    assert "index_cf()" in norm


def _norm_lines() -> list[str]:
    paragraphs = re.split(r"\n\s*\n", _text())
    return [_norm(p) for p in paragraphs]


def test_no_domain_data_or_absolute_paths() -> None:
    text = _text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "IP-адрес"
    assert not re.search(r"(?<![\w/])/(?:home|Users|root|mnt)/", text), "абсолютный путь"
    assert not re.search(r"\b[A-Za-z]:\\", text), "Windows-путь с диском"
    assert "\\\\" not in text, "UNC-путь"
    assert not re.search(r"(?i)(server|srvr|ref)\s*=", text), "строка подключения"
