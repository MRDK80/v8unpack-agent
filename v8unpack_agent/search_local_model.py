"""Опциональный адаптер локальной модели эмбеддингов на sentence-transformers (#407).

Extra ``local-model``: ``pip install "v8unpack-agent[local-model]"``. Без него
модуль импортируется, а создание эмбеддера даёт
``LocalModelError("embedder_unavailable")``. Веса только локальные: каталог
модели передаётся явно, сеть отключается до импорта библиотеки
(``HF_HUB_OFFLINE``, ``TRANSFORMERS_OFFLINE``, ``local_files_only``), веса не
скачиваются. Production default эмбеддера этим модулем не меняется.

Идентичность (``EmbedderIdentity``) не зависит от пути к модели: имя
``st:<model_name>``, версия — ревизия модели, ``config_hash`` — sha256
параметров векторизации и конфигурационных файлов модели (размеры файлов
весов, без их чтения целиком).

Векторизация пакетная, с прогрессом и оценкой оставшегося времени (по
желанию) и возобновляемая: при ``cache_dir`` готовые векторы сохраняются
частями ``.npz`` и при повторном запуске не пересчитываются.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import tempfile
import time
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from v8unpack_agent.search_index import EmbedderIdentity

__all__ = [
    "DEVICES",
    "LOCAL_MODEL_EMBEDDER_NAME",
    "PRECISIONS",
    "LocalModelError",
    "SentenceTransformerEmbedder",
    "local_model_identity",
]

LOCAL_MODEL_EMBEDDER_NAME = "sentence-transformers"
DEVICES = ("auto", "cpu", "cuda")
PRECISIONS = ("fp32", "fp16")
_ADAPTER_VERSION = "1"
# Конфигурационные файлы читаются целиком; у весов учитывается только размер.
_CONFIG_SUFFIXES = (".json", ".txt", ".model")
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".onnx", ".h5", ".msgpack")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_PART_RE = re.compile(r"part_(\d{6})\.npz")


class LocalModelError(Exception):
    """Безопасная ошибка адаптера: ``code`` фиксирован, текст без путей."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    # Без индикаторов загрузки и предупреждений библиотек в stderr CLI.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")


