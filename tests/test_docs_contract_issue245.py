"""Контрактные проверки документации (issue #245).

Проверяется только то, что должно оставаться стабильным:

- размер README как landing page;
- отсутствие несуществующего публичного API в примерах кода;
- наличие явного предупреждения о том, что такого API нет;
- достижимость канонических документов из README.

Формулировки, порядок разделов и длины абзацев намеренно не проверяются.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

README_MAX_BYTES = 20_000

FENCE = "```"

#: API, которого в пакете нет. В прозе он упоминается как отсутствующий,
#: но в примерах кода появляться не должен.
ABSENT_API = ("index_cf(", "rag.rebuild(")

DOC_FILES = (
    Path("README.md"),
    Path("docs") / "pipeline.md",
    Path("docs") / "runner.md",
)

#: Документы, обязанные явно предупреждать об отсутствии API.
DISCLAIMER_FILES = (
    Path("README.md"),
    Path("docs") / "pipeline.md",
)

CANONICAL_LINKS = (
    "docs/pipeline.md",
    "docs/runner.md",
    "docs/run_report.md",
    "docs/scan_forms.md",
    "docs/form_classifier.md",
    "examples/README.md",
)


def read(rel: Path) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"отсутствует файл документации: {rel.as_posix()}"
    return path.read_text(encoding="utf-8")


def code_blocks(text: str) -> str:
    """Вернуть содержимое всех fenced-блоков одной строкой."""
    inside = False
    collected: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(FENCE):
            inside = not inside
            continue
        if inside:
            collected.append(line)
    return "\n".join(collected)


def test_readme_size_within_limit() -> None:
    payload = (REPO_ROOT / "README.md").read_bytes()
    assert len(payload) <= README_MAX_BYTES, (
        f"README.md занимает {len(payload)} bytes при пределе {README_MAX_BYTES}; "
        "перенесите справочный материал в профильные документы"
    )


def test_code_fences_are_balanced() -> None:
    for rel in DOC_FILES:
        opened = sum(
            1 for line in read(rel).splitlines() if line.lstrip().startswith(FENCE)
        )
        assert opened % 2 == 0, (
            f"{rel.as_posix()}: непарное число ограждений блока кода ({opened})"
        )


@pytest.mark.parametrize("rel", DOC_FILES, ids=lambda p: p.as_posix())
@pytest.mark.parametrize("token", ABSENT_API)
def test_absent_api_not_used_in_examples(rel: Path, token: str) -> None:
    samples = code_blocks(read(rel))
    assert token not in samples, (
        f"{rel.as_posix()}: пример кода вызывает {token} — такой публичной "
        "функции в пакете нет"
    )


@pytest.mark.parametrize("rel", DISCLAIMER_FILES, ids=lambda p: p.as_posix())
@pytest.mark.parametrize("token", ("index_cf()", "rag.rebuild()"))
def test_absent_api_disclaimer_present(rel: Path, token: str) -> None:
    assert token in read(rel), (
        f"{rel.as_posix()} больше не предупреждает про отсутствие {token}; "
        "предупреждение удалять нельзя"
    )


@pytest.mark.parametrize("target", CANONICAL_LINKS)
def test_readme_links_to_canonical_docs(target: str) -> None:
    assert target in read(Path("README.md")), (
        f"README.md не ссылается на канонический документ {target}"
    )


@pytest.mark.parametrize("target", CANONICAL_LINKS)
def test_canonical_doc_exists(target: str) -> None:
    assert (REPO_ROOT / target).is_file(), (
        f"ссылка ведёт на отсутствующий файл: {target}"
    )
