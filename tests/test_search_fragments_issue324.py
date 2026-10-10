"""Синтетические границы, координаты и сохранность текста (#324).

Файловые fixtures используют write_bytes, не Windows text mode.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerLink,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    TextSpan,
)
from v8unpack_agent.search_corpus import CorpusItem, CorpusReport, SearchCorpus
from v8unpack_agent.search_fragments import (
    FragmentationResult,
    FragmentParameters,
    FragmentReason,
    fragment_corpus,
    fragment_module,
    verify_fragmentation,
)

OWNER = OwnerRef("metadata_object", "Catalog", "РеализацияТоваров")
ARTIFACT = ArtifactRef("module", OWNER, "object")


def checked(text: str, **kwargs: int) -> FragmentationResult:
    parameters = FragmentParameters(**kwargs)
    result = fragment_module(ARTIFACT, text, parameters=parameters)
    verify_fragmentation(result, {ARTIFACT.artifact_id: text})
    assert result == fragment_module(ARTIFACT, text, parameters=parameters)
    assert result.to_json(include_text=True) == fragment_module(
        ARTIFACT, text, parameters=parameters).to_json(include_text=True)
    normalized = text.removeprefix(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")
    assert result.items[0].source_sha256 == hashlib.sha256(normalized.encode()).hexdigest()
    for fragment in result.fragments:
        span = fragment.document.span
        assert span is not None
        lines = normalized.split("\n")
        expected = "\n".join(lines[span.start_line - 1:span.end_line])
        if span.end_line < len(lines):
            expected += "\n"
        assert fragment.document.text == expected
    return result


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", ["", chr(0xFEFF)])
def test_coordinates_comments_annotations_outside(eol: str, bom: str) -> None:
    text = bom + eol.join([
        "#Область Тест", "Перем Счетчик;", "", "// Описание", "&НаКлиенте",
        "Процедура ПриЗаписи()", "  Счетчик = 1;", "КонецПроцедуры",
        "", "Функция Значение() Экспорт", "Возврат 1;", "КонецФункции",
        "#КонецОбласти", "",
    ])
    result = checked(text)
    procs = {d.procedure: d for d in result.documents.documents if d.procedure is not None}
    assert procs["ПриЗаписи"].span == TextSpan(4, 8)
    assert procs["Значение"].span == TextSpan(10, 12)
    assert {d.span for d in result.documents.documents if d.procedure is None} == {
        TextSpan(1, 3), TextSpan(13, 13)}
    assert result.items[0].omitted_blank_lines == (9,)
    assert procs["ПриЗаписи"].document_id.endswith("#ПриЗаписи")


@pytest.mark.parametrize("text", ["", chr(0xFEFF), " \t", "\n\n", " \r\n \r"])
def test_empty_module_explicit_report(text: str) -> None:
    result = checked(text)
    assert not result.fragments
    assert result.items[0].covered_lines == 0
    assert len(result.items[0].omitted_blank_lines) == result.items[0].total_lines
    assert not result.items[0].fallback


def test_comments_and_strings_no_false_boundaries() -> None:
    text = '// Процедура Ложная()\nПерем А;\nПроцедура Истинная()\nС = "КонецПроцедуры // не комментарий ""кавычки""";\nС = "начало\n|Процедура Ложная2()\n|КонецФункции\n|конец";\n// КонецПроцедуры\nКонецПроцедуры\n'
    result = checked(text)
    assert [f.procedure for f in result.fragments if f.procedure] == ["Истинная"]
    assert not result.items[0].fallback
    assert next(f for f in result.fragments if f.procedure).document.span == TextSpan(3, 10)


@pytest.mark.parametrize("opening,closing", [
    ("aSyNc pRoCeDuRe DoWork()", "eNdPrOcEdUrE"),
    ("Async Function GetValue()", "EndFunction; // comment"),
    ("Асинх Функция Получить()", "КонецФункции"),
])
def test_bilingual_async(opening: str, closing: str) -> None:
    result = checked(opening + "\nВозврат 1;\n" + closing)
    assert result.fragments[0].kind == "procedure"


@pytest.mark.parametrize("text,reason", [
    ("Процедура А()\nА = 1;", FragmentReason.UNCLOSED_PROCEDURE),
    ("КонецПроцедуры\nА = 1;", FragmentReason.UNEXPECTED_END),
    ("Процедура А()\nКонецФункции", FragmentReason.MISMATCHED_END),
    ("Процедура А()\nПроцедура Б()\nКонецПроцедуры", FragmentReason.NESTED_DECLARATION),
    ("Процедура 1А()\nКонецПроцедуры", FragmentReason.INVALID_DECLARATION),
    ("Функция\nКонецФункции", FragmentReason.INVALID_DECLARATION),
    ('А = "начало', FragmentReason.UNCLOSED_STRING),
    ('А = "начало\nПроцедура А()', FragmentReason.INVALID_STRING_CONTINUATION),
    ("Процедура А()\nКонецПроцедуры Еще();", FragmentReason.INVALID_END),
    ("Процедура А() КонецПроцедуры", FragmentReason.UNCLOSED_PROCEDURE),
])
def test_whole_module_fallback(text: str, reason: FragmentReason) -> None:
    result = checked(text, max_chars=20)
    assert result.items[0].reasons == (reason,)
    assert result.items[0].fallback
    assert all(f.fallback and f.reasons == (reason,) for f in result.fragments)
    assert all(f.document.procedure is None and f.procedure is None for f in result.fragments)