def _model_files(model_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(model_dir).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        suffix = path.suffix.lower()
        if suffix in _CONFIG_SUFFIXES:
            files[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        elif suffix in _WEIGHT_SUFFIXES:
            files[rel] = f"size:{path.stat().st_size}"
    return files


def local_model_identity(
    model_dir: str | os.PathLike[str],
    *,
    model_name: str,
    model_revision: str | None,
    dimension: int,
    max_tokens: int,
    normalize: bool = True,
    precision: str = "fp32",
) -> EmbedderIdentity:
    """Идентичность модели без её загрузки; путь в неё не входит."""
    root = Path(model_dir)
    if not root.is_dir():
        raise LocalModelError("model_not_found", "model directory not found")
    if not _NAME_RE.fullmatch(model_name):
        raise LocalModelError("invalid_model_name", "invalid model name")
    files = _model_files(root)
    if not files:
        raise LocalModelError("model_not_found", "model directory has no model files")
    params = {
        "adapter": _ADAPTER_VERSION,
        "files": files,
        "max_tokens": max_tokens,
        "model_name": model_name,
        "normalize": normalize,
        "precision": precision,
    }
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return EmbedderIdentity(
        f"st:{model_name}",
        model_revision or "unspecified",
        dimension,
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


class SentenceTransformerEmbedder:
    """Эмбеддер на локальной модели sentence-transformers.

    ``progress`` — функция одной строки (например, печать в stderr) или
    ``None``. ``device="auto"`` выбирает ``cuda`` при наличии, иначе ``cpu``.
    """

    def __init__(
        self,
        model_dir: str | os.PathLike[str],
        *,
        model_name: str,
        model_revision: str | None = None,
        device: str = "auto",
        max_tokens: int = 512,
        batch_size: int = 32,
        normalize: bool = True,
        precision: str = "fp32",
        cache_dir: str | os.PathLike[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if device not in DEVICES:
            raise LocalModelError("invalid_device", "device must be auto, cpu or cuda")
        if precision not in PRECISIONS:
            raise LocalModelError("invalid_parameter", "precision must be fp32 or fp16")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise LocalModelError("invalid_parameter", "max_tokens must be >= 1")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise LocalModelError("invalid_parameter", "batch_size must be >= 1")
        self._dir = Path(model_dir)
        if not self._dir.is_dir():
            raise LocalModelError("model_not_found", "model directory not found")
        _offline()
        try:
            self._np = importlib.import_module("numpy")
            st = importlib.import_module("sentence_transformers")
        except ImportError:
            raise LocalModelError(
                "embedder_unavailable",
                "install the local-model extra: sentence-transformers and numpy",
            ) from None
        self.device = self._resolve_device(device)
        if precision == "fp16" and self.device != "cuda":
            raise LocalModelError("invalid_parameter", "fp16 requires the cuda device")
        self._progress = progress
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._model = st.SentenceTransformer(
                    str(self._dir), device=self.device, local_files_only=True
                )
                if precision == "fp16":
                    self._model.half()
                self._model.max_seq_length = max_tokens
                getter = getattr(self._model, "get_embedding_dimension", None) or (
                    self._model.get_sentence_embedding_dimension
                )
                dimension = int(getter())
        except Exception:  # noqa: BLE001 - сторонняя библиотека, детали не раскрываем
            raise LocalModelError("model_load_failed", "local model could not be loaded") from None
        self.precision = precision
        self._batch_size = batch_size
        self._normalize = normalize
        self._identity = local_model_identity(
            self._dir, model_name=model_name, model_revision=model_revision,
            dimension=dimension, max_tokens=max_tokens, normalize=normalize,
            precision=precision,
        )
        self._cache = None if cache_dir is None else Path(cache_dir) / self._identity.config_hash[:16]
        self._say(f"device: {self.device} precision: {precision}")

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            torch = importlib.import_module("torch")
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001 - нет torch или драйвера: CPU
            return "cpu"

    @property
    def identity(self) -> EmbedderIdentity:
        return self._identity

    def _say(self, line: str) -> None:
        if self._progress is not None:
            self._progress(line)

    def _load_cache(self) -> dict[str, tuple[float, ...]]:
        cached: dict[str, tuple[float, ...]] = {}
        if self._cache is None or not self._cache.is_dir():
            return cached
        for path in sorted(self._cache.iterdir()):
            if not _PART_RE.fullmatch(path.name):
                continue
            try:
                with self._np.load(path, allow_pickle=False) as part:
                    keys, vectors = part["keys"], part["vectors"]
                    if vectors.ndim != 2 or vectors.shape != (len(keys), self._identity.dimension):
                        continue
                    for key, vector in zip(keys.tolist(), vectors.tolist(), strict=True):
                        cached[str(key)] = tuple(float(v) for v in vector)
            except Exception:  # noqa: BLE001, S112 - повреждённая часть пересчитывается
                continue
        return cached

    def _save_part(self, keys: list[str], vectors: Any) -> None:
        if self._cache is None:
            return
        self._cache.mkdir(parents=True, exist_ok=True)
        numbers = [int(m.group(1)) for p in self._cache.iterdir()
                   if (m := _PART_RE.fullmatch(p.name))]
        target = self._cache / f"part_{(max(numbers) + 1 if numbers else 0):06d}.npz"
        fd, tmp = tempfile.mkstemp(prefix="part.", suffix=".tmp", dir=self._cache)
        try:
            with os.fdopen(fd, "wb") as handle:
                self._np.savez(handle, keys=self._np.asarray(keys), vectors=vectors)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Векторы текстов в исходном порядке; готовые берутся из кэша."""
        keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        cached = self._load_cache()
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in cached and key not in missing:
                missing[key] = text
        total = len(missing)
        if total:
            self._say(f"embed: {total} to compute, {len(set(keys)) - total} cached")
        # По длине: пакеты из текстов близкой длины меньше дополняются; на
        # векторы порядок не влияет (каждый текст кодируется независимо).
        pending = sorted(missing.items(), key=lambda item: -len(item[1]))
        started = time.monotonic()
        done = 0
        for start in range(0, total, self._batch_size):
            batch = pending[start:start + self._batch_size]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                vectors = self._model.encode(
                    [text for _, text in batch], batch_size=self._batch_size,
                    normalize_embeddings=self._normalize, convert_to_numpy=True,
                    show_progress_bar=False,
                )
            vectors = self._np.asarray(vectors, dtype=self._np.float32)
            batch_keys = [key for key, _ in batch]
            self._save_part(batch_keys, vectors)
            for key, vector in zip(batch_keys, vectors.tolist(), strict=True):
                cached[key] = tuple(float(v) for v in vector)
            done += len(batch)
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed > 0 else 0.0
            eta = (total - done) / rate if rate > 0 else 0.0
            self._say(f"embed: {done}/{total} {rate:.1f}/s eta {_format_eta(eta)}")
        return [cached[key] for key in keys]
