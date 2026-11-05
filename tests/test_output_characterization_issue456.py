"""Характеристика текущего LLM-вывода на синтетической выгрузке (issue #456).

Фиксирует «бит в бит» текущий вывод поверхностей инварианта 3 эпика #455 и
каталог ``--describe-cli`` (инвариант 4) до любых изменений продуктового кода.
Фикстура содержит символы Unicode категории Cf, файловый BOM и строку,
совпадающую по виду со служебным маркером (``SOURCE TEXT``). Их наличие —
только входные данные; обнаружение Cf/BOM и схема представления — T3/T2.

Эталоны лежат в ``tests/golden_issue456`` и сравниваются побайтно, без
нормализации. Время прогона ``v8unpack-agent-run`` фиксируется подменой
``runner._utc_now`` (решение владельца от 05.11.2026); отчёт schema 1
строится конструктором с явными ``started_at``/``finished_at``. Пути
синтетические и относительные: тесты работают из ``tmp_path``.

Пересъём эталонов: ``V8UNPACK_REGEN_ISSUE456=1 python -m pytest
tests/test_output_characterization_issue456.py`` — только осознанно, с
проверкой diff эталонов.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from v8unpack_agent import cli, runner, search_cli
from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.module_projection import to_llm_module_fragment
from v8unpack_agent.modules import ModuleEntry
from v8unpack_agent.run_report import PostRunReport, RunSummary
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_api import collect_search_corpus, search_with_context
from v8unpack_agent.search_fragments import fragment_corpus

GOLDEN = Path(__file__).parent / "golden_issue456"
REGEN = os.environ.get("V8UNPACK_REGEN_ISSUE456") == "1"

FIXED_NOW = "2026-01-01T00:00:00.000000Z"
EXPORT = "export"
BOM = b"\xef\xbb\xbf"
ZWSP = chr(0x200B)
ZWJ = chr(0x200D)
RLO = chr(0x202E)
SOFT_HYPHEN = chr(0x00AD)
FORM_DIR = ("Catalog", "SampleObject", "ItemForm", "Variant1")
MODULE_DIR = ("CommonModule", "SharedHelpers")
MODULE_PATH = "CommonModule/SharedHelpers/CommonModule.obj.bsl"

FORM_BSL = (
    "// SOURCE TEXT\n"
    "## BSL\n"
    "Процедура Тест()\n"
    f'\tСообщить("a{ZWSP}b{RLO}c{SOFT_HYPHEN}d");\n'
    "КонецПроцедуры\n"
)
MODULE_BSL = (
    "// SOURCE TEXT\n"
    "MODULE\n"
    "Функция Ф() Экспорт\n"
    f'\tВозврат "x{ZWJ}y";\n'
    "КонецФункции\n"
)


def _make_export(base: Path) -> Path:
    """Синтетическая выгрузка: форма и общий модуль, оба с BOM и символами Cf."""
    root = base / EXPORT
    form_dir = root.joinpath(*FORM_DIR)
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_bytes(BOM + FORM_BSL.encode("utf-8"))
    module_dir = root.joinpath(*MODULE_DIR)
    module_dir.mkdir(parents=True)
    (module_dir / "CommonModule.obj.bsl").write_bytes(BOM + MODULE_BSL.encode("utf-8"))
    return root


def _check(name: str, actual: bytes) -> None:
    path = GOLDEN / name
    if REGEN:
        GOLDEN.mkdir(exist_ok=True)
        path.write_bytes(actual)
    assert path.read_bytes() == actual, f"вывод отличается от эталона {name}"


@pytest.fixture
def export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_utc_now", lambda: FIXED_NOW)
    _make_export(tmp_path)
    return Path(EXPORT)


def test_fixture_contains_cf_bom_and_marker(export: Path) -> None:
    form = export.joinpath(*FORM_DIR, "ItemForm.obj.bsl").read_bytes()
    module = export.joinpath(*MODULE_DIR, "CommonModule.obj.bsl").read_bytes()
    for data in (form, module):
        assert data.startswith(BOM)
        assert b"SOURCE TEXT" in data
    assert ZWSP.encode("utf-8") in form and RLO.encode("utf-8") in form
    assert ZWJ.encode("utf-8") in module


def test_to_llm_prompt_fragment(export: Path) -> None:
    index = scan_forms(export)
    assert len(index.forms) == 1
    context = build_form_context(index.forms[0], export)
    _check("prompt_fragment_full.txt", to_llm_prompt_fragment(context).encode("utf-8"))
    _check(
        "prompt_fragment_max200.txt",
        to_llm_prompt_fragment(context, max_chars=200).encode("utf-8"),
    )


def test_to_llm_module_fragment(export: Path) -> None:
    entry = ModuleEntry(
        module_kind="common_module",
        owner_kind="common_module",
        metadata_type="CommonModule",
        owner_name="SharedHelpers",
        relative_path=MODULE_PATH,
        read_status="ok",
    )
    text = (export / MODULE_PATH).read_bytes().decode("utf-8")
    full = to_llm_module_fragment(entry, text)
    cut = to_llm_module_fragment(entry, text, max_chars=200)
    _check("module_fragment_full.txt", full.text.encode("utf-8"))
    _check("module_fragment_max200.txt", cut.text.encode("utf-8"))
    assert (full.truncated, cut.truncated) == (False, True)


def test_search_context(export: Path) -> None:
    fragmentation = fragment_corpus(collect_search_corpus(export))
    context = search_with_context(fragmentation, "SharedHelpers", max_chars=-1)
    _check("search_context.json", context.to_json().encode("utf-8"))
    _check("search_context_text.json", context.to_json(include_text=True).encode("utf-8"))


def _run_search_cli(capsys: pytest.CaptureFixture[str], argv: list[str]) -> bytes:
    code = search_cli.main(argv)
    out = capsys.readouterr()
    return f"exit={code}\n--stdout--\n{out.out}--stderr--\n{out.err}".encode()


def test_search_cli(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build = _run_search_cli(
        capsys,
        ["build", str(export), "--snapshot", "snap.json", "--embedder", "hashed-bow-demo"],
    )
    _check("search_cli_build.txt", build)
    for name, extra in (
        ("search_cli_query_exact.txt", ["--query", "SharedHelpers"]),
        ("search_cli_query_text.txt", ["--query", "SharedHelpers", "--include-text"]),
        ("search_cli_query_semantic.txt", ["--query", "Сообщить", "--search-mode", "semantic"]),
    ):
        argv = ["query", str(export), "--snapshot", "snap.json", "--embedder", "hashed-bow-demo"]
        _check(name, _run_search_cli(capsys, argv + extra))


def _run_agent(capsys: pytest.CaptureFixture[str], argv: list[str]) -> bytes:
    code = cli.main(argv)
    out = capsys.readouterr()
    return f"exit={code}\n--stdout--\n{out.out}--stderr--\n{out.err}".encode()


def test_agent_run_text_and_report(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _check(
        "agent_run_text.txt",
        _run_agent(capsys, [str(export), "--report-path", "report_text.json"]),
    )
    _check("run_report_schema2.json", Path("report_text.json").read_bytes())


def test_agent_run_module_index(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [str(export), "--report-path", "report_mi.json", "--include-module-index"]
    _check("agent_run_module_index.txt", _run_agent(capsys, argv))
    _check("run_report_schema2_module_index.json", Path("report_mi.json").read_bytes())


def test_agent_run_json_envelope(export: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [str(export), "--report-path", "report_json.json", "--json"]
    _check("agent_run_json.txt", _run_agent(capsys, argv))


def test_run_report_schema1() -> None:
    report = PostRunReport(
        schema_version=1,
        completed=True,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        summary=RunSummary.from_objects(()),
        objects=(),
    )
    _check("run_report_schema1.json", report.to_json().encode("utf-8"))


def test_describe_cli(capsys: pytest.CaptureFixture[str]) -> None:
    _check("describe_cli.txt", _run_agent(capsys, ["--describe-cli"]))