@pytest.mark.parametrize("names", [("Тест", "тЕСТ"), ("Й", "И" + chr(0x306))])
def test_duplicate_names_all_occurrences_line_ids(names: tuple[str, str]) -> None:
    text = "\n".join(f"Процедура {name}()\nКонецПроцедуры" for name in names)
    result = checked(text)
    assert len(result.documents) == 2
    assert {f.procedure for f in result.fragments} == set(names)
    assert all(f.document.procedure is None for f in result.fragments)
    assert all(f.reasons == (FragmentReason.DUPLICATE_PROCEDURE,) for f in result.fragments)
    assert not result.items[0].fallback


def test_large_procedure_windows_overlap_tail() -> None:
    text = "Процедура Большая()\n" + "Значение = 123456;\n" * 250 + "КонецПроцедуры\n"
    result = checked(text)
    assert len(result.fragments) > 1
    assert all(f.procedure == "Большая" and f.document.procedure is None for f in result.fragments)
    assert all(len(f.document.text) <= 2000 for f in result.fragments)
    ordered = sorted(result.fragments, key=lambda f: f.document.span.start_line)
    assert ordered[-1].document.span.end_line == 252
    for left, right in pairwise(ordered):
        assert left.document.span.end_line - right.document.span.start_line + 1 == 3
    assert result.items[0].covered_lines == 252
    assert result.items[0].repeated_lines > 0


@pytest.mark.parametrize("limit", [1, 15, 50, 120])
@pytest.mark.parametrize("overlap", [0, 1, 3, 100])
def test_windows_progress_and_oversized_line(limit: int, overlap: int) -> None:
    text = "Начало = 1;\n" + " " * 30 + "\n" + "Я" * 300 + ";\n" + "Конец = 2;\n" * 30
    result = checked(text, max_chars=limit, overlap_lines=overlap)
    assert any(f.oversized_line for f in result.fragments)
    for f in result.fragments:
        if len(f.document.text) > limit:
            assert f.oversized_line and f.document.span.start_line == f.document.span.end_line
    assert not any(i.to_dict()["truncated"] for i in result.items)


def test_projection_not_source_or_ids() -> None:
    text = 'Процедура РеализацияТоваров()\nТекст = "HTTPЗапрос";\nКонецПроцедуры\n'
    result = checked(text)
    f = result.fragments[0]
    assert f.document.text == text
    assert f.document.document_id.endswith("#РеализацияТоваров")
    assert "реализация товаров" in f.search_text and "http запрос" in f.search_text
    assert f.document.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    safe = json.loads(result.to_json())
    assert "text" not in safe["fragments"][0]["document"]
    assert "search_text" not in safe["fragments"][0]
    assert SearchDocumentSet.from_dict(result.documents.to_dict(include_text=True)) == result.documents


def test_parameters_version() -> None:
    default = FragmentParameters()
    expected = hashlib.sha256((json.dumps(default.to_dict(), ensure_ascii=False,
        sort_keys=True, indent=2, allow_nan=False) + "\n").encode()).hexdigest()
    assert default.representation_version == expected
    assert len({default.representation_version, FragmentParameters(2001).representation_version,
        FragmentParameters(overlap_lines=0).representation_version}) == 3


@pytest.mark.parametrize("kwargs,exception", [
    ({"max_chars": 0}, ValueError), ({"overlap_lines": -1}, ValueError),
    ({"max_chars": True}, TypeError), ({"max_chars": 2.5}, TypeError),
])
def test_invalid_parameters(kwargs: dict, exception: type[Exception]) -> None:
    with pytest.raises(exception):
        FragmentParameters(**kwargs)


