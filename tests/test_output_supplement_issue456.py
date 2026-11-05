"""Дополнение характеристики T1 (#456) по решению 9 ADR #457.

Фиксирует «бит в бит» существующие поля текущего вывода, которые фикстура
``test_output_characterization_issue456.py`` не порождала:

1. ``## SUMMARY`` из ``elem.json`` управляемой формы (имена с символом Cf);
2. токенный режим и выбор ``sections`` у ``to_llm_prompt_fragment``;
3. объекты СКД в отчёте ``v8unpack-agent-run`` и режим ``--mode external``;
4. состояния ``search_context``: усечение, нехватка бюджета, сбой счётчика
   токенов, опущенные кандидаты.

Данные синтетические; эталоны в ``tests/golden_issue456``; нормализации нет.
Время прогона фиксируется подменой ``runner._utc_now`` (решение владельца от
05.11.2026). Пересъём: ``V8UNPACK_REGEN_ISSUE456=1``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from _managed_fixtures import make_managed_form_elem_json, write_managed_form_elem

from v8unpack_agent import cli, runner
from v8unpack_agent.form_context import (
    SECTION_BSL,
    SECTION_FORM,
    SECTION_SUMMARY,
    build_form_context,
    to_llm_prompt_fragment,
)
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_api import (
    HashedBagOfWordsDemoEmbedder,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import build_index

GOLDEN = Path(__file__).parent / "golden_issue456"
REGEN = os.environ.get("V8UNPACK_REGEN_ISSUE456") == "1"
# Смысловое сходство зависит от sum() по float: с Python 3.12 оно компенсированное.
PY_SUFFIX = "" if sys.version_info >= (3, 12) else "_py_lt312"

FIXED_NOW = "2026-01-01T00:00:00.000000Z"
EXPORT = "export"
EXTERNAL = "external"
BOM = b"\xef\xbb\xbf"
ZWSP = chr(0x200B)
RLO = chr(0x202E)
PAGE = f"Страница{ZWSP}1"
FORM_BSL = (
    "// SOURCE TEXT\n"
    "## SUMMARY\n"
    "Процедура ПриОткрытии(Отказ)\n"
    f'\tСообщить("a{RLO}b");\n'
    "КонецПроцедуры\n"
)
SKD_QUERY = f"ВЫБРАТЬ Номенклатура.Ссылка КАК Ссылка{ZWSP} ИЗ Справочник.Номенклатура"
SKD_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<SchemaFile xmlns="" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
    '<dataSet xsi:type="DataSetQuery"><name>НаборЗапрос</name>'
    f"<query>{SKD_QUERY}</query></dataSet>\r\n</SchemaFile>"
)
SKD_BIN = b"\x00\x00\x00\x00\x01\x00\x00\x00" + b"\x00" * 16 + BOM + SKD_XML.encode("utf-8")


def _write(root: Path, relative: str, payload: bytes) -> None:
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _make_export(base: Path) -> None:
    root = base / EXPORT
    payload = make_managed_form_elem_json(pages=[PAGE], with_noise=False)
    elem = write_managed_form_elem(root, "Catalog", "Образец", "ФормаЭлемента", payload)
    (elem.parent / "CatalogForm.obj.bsl").write_bytes(BOM + FORM_BSL.encode("utf-8"))
    _write(root, "Catalog/Образец/Template/Макет/Template.bin", SKD_BIN)


def _make_external(base: Path) -> None:
    root = base / EXTERNAL
    meta = json.dumps({"uuid": "u", "name": "Обработка1"}, ensure_ascii=False)
    _write(root, "Обработка.epf/ExternalDataProcessor.json", meta.encode("utf-8"))
    _write(root, "Обработка.epf/Form/Форма/Form.obj.bsl", BOM + FORM_BSL.encode("utf-8"))


def _check(name: str, actual: bytes) -> None:
    path = GOLDEN / name
    if REGEN:
        GOLDEN.mkdir(exist_ok=True)
        path.write_bytes(actual)
    assert path.read_bytes() == actual, f"вывод отличается от эталона {name}"


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "_utc_now", lambda: FIXED_NOW)
    _make_export(tmp_path)
    _make_external(tmp_path)
    return tmp_path


def _context(base: Path):  # type: ignore[no-untyped-def]
    index = scan_forms(Path(EXPORT))
    assert len(index.forms) == 1
    return build_form_context(index.forms[0], Path(EXPORT))


def _raise(_text: str) -> int:
    raise RuntimeError("counter failed")


def test_prompt_fragment_summary_from_elem(base: Path) -> None:
    text = to_llm_prompt_fragment(_context(base))
    assert PAGE in text
    _check("supp_prompt_fragment_elem.txt", text.encode("utf-8"))


def test_prompt_fragment_sections_and_tokens(base: Path) -> None:
    context = _context(base)
    cases = {
        "supp_prompt_sections_summary_bsl.txt": to_llm_prompt_fragment(
            context, sections=(SECTION_FORM, SECTION_SUMMARY, SECTION_BSL)
        ),
        "supp_prompt_tokens_60.txt": to_llm_prompt_fragment(
            context, max_tokens=60, count_tokens=lambda s: len(s.split())
        ),
        "supp_prompt_tokens_fallback.txt": to_llm_prompt_fragment(
            context, max_chars=300, max_tokens=60, count_tokens=_raise
        ),
    }
    for name, text in cases.items():
        _check(name, text.encode("utf-8"))


def _run_agent(capsys: pytest.CaptureFixture[str], argv: list[str]) -> bytes:
    code = cli.main(argv)
    out = capsys.readouterr()
    return f"exit={code}\n--stdout--\n{out.out}--stderr--\n{out.err}".encode()


def test_agent_run_with_skd(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _check("supp_agent_run_skd.txt", _run_agent(capsys, [EXPORT, "--report-path", "r_skd.json"]))
    _check("supp_run_report_skd.json", Path("r_skd.json").read_bytes())
    argv = [EXPORT, "--report-path", "r_skd_json.json", "--json"]
    _check("supp_agent_run_skd_json.txt", _run_agent(capsys, argv))


def test_agent_run_external(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [EXTERNAL, "--mode", "external", "--report-path", "r_ext.json"]
    _check("supp_agent_run_external.txt", _run_agent(capsys, argv))
    _check("supp_run_report_external.json", Path("r_ext.json").read_bytes())


def test_search_context_states(base: Path) -> None:
    fragmentation = fragment_corpus(collect_search_corpus(Path(EXPORT)))
    embedder = HashedBagOfWordsDemoEmbedder()
    index = build_index(fragmentation, embedder)
    cases = {
        "supp_search_context_truncated.json": {"max_chars": 1600},
        "supp_search_context_zero_budget.json": {"max_chars": 0},
        "supp_search_context_token_failed.json": {
            "max_chars": -1,
            "max_tokens": 100,
            "count_tokens": _raise,
        },
    }
    for name, kwargs in cases.items():
        context = search_with_context(
            fragmentation, "Образец", index=index, embedder=embedder, **kwargs
        )
        _check(name, context.to_json(include_text=True).encode("utf-8"))
    semantic = search_with_context(
        fragmentation,
        "Сообщить ПриОткрытии",
        search_mode="semantic",
        top_k=3,
        index=index,
        embedder=embedder,
        max_chars=700,
    )
    _check(
        f"supp_search_context_semantic_omitted{PY_SUFFIX}.json",
        semantic.to_json(include_text=True).encode("utf-8"),
    )
