"""Тесты FormRagIndex (issue #78).

1. build/query: top_k результатов по cosine similarity
2. save/load: round-trip детерминированный
3. determinism: байты npz совпадают при повторном save
4. fail-closed reader: dtype, magic, shape — ошибка до выделения памяти
5. Пустой индекс: query возвращает []
6. RagBuildError при дубликате ключа и отсутствующем ключе
7. RagLoadError при несоответствии ключей scan_index
8. Ленивый импорт: form_rag не тянет scan_forms до обращения
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from v8unpack_agent.form_rag import (
    _META_NAME,
    _NPY_ENTRY,
    _NPZ_NAME,
    _ZIP_DATE,
    FormRagIndex,
    RagBuildError,
    RagLoadError,
    _cosine,
    _decode_matrix,
    _encode_matrix,
)
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

def _entry(
    object_type: str = "Catalog",
    object_name: str = "Контрагенты",
    container_name: str = "CatalogForm",
    form_name: str = "ФормаСписка",
    root: Path | None = None,
) -> FormEntry:
    """Минимальный FormEntry с реальными Path-полями."""
    base = root or Path("/tmp/fake")
    form_dir = base / object_type / object_name / container_name / form_name
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container_name,
        form_name=form_name,
        form_path=form_dir,
        bsl_path=form_dir / f"{container_name}.obj.bsl",
        json_path=form_dir / f"{container_name}.json",
    )


def _scan_index(*entries: FormEntry) -> FormScanIndex:
    return FormScanIndex(forms=list(entries), total=len(entries), scanned_at="")


def _embedder_const(dim: int = 4) -> object:
    """Embedder, возвращающий вектор из единиц."""
    def embed(text: str) -> list[float]:
        return [1.0] * dim
    return embed


# ---------------------------------------------------------------------------
# Простой FormContext-заглушка для build()
# ---------------------------------------------------------------------------

@dataclass
class _FakeCtx:
    object_type: str
    object_name: str
    container_name: str
    form_name: str
    bsl_text: str | None = None
    _prompt: str = field(default="stub", repr=False)


def _contexts_for(*entries: FormEntry) -> list[_FakeCtx]:
    return [
        _FakeCtx(
            object_type=e.object_type,
            object_name=e.object_name,
            container_name=e.container_name,
            form_name=e.form_name,
        )
        for e in entries
    ]


def _embedder_unique(dim: int = 3) -> object:
    """Каждый вызов возвращает уникальный вектор (счётчик в первом элементе)."""
    state = {"n": 0}

    def embed(text: str) -> list[float]:
        n = state["n"]
        state["n"] += 1
        vec = [0.0] * dim
        vec[n % dim] = 1.0
        return vec

    return embed


# ---------------------------------------------------------------------------
# Тест: обход to_llm_prompt_fragment через monkeypatch
# ---------------------------------------------------------------------------

@pytest.fixture()
def patch_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заменить to_llm_prompt_fragment на заглушку для build()."""
    import v8unpack_agent.form_rag as rag_mod

    def _fake_prompt(ctx, **kwargs) -> str:
        return getattr(ctx, "_prompt", str(ctx))

    monkeypatch.setattr(rag_mod, "_prompt_fn", _fake_prompt, raising=False)


# ---------------------------------------------------------------------------
# Вспомогательная функция build с patch
# ---------------------------------------------------------------------------

def _build(rag: FormRagIndex, contexts: list, embedder) -> None:
    """build() с обходом to_llm_prompt_fragment через прямую инъекцию."""
    import v8unpack_agent.form_context as fc_mod

    original = getattr(fc_mod, "to_llm_prompt_fragment", None)
    fc_mod.to_llm_prompt_fragment = lambda ctx, **kw: getattr(ctx, "_prompt", str(ctx))  # type: ignore[assignment]
    try:
        rag.build(contexts, embedder)
    finally:
        if original is not None:
            fc_mod.to_llm_prompt_fragment = original
        else:
            del fc_mod.to_llm_prompt_fragment


# ===========================================================================
# 1. build + query
# ===========================================================================

def test_query_returns_top_k_by_cosine() -> None:
    e1 = _entry(form_name="A")
    e2 = _entry(form_name="B")
    e3 = _entry(form_name="C")
    idx = FormRagIndex(_scan_index(e1, e2, e3))
    ctxs = _contexts_for(e1, e2, e3)

    call = {"n": 0}

    def embed(text: str) -> list[float]:
        n = call["n"]
        call["n"] += 1
        v = [0.0, 0.0, 0.0]
        v[n % 3] = 1.0
        return v

    _build(idx, ctxs, embed)

    results = idx.query([0.0, 1.0, 0.0], top_k=3)
    assert len(results) == 3
    assert results[0].matched[0].form_name == "B"
    assert results[0].confidence == pytest.approx(1.0)
    assert results[1].confidence == pytest.approx(0.0)
    assert results[2].confidence == pytest.approx(0.0)