def test_corpus_cards_reports_links_and_determinism() -> None:
    form = ArtifactRef.form("CommonForm", "", "CommonForm", "Список")
    card = ArtifactRef.owner_card(OWNER)
    missing = CorpusItem("form:CommonForm//CommonForm/БезМодуля", "form", "forms", None, False, "missing")
    link = OwnerLink(OWNER, form, "unconfirmed")
    docs = [SearchDocument(ARTIFACT, "Процедура А()\nКонецПроцедуры\n"),
        SearchDocument(form, "А = 1;\n"), SearchDocument(card, 'OWNER\nПроцедура Fake()\n')]
    corpus = SearchCorpus(SearchDocumentSet(tuple(docs)), (link,), CorpusReport((missing,)))
    before = corpus.documents.to_json(include_text=True) + corpus.to_json()
    result = fragment_corpus(corpus)
    verify_fragmentation(result, {d.artifact.artifact_id: d.text for d in docs})
    assert result.corpus_report is corpus.report
    assert result.owner_links == corpus.owner_links
    assert result.owner_links[0].basis == "unconfirmed"
    assert before == corpus.documents.to_json(include_text=True) + corpus.to_json()
    f = next(f for f in result.fragments if f.kind == "owner_card")
    assert f.document is docs[-1] and f.search_text == docs[-1].text
    assert not f.fallback
    assert result.documents.artifacts() == corpus.documents.artifacts()
    random.Random(324).shuffle(docs)
    other = fragment_corpus(SearchCorpus(SearchDocumentSet(tuple(docs)), (link,), corpus.report))
    assert result.to_json(include_text=True) == other.to_json(include_text=True)


def test_corpus_integration_byte_fixture(tmp_path: Path) -> None:
    from v8unpack_agent.modules import ModuleEntry, ModuleIndex
    from v8unpack_agent.search_corpus import build_search_corpus

    path = tmp_path / "demo.bsl"
    payload = (chr(0xFEFF) + "Процедура А()\r\nКонецПроцедуры\r\n").encode()
    path.write_bytes(payload)
    entry = ModuleEntry("object", "metadata_object", "Catalog", "Demo", "demo.bsl", "ok",
        len(payload), hashlib.sha256(payload).hexdigest())
    corpus = build_search_corpus(tmp_path, module_index=ModuleIndex.from_entries([entry]))
    result = fragment_corpus(corpus)
    verify_fragmentation(result, {d.artifact.artifact_id: d.text for d in corpus.documents.documents})
    assert result.corpus_report is corpus.report
    assert any(f.document.procedure == "А" for f in result.fragments)


def test_verifier_rejects_corruption() -> None:
    text = "Процедура А()\nКонецПроцедуры\n"
    result = checked(text)
    f = result.fragments[0]
    sources = {ARTIFACT.artifact_id: text}
    cases = [replace(result, fragments=()),
        replace(result, fragments=(replace(f, document=replace(f.document, text="другой текст")),)),
        replace(result, fragments=(replace(f, document=replace(f.document, span=TextSpan(1, 3))),)),
        replace(result, fragments=(replace(f, search_text="другой поиск"),)),
        replace(result, fragments=(replace(f, oversized_line=True),)),
        replace(result, items=(replace(result.items[0], repeated_lines=2),)),
        replace(result, items=(replace(result.items[0], fallback=True),))]
    for bad in cases:
        with pytest.raises(ValueError):
            verify_fragmentation(bad, sources)
    with pytest.raises(ValueError):
        verify_fragmentation(result, {ARTIFACT.artifact_id: text + "\n"})
    with pytest.raises(ValueError):
        verify_fragmentation(result, {})


def test_fragmented_corpus_rejected() -> None:
    corpus = SearchCorpus(checked("А = 1;\n").documents, (), CorpusReport())
    with pytest.raises(ValueError, match="whole artifacts"):
        fragment_corpus(corpus)


def test_multiline_signature_and_empty_strings() -> None:
    text = 'Процедура Тест(\nА,\nБ) Экспорт\nС = "";\nС = """";\nКонецПроцедуры\n'
    result = checked(text)
    assert result.fragments[0].document.span == TextSpan(1, 6)
    assert result.fragments[0].procedure == "Тест"


def test_no_phantom_trailing_line_and_lf_only_separator() -> None:
    assert checked("А = 1;\n").items[0].total_lines == 1
    assert checked("А = 1;\n\n").items[0].total_lines == 2
    assert checked("А = 1;" + chr(0x2028) + "Б = 2;").items[0].total_lines == 1


def test_random_synthetic_windows_balance() -> None:
    rng = random.Random(324)
    for _ in range(100):
        text = "".join((" " if rng.randrange(4) == 0 else "А=" + "1" * rng.randrange(100) + ";")
            + "\n" for _ in range(rng.randrange(1, 50)))
        checked(text, max_chars=rng.randrange(1, 150), overlap_lines=rng.randrange(30))


def test_docs_changelog_and_root_surface() -> None:
    import v8unpack_agent

    root = Path(__file__).resolve().parents[1]
    assert "fragment_corpus" not in v8unpack_agent.__all__
    assert "docs/search_fragments.md" in (root / "README.md").read_text(encoding="utf-8")
    doc = (root / "docs" / "search_fragments.md").read_text(encoding="utf-8")
    assert all(name in doc for name in ("fragment_module", "fragment_corpus", "verify_fragmentation"))
    assert "(#324)" in (root / "CHANGELOG.md").read_text(encoding="utf-8")
