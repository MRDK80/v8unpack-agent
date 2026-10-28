"""Адаптер локальной модели и слияние двух уровней (#407), без весов.

``sentence_transformers`` и ``torch`` подменяются модулями в памяти: проверяются
управление (офлайн-режим, устройство, пакеты, прогресс, кэш, ошибки,
идентичность) и слияние рангов уровней фрагментов и объектов. Настоящая
библиотека здесь не проверяется — это API smoke владельца.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import socket
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

np = pytest.importorskip("numpy")

from v8unpack_agent import search_cli
from v8unpack_agent.search_api import (
    build_object_snapshot,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerRef,
    SearchFilters,
    SearchQuery,
)
from v8unpack_agent.search_corpus import SEARCH_CORPUS_SCHEMA
from v8unpack_agent.search_fragments import fragment_corpus
from v8unpack_agent.search_index import (
    RRF_K,
    EmbedderIdentity,
    IndexEntry,
    SearchIndex,
    load_index,
    object_level,
    query_index,
    query_two_level,
)
from v8unpack_agent.search_local_model import (
    LocalModelError,
    SentenceTransformerEmbedder,
    local_model_identity,
)

DIM = 8


class FakeModel:
    instances: ClassVar[list[FakeModel]] = []
    encoded: ClassVar[list[str]] = []
    fail_after: ClassVar[int | None] = None

    def __init__(self, path: str, device: str | None = None,
                 local_files_only: bool = False) -> None:
        import os

        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        self.path, self.device, self.local_files_only = path, device, local_files_only
        self.max_seq_length: int | None = None
        self.calls: list[dict[str, Any]] = []
        self.halved = False
        FakeModel.instances.append(self)

    def half(self) -> FakeModel:
        self.halved = True
        return self

    def get_embedding_dimension(self) -> int:
        return DIM

    def get_sentence_embedding_dimension(self) -> int:
        raise AssertionError("deprecated API used")

    def encode(self, texts: Sequence[str], **kwargs: Any) -> Any:
        if FakeModel.fail_after is not None and len(self.calls) >= FakeModel.fail_after:
            raise RuntimeError("interrupted")
        self.calls.append(dict(kwargs, n=len(texts), texts=list(texts)))
        FakeModel.encoded.extend(texts)
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            row = [(b - 128) / 128 for b in digest[:DIM]]
            row[0] += 1.5
            norm = math.sqrt(sum(v * v for v in row))
            rows.append([v / norm for v in row] if kwargs["normalize_embeddings"] else row)
        return np.asarray(rows, dtype=np.float32)


@pytest.fixture
def fake_st(monkeypatch: pytest.MonkeyPatch) -> type[FakeModel]:
    FakeModel.instances, FakeModel.encoded, FakeModel.fail_after = [], [], None
    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = FakeModel  # type: ignore[attr-defined]
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setitem(sys.modules, "torch", torch)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    return FakeModel


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    root = tmp_path / "models" / "fake-model"
    (root / "1_Pooling").mkdir(parents=True)
    (root / "config.json").write_text('{"hidden_size": 8}', encoding="utf-8")
    (root / "modules.json").write_text("[]", encoding="utf-8")
    (root / "1_Pooling" / "config.json").write_text('{"cls": true}', encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"\x00" * 64)
    (root / ".cache").mkdir()
    (root / ".cache" / "lock.json").write_text("{}", encoding="utf-8")
    return root


def _embedder(model_dir: Path, **kwargs: Any) -> SentenceTransformerEmbedder:
    kwargs.setdefault("model_name", "fake/model")
    return SentenceTransformerEmbedder(model_dir, **kwargs)


def test_missing_extra(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(LocalModelError) as err:
        _embedder(model_dir)
    assert err.value.code == "embedder_unavailable"


def test_missing_model_dir(fake_st: type[FakeModel], tmp_path: Path) -> None:
    with pytest.raises(LocalModelError) as err:
        _embedder(tmp_path / "absent")
    assert err.value.code == "model_not_found"
    assert str(tmp_path) not in str(err.value)
    assert fake_st.instances == []


def test_offline_device_and_parameters(
    fake_st: type[FakeModel], model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    embedder = _embedder(model_dir, max_tokens=77, batch_size=3, progress=lines.append)
    model = fake_st.instances[-1]
    assert model.local_files_only is True
    assert model.device == "cpu" and embedder.device == "cpu"
    assert model.max_seq_length == 77
    assert lines == ["device: cpu precision: fp32"]
    assert model.halved is False
    sys.modules["torch"].cuda = types.SimpleNamespace(is_available=lambda: True)  # type: ignore[attr-defined]
    assert _embedder(model_dir).device == "cuda"
    assert _embedder(model_dir, device="cpu").device == "cpu"
    monkeypatch.setitem(sys.modules, "torch", None)
    assert _embedder(model_dir).device == "cpu"


def test_fp16(fake_st: type[FakeModel], model_dir: Path) -> None:
    with pytest.raises(LocalModelError):
        _embedder(model_dir, precision="fp16", device="cpu")
    sys.modules["torch"].cuda = types.SimpleNamespace(is_available=lambda: True)  # type: ignore[attr-defined]
    half = _embedder(model_dir, precision="fp16")
    assert fake_st.instances[-1].halved is True and half.precision == "fp16"
    assert half.identity != _embedder(model_dir).identity


def test_legacy_dimension_api(fake_st: type[FakeModel], model_dir: Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(FakeModel, "get_embedding_dimension")
    monkeypatch.setattr(FakeModel, "get_sentence_embedding_dimension", lambda self: DIM)
    assert _embedder(model_dir).identity.dimension == DIM


@pytest.mark.parametrize("kwargs", [
    {"device": "tpu"}, {"max_tokens": 0}, {"batch_size": 0}, {"batch_size": True},
    {"precision": "int8"},
])
def test_bad_parameters(fake_st: type[FakeModel], model_dir: Path, kwargs: dict[str, Any]) -> None:
    with pytest.raises(LocalModelError):
        _embedder(model_dir, **kwargs)


def test_model_load_failure(
    fake_st: type[FakeModel], model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("secret-model-location")

    monkeypatch.setattr(sys.modules["sentence_transformers"], "SentenceTransformer", broken)
    with pytest.raises(LocalModelError) as err:
        _embedder(model_dir)
    assert err.value.code == "model_load_failed"
    assert "secret-model-location" not in str(err.value)


def test_identity(fake_st: type[FakeModel], model_dir: Path, tmp_path: Path) -> None:
    base = _embedder(model_dir, model_revision="rev1").identity
    assert base.name == "st:fake/model" and base.version == "rev1" and base.dimension == DIM
    assert str(tmp_path) not in json.dumps(base.to_dict())
    moved = tmp_path / "elsewhere" / "copy"
    shutil.copytree(model_dir, moved)
    assert _embedder(moved, model_revision="rev1").identity == base
    assert _embedder(model_dir).identity.version == "unspecified"
    assert _embedder(model_dir, model_revision="rev1", max_tokens=128).identity != base
    assert _embedder(model_dir, model_revision="rev1", model_name="other").identity != base
    (moved / "1_Pooling" / "config.json").write_text('{"cls": false}', encoding="utf-8")
    assert _embedder(moved, model_revision="rev1").identity != base
    (moved / "1_Pooling" / "config.json").write_text('{"cls": true}', encoding="utf-8")
    (moved / "model.safetensors").write_bytes(b"\x01" * 64)
    assert _embedder(moved, model_revision="rev1").identity == base  # веса — по размеру
    (moved / "model.safetensors").write_bytes(b"\x01" * 65)
    resized = _embedder(moved, model_revision="rev1").identity
    assert resized != base
    (moved / ".cache" / "lock.json").write_text('{"x": 1}', encoding="utf-8")
    assert _embedder(moved, model_revision="rev1").identity == resized  # скрытые файлы не входят
    with pytest.raises(LocalModelError):
        local_model_identity(model_dir, model_name="bad name!", model_revision=None,
                             dimension=DIM, max_tokens=512)


def test_embed_batches_order_and_progress(fake_st: type[FakeModel], model_dir: Path) -> None:
    lines: list[str] = []
    embedder = _embedder(model_dir, batch_size=2, progress=lines.append)
    texts = ["а", "бббб", "вв", "а", "ггг"]
    vectors = embedder.embed(texts)
    model = fake_st.instances[-1]
    assert [c["n"] for c in model.calls] == [2, 2]
    assert [c["texts"] for c in model.calls] == [["бббб", "ггг"], ["вв", "а"]]  # по длине
    fresh = _embedder(model_dir, batch_size=10)
    assert fresh.embed(texts) == vectors  # порядок результата — исходный
    assert all(c["normalize_embeddings"] and c["convert_to_numpy"] for c in model.calls)
    assert vectors[0] == vectors[3] and len(set(vectors)) == 4
    assert all(len(v) == DIM for v in vectors)
    assert lines[1] == "embed: 4 to compute, 0 cached"
    assert lines[-1].startswith("embed: 4/4 ") and " eta " in lines[-1]
    silent = _embedder(model_dir)
    silent.embed(["x"])  # без progress — без вывода


def test_resumable_cache(fake_st: type[FakeModel], model_dir: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    texts = [f"текст {i}" for i in range(7)]
    fake_st.fail_after = 2
    with pytest.raises(RuntimeError):
        _embedder(model_dir, batch_size=2, cache_dir=cache).embed(texts)
    first_run = list(fake_st.encoded)
    assert len(first_run) == 4
    fake_st.fail_after, fake_st.encoded = None, []
    resumed = _embedder(model_dir, batch_size=2, cache_dir=cache).embed(texts)
    assert sorted(fake_st.encoded) == sorted(set(texts) - set(first_run))
    fake_st.encoded = []
    fresh = _embedder(model_dir, batch_size=2).embed(texts)
    assert resumed == fresh
    fake_st.encoded = []
    again = _embedder(model_dir, batch_size=2, cache_dir=cache).embed(texts)
    assert again == fresh and fake_st.encoded == []
    parts = sorted(next(cache.iterdir()).iterdir())
    parts[0].write_bytes(b"broken")
    fake_st.encoded = []
    _embedder(model_dir, batch_size=2, cache_dir=cache).embed(texts)
    assert sorted(fake_st.encoded) == sorted(first_run[:2])
    other = _embedder(model_dir, batch_size=2, cache_dir=cache, max_tokens=64)
    fake_st.encoded = []
    other.embed(texts[:1])
    assert fake_st.encoded == texts[:1]  # другая идентичность — свой кэш


# ------------------------------------------------------------- два уровня

IDENTITY = EmbedderIdentity("fake407", "1", 3, "c407")
H = "0" * 64


class FixedEmbedder:
    identity = IDENTITY
    calls = 0

    def __init__(self, vector: Sequence[float]) -> None:
        self.vector = list(vector)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        FixedEmbedder.calls += 1
        return [list(self.vector) for _ in texts]


def _art(i: int) -> ArtifactRef:
    return ArtifactRef("module", OwnerRef("metadata_object", ("Catalog", "Document")[i % 2],
                                          f"Объект{i}"), "object")


def _entries(spec: dict[int, list[tuple[float, float, float]]]) -> SearchIndex:
    entries = []
    for i, vectors in spec.items():
        for p, vector in enumerate(vectors):
            entries.append(IndexEntry(f"{_art(i).artifact_id}#{p}", _art(i), f"П{p}",
                                      None, H, H, H, vector))
    return SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", IDENTITY, tuple(entries))


def _expected(frag: SearchIndex, obj: SearchIndex, query: SearchQuery,
              vector: list[float]) -> list[tuple[str, str, float]]:
    depth = max(query.top_k * 5, 50)
    wide = SearchQuery(query.text, mode="semantic", top_k=depth, filters=query.filters)
    scores: dict[str, float] = {}
    docs: dict[str, str] = {}
    for index in (frag, obj):
        result = query_index(index, wide, FixedEmbedder(vector))
        for rank, hit in enumerate(result.hits, start=1):
            key = hit.artifact.identity
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            docs.setdefault(key, str(hit.document_id))
    order = sorted(scores, key=lambda k: (-scores[k], k))[: query.top_k]
    return [(k, docs[k], scores[k]) for k in order]


def test_two_level_rrf() -> None:
    frag = _entries({1: [(1, 0, 0), (0, 1, 0)], 2: [(0.9, 0.1, 0)], 3: [(0, 0, 1)],
                     4: [(0.5, 0.5, 0), (0.7, 0.2, 0.1)]})
    obj = _entries({1: [(0, 1, 0)], 3: [(1, 0, 0.1)], 5: [(1, 0.05, 0)]})
    for vector in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.2], [0.3, 0.3, 0.9]):
        for filters in (SearchFilters(), SearchFilters(metadata_types=("Catalog",))):
            for top_k in (1, 2, 3, 10):
                query = SearchQuery("q", mode="semantic", top_k=top_k, filters=filters)
                FixedEmbedder.calls = 0
                result = query_two_level(frag, obj, query, FixedEmbedder(vector))
                assert FixedEmbedder.calls == 1
                got = [(h.artifact.identity, h.document_id, h.similarity) for h in result.hits]
                assert got == _expected(frag, obj, query, vector)
                assert all(filters.matches(h.artifact) for h in result.hits)
    only_objects = query_two_level(frag, obj, SearchQuery("q", mode="semantic", top_k=10),
                                   FixedEmbedder([1.0, 0.05, 0.0]))
    by_id = {h.artifact.identity: h.document_id for h in only_objects.hits}
    assert by_id[_art(5).identity] == f"{_art(5).artifact_id}#0"
    assert by_id[_art(1).identity] == f"{_art(1).artifact_id}#0"  # лучший фрагмент уровня фрагментов
    assert query_two_level(frag, obj, SearchQuery("q", mode="exact"),
                           FixedEmbedder([1, 0, 0])).status == "empty"
    other = SearchIndex(SEARCH_CORPUS_SCHEMA, "rv", EmbedderIdentity("x", "1", 3, "y"), obj.entries)
    failed = query_two_level(frag, other, SearchQuery("q", mode="semantic"), FixedEmbedder([1, 0, 0]))
    assert failed.status == "error" and failed.error is not None
    assert failed.error.code == "incompatible_index"
    empty = query_two_level(frag, obj, SearchQuery(
        "q", mode="semantic", filters=SearchFilters(metadata_types=("Report",))), FixedEmbedder([1, 0, 0]))
    assert empty.status == "empty"


FILES = {
    "Catalog/Товары/Catalog.obj.bsl": (
        "Процедура ПередЗаписью(Отказ)\n  Цена = 1;\nКонецПроцедуры\n\n"
        "Процедура ПриЗаписи()\n  Остаток = 2;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.obj.bsl": (
        "Процедура ПриОткрытии()\n  ЦенаТовара = 1;\nКонецПроцедуры\n"
    ),
    "Catalog/Товары/Form/ФормаЭлемента/Form.json": "{}",
    "CommonModule/Общий/CommonModule.obj.bsl": (
        "Процедура Первая() Экспорт\nКонецПроцедуры\n\nПроцедура Вторая() Экспорт\n"
        "  Сумма = 0;\nКонецПроцедуры\n"
    ),
}


@pytest.fixture
def export(tmp_path: Path) -> Path:
    root = tmp_path / "export-407"
    for relative, text in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return root


def test_object_level_and_context(fake_st: type[FakeModel], model_dir: Path,
                                  export: Path, tmp_path: Path) -> None:
    fragmentation = fragment_corpus(collect_search_corpus(export))
    objects = object_level(fragmentation)
    assert object_level(fragmentation) is objects
    ids = [f.document.artifact.identity for f in objects.fragments]
    assert len(ids) == len(set(ids)) == len({f.document.artifact.identity
                                             for f in fragmentation.fragments})
    assert len(objects.fragments) < len(fragmentation.fragments)
    embedder = _embedder(model_dir)
    frag_index = build_search_snapshot(fragmentation, embedder, tmp_path / "f.json")
    obj_index = build_object_snapshot(fragmentation, embedder, tmp_path / "o.json")
    assert obj_index.assess(embedder.identity, objects).freshness == "fresh"
    assert load_index(tmp_path / "o.json") == obj_index
    context = search_with_context(
        fragmentation, "цена", search_mode="semantic", top_k=3, index=frag_index,
        embedder=embedder, object_index=obj_index, max_chars=-1,
    )
    expected = query_two_level(frag_index, obj_index,
                               SearchQuery("цена", mode="semantic", top_k=3), embedder, fragmentation)
    assert context.dispatch.result == expected
    assert context.dispatch.index_status is not None
    assert context.dispatch.index_status.freshness == "fresh"


def _cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any, str]:
    code = search_cli.main(list(argv))
    captured = capsys.readouterr()
    data = json.loads(captured.out) if captured.out else None
    return code, data, captured.err


def test_cli_local_model_two_levels(fake_st: type[FakeModel], model_dir: Path, export: Path,
                                    tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap, obj = tmp_path / "s.json", tmp_path / "o.json"
    local = ("--embedder", "sentence-transformers", "--model", str(model_dir),
             "--model-name", "fake/model", "--model-revision", "r1")
    code, data, err = _cli(capsys, "build", str(export), "--snapshot", str(snap),
                           "--object-snapshot", str(obj), *local)
    assert code == 0 and err == ""
    assert data["snapshot"]["schema"] == "search_index/2"
    assert data["snapshot"]["embedder"]["name"] == "st:fake/model"
    assert data["object_snapshot"]["schema"] == "search_index/2"
    assert data["object_snapshot"]["entries"] < data["snapshot"]["entries"]
    code, data, err = _cli(capsys, "query", str(export), "--query", "цена",
                           "--search-mode", "semantic", "--snapshot", str(snap),
                           "--object-snapshot", str(obj), *local)
    assert code == 0 and err == ""
    assert data["object_snapshot"] == {"loaded": True, "error_code": None,
                                       "compatibility": "compatible", "freshness": "fresh"}
    assert data["context"]["dispatch"]["result"]["status"] == "semantic"
    assert str(tmp_path) not in json.dumps(data)
    code, data, err = _cli(capsys, "query", str(export), "--query", "цена",
                           "--search-mode", "semantic", "--snapshot", str(snap),
                           "--object-snapshot", str(tmp_path / "absent.json"), *local)
    assert code == 3 and data["object_snapshot"]["error_code"] == "snapshot_not_found"
    code, data, err = _cli(capsys, "build", str(export), "--snapshot", str(tmp_path / "p.json"),
                           "--snapshot-format", "1", "--progress", *local)
    assert code == 0 and data["snapshot"]["schema"] == "search_index/1"
    assert "device: cpu" in err and "embed: " in err
    assert str(model_dir) not in err


def test_cli_demo_unchanged(export: Path, tmp_path: Path,
                            capsys: pytest.CaptureFixture[str]) -> None:
    code, data, _ = _cli(capsys, "build", str(export), "--snapshot", str(tmp_path / "s.json"),
                         "--embedder", "hashed-bow-demo")
    assert code == 0 and data["snapshot"]["schema"] == "search_index/1"
    assert "object_snapshot" not in data


@pytest.mark.parametrize("extra", [
    ["--embedder", "hashed-bow-demo", "--model", "{model}"],
    ["--embedder", "hashed-bow-demo", "--progress"],
    ["--embedder", "hashed-bow-demo", "--precision", "fp16"],
    ["--embedder", "sentence-transformers", "--model-name", "x"],
    ["--embedder", "sentence-transformers", "--model", "{model}"],
    ["--embedder", "sentence-transformers", "--model", "{absent}", "--model-name", "x"],
    ["--embedder", "sentence-transformers", "--model", "{model}", "--model-name", "x",
     "--batch-size", "0"],
    ["--embedder", "sentence-transformers", "--model", "{model}", "--model-name", "x",
     "--device", "tpu"],
])
def test_cli_bad_local_arguments(extra: list[str], model_dir: Path, export: Path, tmp_path: Path,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    argv = [a.replace("{model}", str(model_dir)).replace("{absent}", str(tmp_path / "no"))
            for a in extra]
    code, data, err = _cli(capsys, "build", str(export), "--snapshot",
                           str(tmp_path / "s.json"), *argv)
    assert code == 2 and data is None
    assert str(tmp_path) not in err


def test_cli_extra_missing(monkeypatch: pytest.MonkeyPatch, model_dir: Path, export: Path,
                           tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    code, data, _ = _cli(capsys, "build", str(export), "--snapshot", str(tmp_path / "s.json"),
                         "--embedder", "sentence-transformers", "--model", str(model_dir),
                         "--model-name", "x")
    assert code == 4
    assert data["error"]["code"] == "embedder_unavailable"
    assert not (tmp_path / "s.json").exists()
