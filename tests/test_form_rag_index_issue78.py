"""Тесты FormRagIndex (issue #78, hardening #305).

Контракт D2: ``FormRagIndex(scan_index)``, ``build(contexts, embedder)``,
``query(text, top_k)``, ``save(index_dir)``, ``load(index_dir, embedder)``.

Текст для эмбеддинга строится настоящим ``to_llm_prompt_fragment()``:
без monkeypatch, поэтому проверяются #141 (отрицательное знание),
#142 (санитизация) и #125 (токенный бюджет). Контексты синтетические;
canary-пути собираются из сегментов, разделитель NT задан кодом символа.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from v8unpack_agent.form_context import (
    BSL_MARKER,
    DATA_PATH_LINE_PREFIX,
    SUMMARY_MARKER,
    FormContext,
    _data_path_status_line,
    to_llm_prompt_fragment,
)
from v8unpack_agent.form_rag import (
    _MAX_NPY_BYTES,
    _META_NAME,
    _NPY_ENTRY,
    _NPY_MAGIC,
    _NPY_PREFIX_LEN,
    _NPY_VER,
    _NPZ_NAME,
    _ZIP_DATE,
    FormRagIndex,
    RagBuildError,
    RagError,
    RagLoadError,
    RagQueryError,
    _cosine,
    _decode_matrix,
    _encode_matrix,
)
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex

SEP_NT = chr(92)
TAIL = ["private-root-305", "a", "b", "c", "d", "Form.json"]
CANARIES = (
    "/" + "/".join(["home", "canary-user-305", *TAIL]),
    SEP_NT.join(["C:", "Users", "canary-user-305", *TAIL]),
)
LOCAL_MARKERS = ("canary-user-305", "private-root-305")
BSL_TEXT = (
    "&НаКлиенте\n"
    "Процедура ПриОткрытии(Отказ)\n"
    "    Сообщить(\"demo\");\n"
    "КонецПроцедуры\n"
)
UNRESOLVED = [
    {
        "scope": "element",
        "element": "Поле1",
        "data_path": None,
        "status": "unresolved",
        "reason": "binding_not_proven",
    },
    {
        "scope": "form",
        "element": None,
        "data_path": None,
        "status": "unknown_layout",
        "reason": "layout_not_recognized",
    },
]


# ---------------------------------------------------------------------------
# Синтетические данные
# ---------------------------------------------------------------------------

def _entry(root: Path, form_name: str) -> FormEntry:
    form_dir = root / "Catalog" / "Справочник1" / "CatalogForm" / form_name
    return FormEntry(
        object_type="Catalog",
        object_name="Справочник1",
        container_name="CatalogForm",
        form_name=form_name,
        form_path=form_dir,
        bsl_path=form_dir / "CatalogForm.obj.bsl",
        json_path=form_dir / "CatalogForm.json",
    )


def _scan_index(*entries: FormEntry) -> FormScanIndex:
    return FormScanIndex(forms=list(entries), total=len(entries), scanned_at="")


def _context(entry: FormEntry, *, negative: bool = False) -> FormContext:
    return FormContext(
        form_name=entry.form_name,
        container_name=entry.container_name,
        object_type=entry.object_type,
        object_name=entry.object_name,
        bsl_text=BSL_TEXT,
        summary=FormSummary(
            warnings=[f"Не удалось прочитать {canary}" for canary in CANARIES]
        ),
        metadata={},
        unresolved_data_paths=list(UNRESOLVED) if negative else [],
    )


class Recorder:
    """Эмбеддер: i-й вызов получает ``vectors[i]``, остальные — ``query_vec``.

    Запоминает все полученные тексты.
    """

    def __init__(self, vectors: list[list[float]], query_vec: list[float]) -> None:
        self.vectors = vectors
        self.query_vec = query_vec
        self.texts: list[str] = []

    def __call__(self, text: str) -> list[float]:
        position = len(self.texts)
        self.texts.append(text)
        if position < len(self.vectors):
            return list(self.vectors[position])
        return list(self.query_vec)


def _built(
    tmp_path: Path,
    table: dict[str, list[float]] | None = None,
    query_vec: list[float] | None = None,
) -> tuple[FormRagIndex, FormScanIndex, Recorder]:
    table = table or {
        "A": [1.0, 0.0, 0.0],
        "B": [0.0, 1.0, 0.0],
        "C": [0.0, 0.0, 1.0],
    }
    entries = [_entry(tmp_path, name) for name in table]
    si = _scan_index(*entries)
    rec = Recorder(
        list(table.values()), [0.0, 1.0, 0.0] if query_vec is None else query_vec
    )
    idx = FormRagIndex(si)
    idx.build([_context(e) for e in entries], rec)
    return idx, si, rec


def _const(vector: list[float]) -> Callable[[str], list[float]]:
    def embed(text: str) -> list[float]:
        return list(vector)
    return embed


# ===========================================================================
# 1. Контракт D2: build + query(text)
# ===========================================================================

def test_query_by_text_returns_top_k_by_cosine(tmp_path: Path) -> None:
    idx, si, _ = _built(tmp_path)
    results = idx.query("запрос", top_k=3)
    assert [r.matched[0].form_name for r in results] == ["B", "A", "C"]
    assert results[0].matched == [si.forms[1]]
    assert results[0].confidence == pytest.approx(1.0)
    assert results[1].confidence == pytest.approx(0.0)


def test_query_top_k_limits_results(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    assert len(idx.query("q", top_k=2)) == 2
    assert len(idx.query("q", top_k=100)) == 3


def test_query_default_top_k_is_five(tmp_path: Path) -> None:
    table = {f"F{i}": [1.0, float(i)] for i in range(7)}
    idx, _, _ = _built(tmp_path, table, [1.0, 0.0])
    assert len(idx.query("q")) == 5


def test_tie_break_by_key(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path, {"Z": [1.0, 0.0], "A": [1.0, 0.0]}, [1.0, 0.0])
    assert [r.matched[0].form_name for r in idx.query("q", top_k=2)] == ["A", "Z"]


def test_query_passes_text_to_stored_embedder(tmp_path: Path) -> None:
    idx, _, rec = _built(tmp_path)
    rec.texts.clear()
    rec.vectors = []
    idx.query("найти форму", top_k=1)
    assert rec.texts == ["найти форму"]


def test_query_before_build_raises(tmp_path: Path) -> None:
    idx = FormRagIndex(_scan_index(_entry(tmp_path, "A")))
    with pytest.raises(RagQueryError, match="not ready"):
        idx.query("q")


@pytest.mark.parametrize("top_k", [0, -1, -100, True, 1.5, "3"])
def test_query_top_k_must_be_positive_int(tmp_path: Path, top_k: object) -> None:
    idx, _, _ = _built(tmp_path)
    with pytest.raises(RagQueryError, match="top_k"):
        idx.query("q", top_k=top_k)  # type: ignore[arg-type]


def test_query_text_must_be_str(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    with pytest.raises(RagQueryError, match="must be str"):
        idx.query([0.0, 1.0, 0.0])  # type: ignore[arg-type]


@pytest.mark.parametrize("vec", [[1.0, 0.0], [1.0, 0.0, 0.0, 0.0]])
def test_query_dimension_mismatch_raises(tmp_path: Path, vec: list[float]) -> None:
    idx, _, _ = _built(tmp_path, query_vec=vec)
    with pytest.raises(RagQueryError, match="dimension"):
        idx.query("q")


@pytest.mark.parametrize(
    ("vec", "match"),
    [
        ([], "empty"),
        ([0.0, 0.0, 0.0], "zero norm"),
        ([math.nan, 1.0, 0.0], "NaN or inf"),
        ([math.inf, 1.0, 0.0], "NaN or inf"),
        ([None, 1.0, 0.0], "non-numeric"),
    ],
)
def test_query_invalid_vector_raises(
    tmp_path: Path, vec: list[float], match: str
) -> None:
    idx, _, _ = _built(tmp_path, query_vec=vec)
    with pytest.raises(RagQueryError, match=match):
        idx.query("q")


def test_query_embedder_failure_is_typed(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    calls = {"n": 0}

    def embed(text: str) -> list[float]:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError(CANARIES[0])
        return [1.0, 0.0]

    idx.build([_context(e)], embed)
    with pytest.raises(RagQueryError, match="embedder failed") as info:
        idx.query("q")
    assert CANARIES[0] not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_empty_build_query_returns_empty(tmp_path: Path) -> None:
    idx = FormRagIndex(_scan_index())
    idx.build([], _const([1.0, 0.0]))
    assert idx.query("q") == []


def test_errors_share_base_class() -> None:
    for cls in (RagBuildError, RagLoadError, RagQueryError):
        assert issubclass(cls, RagError)
        assert issubclass(cls, ValueError)


# ===========================================================================
# 2. Строгая валидация векторов при build()
# ===========================================================================

@pytest.mark.parametrize(
    ("vec", "match"),
    [
        ([], "empty"),
        ([0.0, 0.0], "zero norm"),
        ([math.nan, 1.0], "NaN or inf"),
        ([1.0, -math.inf], "NaN or inf"),
        (["1.0", 1.0], "non-numeric"),
        ([True, 1.0], "non-numeric"),
        ("1.0", "must return list"),
        (None, "must return list"),
    ],
)
def test_build_rejects_invalid_vector(
    tmp_path: Path, vec: object, match: str
) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    with pytest.raises(RagBuildError, match=match):
        idx.build([_context(e)], lambda text: vec)  # type: ignore[arg-type,return-value]


def test_build_rejects_inconsistent_dimension(tmp_path: Path) -> None:
    e1, e2 = _entry(tmp_path, "A"), _entry(tmp_path, "B")
    rec = Recorder([[1.0, 0.0], [1.0, 0.0, 0.0]], [1.0, 0.0])
    idx = FormRagIndex(_scan_index(e1, e2))
    with pytest.raises(RagBuildError, match="dimension"):
        idx.build([_context(e1), _context(e2)], rec)


def test_build_accepts_int_values(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    idx.build([_context(e)], _const([1, 0]))  # type: ignore[list-item]
    assert idx.query("q")[0].confidence == pytest.approx(1.0)


def test_build_embedder_failure_is_typed(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))

    def boom(text: str) -> list[float]:
        raise RuntimeError(CANARIES[1])

    with pytest.raises(RagBuildError, match="embedder failed") as info:
        idx.build([_context(e)], boom)
    assert CANARIES[1] not in str(info.value)
    assert info.value.__cause__ is None


def test_build_requires_callable_embedder(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    with pytest.raises(RagBuildError, match="callable"):
        idx.build([_context(e)], None)  # type: ignore[arg-type]


def test_build_raises_on_missing_key(tmp_path: Path) -> None:
    idx = FormRagIndex(_scan_index(_entry(tmp_path, "A")))
    with pytest.raises(RagBuildError, match="not found in scan_index"):
        idx.build([_context(_entry(tmp_path, "B"))], _const([1.0]))


def test_build_raises_on_duplicate_context(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    with pytest.raises(RagBuildError, match="duplicate"):
        idx.build([_context(e), _context(e)], _const([1.0]))


def test_duplicate_key_in_scan_index_raises(tmp_path: Path) -> None:
    with pytest.raises(RagBuildError, match="duplicate form key"):
        FormRagIndex(_scan_index(_entry(tmp_path, "A"), _entry(tmp_path, "A")))


def test_rebuild_replaces_state(tmp_path: Path) -> None:
    idx, si, _ = _built(tmp_path)
    idx.build([_context(si.forms[0])], _const([0.0, 1.0]))
    results = idx.query("q", top_k=5)
    assert len(results) == 1
    assert results[0].matched[0].form_name == "A"


def test_failed_rebuild_keeps_previous_state(tmp_path: Path) -> None:
    idx, si, rec = _built(tmp_path)
    before = [(r.matched[0].form_name, r.confidence) for r in idx.query("q")]
    with pytest.raises(RagBuildError, match="zero norm"):
        idx.build([_context(si.forms[0])], _const([0.0, 0.0, 0.0]))
    after = [(r.matched[0].form_name, r.confidence) for r in idx.query("q")]
    assert after == before
    assert rec.texts[-1] == "q"


# ===========================================================================
# 3. Текст эмбеддинга: настоящий to_llm_prompt_fragment (#141, #142, #125)
# ===========================================================================

def test_build_uses_real_prompt_fragment(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    ctx = _context(e, negative=True)
    rec = Recorder([[1.0, 0.0]], [1.0, 0.0])
    FormRagIndex(_scan_index(e)).build([ctx], rec)
    assert rec.texts == [to_llm_prompt_fragment(ctx)]
    assert rec.texts[0].index(SUMMARY_MARKER) < rec.texts[0].index(BSL_MARKER)


def test_negative_knowledge_reaches_embedder(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    ctx = _context(e, negative=True)
    rec = Recorder([[1.0, 0.0]], [1.0, 0.0])
    idx = FormRagIndex(_scan_index(e))
    idx.build([ctx], rec)
    lines = rec.texts[0].split("\n")
    for item in ctx.unresolved_data_paths:
        assert _data_path_status_line(item) in lines
    assert all(item["data_path"] is None for item in ctx.unresolved_data_paths)
    assert idx.query("q")[0].matched == [e]


def test_sanitization_is_not_bypassed(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    rec = Recorder([[1.0, 0.0]], [1.0, 0.0])
    FormRagIndex(_scan_index(e)).build([_context(e, negative=True)], rec)
    for marker in LOCAL_MARKERS:
        assert marker not in rec.texts[0]


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


@pytest.mark.parametrize("max_tokens", [5, 20, 60])
def test_token_budget_is_respected(tmp_path: Path, max_tokens: int) -> None:
    e = _entry(tmp_path, "A")
    ctx = _context(e, negative=True)
    rec = Recorder([], [1.0, 0.0])
    FormRagIndex(_scan_index(e)).build(
        [ctx], rec, max_chars=400, max_tokens=max_tokens, count_tokens=_tokens
    )
    expected = to_llm_prompt_fragment(
        ctx, 400, max_tokens=max_tokens, count_tokens=_tokens
    )
    assert rec.texts == [expected]
    assert _tokens(rec.texts[0]) <= max_tokens
    assert len(rec.texts[0]) <= 400
    status_lines = {_data_path_status_line(x) for x in ctx.unresolved_data_paths}
    for line in rec.texts[0].split("\n"):
        if line.startswith(DATA_PATH_LINE_PREFIX):
            assert line in status_lines


def test_char_budget_is_respected(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    ctx = _context(e)
    rec = Recorder([], [1.0, 0.0])
    FormRagIndex(_scan_index(e)).build([ctx], rec, max_chars=50)
    assert rec.texts == [to_llm_prompt_fragment(ctx, 50)]
    assert len(rec.texts[0]) <= 50


def test_partial_token_config_is_not_ignored(tmp_path: Path) -> None:
    e = _entry(tmp_path, "A")
    idx = FormRagIndex(_scan_index(e))
    with pytest.raises(ValueError, match="только вместе"):
        idx.build([_context(e)], _const([1.0]), max_tokens=10)


# ===========================================================================
# 4. save / load
# ===========================================================================

def _pairs(idx: FormRagIndex) -> list[tuple[str, float]]:
    return [(r.matched[0].form_name, r.confidence) for r in idx.query("q", top_k=10)]


def test_save_load_roundtrip(tmp_path: Path) -> None:
    idx, si, rec = _built(tmp_path)
    out = tmp_path / "rag"
    idx.save(out)
    restored = FormRagIndex(si)
    restored.load(out, rec)
    assert _pairs(restored) == _pairs(idx)
    assert restored.query("q")[0].matched[0] is si.forms[1]


def test_load_requires_embedder(tmp_path: Path) -> None:
    idx, si, _ = _built(tmp_path)
    idx.save(tmp_path / "rag")
    restored = FormRagIndex(si)
    with pytest.raises(RagLoadError, match="callable"):
        restored.load(tmp_path / "rag", None)  # type: ignore[arg-type]
    with pytest.raises(RagQueryError, match="not ready"):
        restored.query("q")


def test_load_embedder_dimension_checked_on_query(tmp_path: Path) -> None:
    idx, si, _ = _built(tmp_path)
    idx.save(tmp_path / "rag")
    restored = FormRagIndex(si)
    restored.load(tmp_path / "rag", _const([1.0, 0.0]))
    with pytest.raises(RagQueryError, match="dimension"):
        restored.query("q")


def test_save_before_build_raises(tmp_path: Path) -> None:
    idx = FormRagIndex(_scan_index())
    with pytest.raises(RagBuildError, match="not ready"):
        idx.save(tmp_path / "rag")
    assert not (tmp_path / "rag").exists()


def test_empty_save_load(tmp_path: Path) -> None:
    si = _scan_index()
    idx = FormRagIndex(si)
    idx.build([], _const([1.0]))
    idx.save(tmp_path)
    restored = FormRagIndex(si)
    restored.load(tmp_path, _const([1.0]))
    assert restored.query("q") == []


def test_save_is_deterministic(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    idx.save(tmp_path / "a")
    idx.save(tmp_path / "b")
    for name in (_NPZ_NAME, _META_NAME):
        first = (tmp_path / "a" / name).read_bytes()
        assert first == (tmp_path / "b" / name).read_bytes()


def test_npz_is_numpy_compatible_layout(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    idx.save(tmp_path)
    with zipfile.ZipFile(tmp_path / _NPZ_NAME) as zf:
        assert zf.namelist() == [_NPY_ENTRY]
        info = zf.getinfo(_NPY_ENTRY)
        assert info.date_time == _ZIP_DATE
        assert info.compress_type == zipfile.ZIP_STORED
        assert zf.read(_NPY_ENTRY).startswith(_NPY_MAGIC + _NPY_VER)


def test_meta_contains_only_allowed_fields(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    idx.save(tmp_path)
    raw = (tmp_path / _META_NAME).read_text(encoding="utf-8")
    meta = json.loads(raw)
    assert set(meta) == {
        "schema_version", "count", "dimension", "vectors_sha256", "keys",
    }
    assert meta["schema_version"] == 2
    assert meta["count"] == 3
    assert meta["dimension"] == 3
    npz = (tmp_path / _NPZ_NAME).read_bytes()
    assert meta["vectors_sha256"] == hashlib.sha256(npz).hexdigest()
    for forbidden in (str(tmp_path), "Процедура", SUMMARY_MARKER, "bsl", "path"):
        assert forbidden not in raw


# ---------------------------------------------------------------------------
# Атомарность save()
# ---------------------------------------------------------------------------

def _fail_replace_on(monkeypatch: pytest.MonkeyPatch, call_no: int) -> None:
    real = os.replace
    calls = {"n": 0}

    def fake(src: str | Path, dst: str | Path) -> None:
        calls["n"] += 1
        if calls["n"] == call_no:
            raise OSError("simulated failure")
        real(src, dst)

    monkeypatch.setattr(os, "replace", fake)


def _leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


def test_save_failure_before_replace_keeps_old_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx, si, rec = _built(tmp_path)
    out = tmp_path / "rag"
    idx.save(out)
    old = {n: (out / n).read_bytes() for n in (_NPZ_NAME, _META_NAME)}

    idx.build([_context(si.forms[0])], _const([0.0, 1.0]))
    _fail_replace_on(monkeypatch, 1)
    with pytest.raises(OSError, match="simulated"):
        idx.save(out)
    monkeypatch.undo()

    assert {n: (out / n).read_bytes() for n in old} == old
    assert _leftovers(out) == []
    restored = FormRagIndex(si)
    restored.load(out, rec)
    assert len(restored.query("q", top_k=10)) == 3


def test_save_failure_between_replaces_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx, si, rec = _built(tmp_path)
    out = tmp_path / "rag"
    idx.save(out)

    idx.build([_context(si.forms[0])], _const([0.0, 1.0]))
    _fail_replace_on(monkeypatch, 2)
    with pytest.raises(OSError, match="simulated"):
        idx.save(out)
    monkeypatch.undo()

    assert _leftovers(out) == []
    with pytest.raises(RagLoadError, match="does not match"):
        FormRagIndex(si).load(out, rec)


def test_first_save_failure_leaves_no_valid_looking_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx, si, rec = _built(tmp_path)
    out = tmp_path / "rag"
    _fail_replace_on(monkeypatch, 2)
    with pytest.raises(OSError, match="simulated"):
        idx.save(out)
    monkeypatch.undo()
    assert not (out / _META_NAME).exists()
    assert _leftovers(out) == []
    with pytest.raises(RagLoadError, match="is missing"):
        FormRagIndex(si).load(out, rec)


# ---------------------------------------------------------------------------
# Типизированный load(): все ветки ошибок
# ---------------------------------------------------------------------------

def _write_pair(
    directory: Path,
    matrix: list[list[float]],
    keys: list[list[str]],
    *,
    npy: bytes | None = None,
    npz: bytes | None = None,
    overrides: dict[str, object] | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if npz is None:
        path = directory / _NPZ_NAME
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(
                zipfile.ZipInfo(_NPY_ENTRY, date_time=_ZIP_DATE),
                npy if npy is not None else _encode_matrix(matrix),
            )
        npz = path.read_bytes()
    else:
        (directory / _NPZ_NAME).write_bytes(npz)
    meta: dict[str, object] = {
        "schema_version": 2,
        "count": len(keys),
        "dimension": len(matrix[0]) if matrix else 0,
        "vectors_sha256": hashlib.sha256(npz).hexdigest(),
        "keys": keys,
    }
    meta.update(overrides or {})
    (directory / _META_NAME).write_text(json.dumps(meta), encoding="utf-8")


def _key(e: FormEntry) -> list[str]:
    return [e.object_type, e.object_name, e.container_name, e.form_name]


def _assert_safe_load_error(
    idx: FormRagIndex, directory: Path, match: str, prefix: str = ""
) -> None:
    with pytest.raises(RagLoadError, match=match) as info:
        idx.load(directory, _const([1.0, 0.0]))
    message = str(info.value)
    assert message.startswith(prefix)
    assert str(directory) not in message
    assert directory.name not in message
    assert "Error" not in message
    assert info.value.__cause__ is None
    with pytest.raises(RagQueryError, match="not ready"):
        idx.query("q")


@pytest.fixture
def pair(tmp_path: Path) -> tuple[FormRagIndex, Path, FormEntry]:
    e = _entry(tmp_path, "A")
    return FormRagIndex(_scan_index(e)), tmp_path / "load-dir-305", e


def test_load_missing_meta(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, _ = pair
    d.mkdir()
    _assert_safe_load_error(idx, d, "is missing", prefix=_META_NAME)


def test_load_missing_npz(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)])
    (d / _NPZ_NAME).unlink()
    _assert_safe_load_error(idx, d, "is missing", prefix=_NPZ_NAME)


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00", b""])
def test_load_broken_json(
    pair: tuple[FormRagIndex, Path, FormEntry], raw: bytes
) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)])
    (d / _META_NAME).write_bytes(raw)
    _assert_safe_load_error(idx, d, "not valid JSON")


def test_load_meta_not_object(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)])
    (d / _META_NAME).write_text("[1, 2]", encoding="utf-8")
    _assert_safe_load_error(idx, d, "invalid structure")


def test_load_broken_zip(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)], npz=b"PK\x03\x04 broken archive")
    _assert_safe_load_error(idx, d, "not a valid archive")


def test_load_extra_zip_entries(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    d.mkdir()
    path = d / "scratch.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_NPY_ENTRY, _encode_matrix([[1.0, 0.0]]))
        zf.writestr("extra.npy", b"x")
    npz = path.read_bytes()
    path.unlink()
    _write_pair(d, [[1.0, 0.0]], [_key(e)], npz=npz)
    _assert_safe_load_error(idx, d, "unexpected entries")


@pytest.mark.parametrize("schema", [1, 3, 99, "2", None, True])
def test_load_unknown_schema(
    pair: tuple[FormRagIndex, Path, FormEntry], schema: object
) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)], overrides={"schema_version": schema})
    _assert_safe_load_error(idx, d, "schema_version")


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"count": 2}, "count does not match keys"),
        ({"count": -1}, "field count"),
        ({"count": "1"}, "field count"),
        ({"dimension": True}, "field dimension"),
        ({"dimension": 0}, "inconsistent"),
        ({"dimension": 3}, "dimension does not match"),
        ({"vectors_sha256": "0" * 64}, "does not match"),
        ({"vectors_sha256": 5}, "vectors_sha256"),
        ({"keys": "A"}, "field keys"),
        ({"keys": [["Catalog", "X"]]}, "field keys"),
        ({"keys": [[1, 2, 3, 4]]}, "field keys"),
        (
            {"keys": [["Catalog", "Нет", "CatalogForm", "A"]]},
            "not found in scan_index",
        ),
    ],
)
def test_load_meta_mismatch(
    pair: tuple[FormRagIndex, Path, FormEntry],
    overrides: dict[str, object],
    match: str,
) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0]], [_key(e)], overrides=overrides)
    _assert_safe_load_error(idx, d, match)


def test_load_duplicate_keys(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0], [0.0, 1.0]], [_key(e), _key(e)])
    _assert_safe_load_error(idx, d, "duplicate keys")


def test_load_cardinality_mismatch(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    _write_pair(d, [[1.0, 0.0], [0.0, 1.0]], [_key(e)])
    _assert_safe_load_error(idx, d, "row count")


@pytest.mark.parametrize("row", [[math.nan, 1.0], [0.0, 0.0], [math.inf, 0.0]])
def test_load_invalid_stored_vector(
    pair: tuple[FormRagIndex, Path, FormEntry], row: list[float]
) -> None:
    idx, d, e = pair
    _write_pair(d, [row], [_key(e)])
    _assert_safe_load_error(idx, d, "NPY row")


def test_load_wrong_dtype(pair: tuple[FormRagIndex, Path, FormEntry]) -> None:
    idx, d, e = pair
    npy = _encode_matrix([[1.0, 0.0]]).replace(b"<f8", b"<f4")
    _write_pair(d, [[1.0, 0.0]], [_key(e)], npy=npy)
    _assert_safe_load_error(idx, d, "dtype")


def test_failed_load_resets_previous_state(tmp_path: Path) -> None:
    idx, _, _ = _built(tmp_path)
    (tmp_path / "empty").mkdir()
    with pytest.raises(RagLoadError, match="is missing"):
        idx.load(tmp_path / "empty", _const([1.0, 0.0, 0.0]))
    with pytest.raises(RagQueryError, match="not ready"):
        idx.query("q")


# ===========================================================================
# 5. Fail-closed NPY reader
# ===========================================================================

def test_decode_wrong_magic_raises() -> None:
    with pytest.raises(RagLoadError, match="magic"):
        _decode_matrix(b"\x00NUMPY\x01\x00" + b"\x00" * 100)


def test_decode_wrong_version_raises() -> None:
    with pytest.raises(RagLoadError, match="magic"):
        _decode_matrix(b"\x93NUMPY\x02\x00" + b"\x00" * 100)


def test_decode_truncated_header_raises() -> None:
    data = _NPY_MAGIC + _NPY_VER + (500).to_bytes(2, "little") + b"{}"
    with pytest.raises(RagLoadError, match="header"):
        _decode_matrix(data)


def test_decode_body_size_mismatch_raises() -> None:
    with pytest.raises(RagLoadError, match="body size"):
        _decode_matrix(_encode_matrix([[1.0, 2.0]])[:-8])


def test_decode_non_2d_raises() -> None:
    data = _encode_matrix([[1.0, 2.0]]).replace(b"(1, 2)", b"(2,)  ")
    with pytest.raises(RagLoadError, match="2D"):
        _decode_matrix(data)


def test_decode_size_limit_raises() -> None:
    with pytest.raises(RagLoadError, match="size"):
        _decode_matrix(b"\x93NUMPY\x01\x00" + b"\x00" * _MAX_NPY_BYTES)


def test_encode_header_is_64_aligned() -> None:
    data = _encode_matrix([[1.0, 2.0, 3.0]])
    hlen = int.from_bytes(data[8:10], "little")
    assert (_NPY_PREFIX_LEN + hlen) % 64 == 0


def test_encode_decode_symmetry() -> None:
    matrix = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert _decode_matrix(_encode_matrix(matrix)) == matrix


def test_encode_decode_empty() -> None:
    assert _decode_matrix(_encode_matrix([])) == []


# ===========================================================================
# 6. Cosine
# ===========================================================================

def test_cosine_identical() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_rejects_dimension_mismatch() -> None:
    with pytest.raises(RagQueryError, match="dimensions"):
        _cosine([1.0, 0.0, 5.0], [1.0, 0.0])


# ===========================================================================
# 7. Ленивый импорт и экспорт
# ===========================================================================

def test_form_rag_does_not_import_heavy_modules_eagerly() -> None:
    code = (
        "import json, sys; import v8unpack_agent.form_rag; "
        "names = ('v8unpack_agent.scan_forms', 'v8unpack_agent.form_context'); "
        "print(json.dumps([m for m in names if m in sys.modules]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert json.loads(result.stdout) == []


def test_form_rag_index_in_all() -> None:
    import v8unpack_agent
    assert "FormRagIndex" in v8unpack_agent.__all__


def test_form_rag_index_resolves_from_root() -> None:
    import v8unpack_agent
    assert v8unpack_agent.FormRagIndex is FormRagIndex