def test_query_empty_index_returns_empty() -> None:
    idx = FormRagIndex(_scan_index())
    results = idx.query([1.0, 0.0], top_k=5)
    assert results == []


def test_query_top_k_limits_results() -> None:
    entries = [_entry(form_name=f"F{i}") for i in range(5)]
    idx = FormRagIndex(_scan_index(*entries))
    ctxs = _contexts_for(*entries)
    embed = _embedder_const(dim=2)
    _build(idx, ctxs, embed)
    results = idx.query([1.0, 0.0], top_k=2)
    assert len(results) == 2


def test_query_result_structure() -> None:
    e = _entry()
    idx = FormRagIndex(_scan_index(e))
    _build(idx, _contexts_for(e), _embedder_const(dim=2))
    results = idx.query([1.0, 0.0], top_k=1)
    assert len(results) == 1
    r = results[0]
    assert r.matched == [e]
    assert 0.0 <= r.confidence <= 1.0


def test_tie_break_by_key() -> None:
    """При одинаковом cosine порядок определяется лексикографически по ключу."""
    e1 = _entry(form_name="Z")
    e2 = _entry(form_name="A")
    idx = FormRagIndex(_scan_index(e1, e2))

    def embed(text: str) -> list[float]:
        return [1.0, 0.0]

    _build(idx, _contexts_for(e1, e2), embed)
    results = idx.query([1.0, 0.0], top_k=2)
    assert results[0].matched[0].form_name == "A"
    assert results[1].matched[0].form_name == "Z"


# ===========================================================================
# 2. save / load round-trip
# ===========================================================================

def test_save_load_roundtrip(tmp_path: Path) -> None:
    e1 = _entry(form_name="X")
    e2 = _entry(form_name="Y")
    si = _scan_index(e1, e2)

    idx_orig = FormRagIndex(si)
    call = {"n": 0}

    def embed(text: str) -> list[float]:
        n = call["n"]; call["n"] += 1
        return [float(n), float(n + 1)]

    _build(idx_orig, _contexts_for(e1, e2), embed)
    idx_orig.save(tmp_path)

    idx_loaded = FormRagIndex(si)
    idx_loaded.load(tmp_path)

    r_orig = idx_orig.query([1.0, 1.0], top_k=2)
    r_load = idx_loaded.query([1.0, 1.0], top_k=2)
    assert len(r_orig) == len(r_load) == 2
    for ro, rl in zip(r_orig, r_load):
        assert ro.matched[0].form_name == rl.matched[0].form_name
        assert ro.confidence == pytest.approx(rl.confidence)


def test_empty_save_load(tmp_path: Path) -> None:
    si = _scan_index()
    idx = FormRagIndex(si)
    idx.save(tmp_path)
    idx2 = FormRagIndex(si)
    idx2.load(tmp_path)
    assert idx2.query([1.0], top_k=1) == []


# ===========================================================================
# 3. Детерминированность
# ===========================================================================

def test_save_is_deterministic(tmp_path: Path) -> None:
    e = _entry()
    si = _scan_index(e)
    idx = FormRagIndex(si)
    _build(idx, _contexts_for(e), _embedder_const(dim=3))

    dir1 = tmp_path / "a"
    dir2 = tmp_path / "b"
    idx.save(dir1)
    idx.save(dir2)

    assert (dir1 / _NPZ_NAME).read_bytes() == (dir2 / _NPZ_NAME).read_bytes()
    assert (dir1 / _META_NAME).read_bytes() == (dir2 / _META_NAME).read_bytes()


def test_npz_date_time(tmp_path: Path) -> None:
    """ZIP entry date_time зафиксирован как _ZIP_DATE."""
    e = _entry()
    idx = FormRagIndex(_scan_index(e))
    _build(idx, _contexts_for(e), _embedder_const(dim=2))
    idx.save(tmp_path)
    with zipfile.ZipFile(tmp_path / _NPZ_NAME) as zf:
        info = zf.getinfo(_NPY_ENTRY)
        assert info.date_time == _ZIP_DATE


# ===========================================================================
# 4. Fail-closed reader
# ===========================================================================

def test_decode_wrong_magic_raises() -> None:
    bad = b"\x00NUMPY\x01\x00" + b"\x00" * 100
    with pytest.raises(RagLoadError, match="magic"):
        _decode_matrix(bad)


def test_decode_wrong_version_raises() -> None:
    bad = b"\x93NUMPY\x02\x00" + b"\x00" * 100
    with pytest.raises(RagLoadError, match="magic"):
        _decode_matrix(bad)


def test_decode_wrong_dtype_raises() -> None:
    from v8unpack_agent.form_rag import _NPY_MAGIC, _NPY_VER, _npy_header  # noqa: F401
    descr = "{'descr': '<f4', 'fortran_order': False, 'shape': (1, 2), }"
    raw = descr.encode("latin-1") + b"\n"
    prefix_len = len(_NPY_MAGIC) + len(_NPY_VER) + 2
    pad = (-len(raw) - prefix_len) % 64
    raw = raw[:-1] + b" " * pad + b"\n"
    hlen = len(raw).to_bytes(2, "little")
    header = _NPY_MAGIC + _NPY_VER + hlen + raw
    body = struct.pack("<2f", 1.0, 2.0)
    with pytest.raises(RagLoadError, match="dtype"):
        _decode_matrix(header + body)


def test_decode_size_limit_raises() -> None:
    from v8unpack_agent.form_rag import _MAX_NPY_BYTES
    big = b"\x93NUMPY\x01\x00" + b"\x00" * _MAX_NPY_BYTES
    with pytest.raises(RagLoadError, match="size"):
        _decode_matrix(big)


def test_load_unknown_schema_raises(tmp_path: Path) -> None:
    meta = {"schema_version": 99, "count": 0, "dimension": 0, "keys": []}
    (tmp_path / _META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    npy = _encode_matrix([])
    with zipfile.ZipFile(tmp_path / _NPZ_NAME, "w") as zf:
        zi = zipfile.ZipInfo(_NPY_ENTRY, date_time=_ZIP_DATE)
        zf.writestr(zi, npy)
    idx = FormRagIndex(_scan_index())
    with pytest.raises(RagLoadError, match="schema_version"):
        idx.load(tmp_path)


def test_load_unknown_key_raises(tmp_path: Path) -> None:
    """Ключ в meta отсутствует в scan_index → RagLoadError."""
    e = _entry(form_name="X")
    si = _scan_index(e)
    idx = FormRagIndex(si)
    _build(idx, _contexts_for(e), _embedder_const(dim=2))
    idx.save(tmp_path)

    idx2 = FormRagIndex(_scan_index())
    with pytest.raises(RagLoadError, match="not found in scan_index"):
        idx2.load(tmp_path)


# ===========================================================================
# 5. RagBuildError
# ===========================================================================

def test_build_raises_on_missing_key() -> None:
    e = _entry(form_name="X")
    other = _entry(form_name="Y")
    idx = FormRagIndex(_scan_index(e))
    with pytest.raises(RagBuildError, match="not found in scan_index"):
        _build(idx, _contexts_for(other), _embedder_const(dim=2))


def test_build_raises_on_duplicate_context() -> None:
    e = _entry()
    idx = FormRagIndex(_scan_index(e))
    ctxs = _contexts_for(e, e)
    with pytest.raises(RagBuildError, match="Duplicate"):
        _build(idx, ctxs, _embedder_const(dim=2))


def test_duplicate_key_in_scan_index_raises() -> None:
    e1 = _entry()
    e2 = _entry()
    with pytest.raises(RagBuildError, match="Duplicate form key"):
        FormRagIndex(_scan_index(e1, e2))


# ===========================================================================
# 6. NPY encode/decode symmetry
# ===========================================================================

def test_encode_decode_symmetry() -> None:
    matrix = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    decoded = _decode_matrix(_encode_matrix(matrix))
    assert len(decoded) == 2
    for orig, dec in zip(matrix, decoded):
        for a, b in zip(orig, dec):
            assert a == pytest.approx(b)


def test_encode_decode_empty() -> None:
    assert _decode_matrix(_encode_matrix([])) == []


# ===========================================================================
# 7. Cosine
# ===========================================================================

def test_cosine_identical() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector() -> None:
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ===========================================================================
# 8. Ленивый импорт (scan_forms не грузится при import form_rag)
# ===========================================================================

def test_form_rag_does_not_import_scan_forms_eagerly() -> None:
    result = subprocess.run(
        [
            sys.executable, "-c",
            (
                "import json, sys; import v8unpack_agent.form_rag; "
                "print(json.dumps('v8unpack_agent.scan_forms' in sys.modules))"
            ),
        ],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert result.stdout.strip() == "false"


# ===========================================================================
# 9. FormRagIndex в __all__ и __init__
# ===========================================================================

def test_form_rag_index_in_all() -> None:
    import v8unpack_agent
    assert "FormRagIndex" in v8unpack_agent.__all__


def test_form_rag_index_resolves_from_root() -> None:
    import v8unpack_agent
    assert v8unpack_agent.FormRagIndex is FormRagIndex
