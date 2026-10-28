"""Opt-in командный вход общего поиска (#328, часть B — #367).

Запуск: ``python -m v8unpack_agent.search_cli {build,query}``. Console script
не объявляется, ``v8unpack-agent-run`` и его parser не меняются. Модуль —
тонкая оболочка над :mod:`v8unpack_agent.search_api` (#366): корпус и
фрагментация пересобираются из ``export_root``, снимок хранит только векторы и
хэши.

Машинный результат
------------------

stdout — один канонический JSON (``to_canonical_json``) схемы
``search_cli/1``. Общие поля: ``schema``, ``command``,
``status: ok|degraded|error``, ``error: {code, message}|null``.

* ``build``: ``corpus: {total, counts}``, ``fragments``,
  ``snapshot: {schema, representation_version, embedder, entries}``.
* ``query``: ``snapshot: {provided, loaded, error_code, compatibility,
  freshness}`` и ``context`` — ``SearchContext.to_dict(include_text=...)``.

``error`` описывает сбой стадии (корпус, построение или запись снимка,
внутренняя ошибка) либо ошибку поиска из ``dispatch``. Сбой снимка при
``query`` хранится отдельно, в ``snapshot.error_code``, и не прерывает точный
поиск. Абсолютные пути в JSON не выводятся; исходный текст — только с
``--include-text``.

Коды выхода
-----------

=====  =========  ==========================================================
Код    status     Условие
=====  =========  ==========================================================
0      ok         ``exact``/``ambiguous``/``empty``/``semantic``, контекст
                  ``built``/``truncated``, снимок без замечаний.
2      —          ошибка аргументов (JSON не выводится); ``export_root`` не
                  каталог.
3      degraded   ``index_not_ready``, ``incompatible_index``; указанный
                  ``--snapshot`` не найден, повреждён, ``incompatible`` или
                  ``stale``; контекст ``insufficient_budget``.
4      error      ``embedder_failed``, ``invalid_vector``, ``internal``;
                  сбой корпуса; внутренняя ошибка.
5      error      ``snapshot_write_failed`` при ``build``.
=====  =========  ==========================================================

Значения — константы :mod:`v8unpack_agent.cli`. stderr — только однострочные
санитизированные сообщения без traceback и текста исключений.

Эмбеддер
--------

``--embedder hashed-bow-demo`` — демонстрационная механика построения и
запроса индекса, а не качество поиска. ``--embedder sentence-transformers``
(#407, extra ``local-model``) — локальная модель из каталога ``--model``,
без сети и скачивания весов. Без ``--embedder`` смысловой шаг даёт
``index_not_ready``, точный поиск работает. ``--object-snapshot`` добавляет
уровень объектов: смысловой шаг сливает ранги двух уровней.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn, cast

from v8unpack_agent._safe_paths import sanitize_diagnostic
from v8unpack_agent.cli import (
    EXIT_BAD_INPUT,
    EXIT_DEGRADED,
    EXIT_FATAL,
    EXIT_OK,
    EXIT_WRITE_ERROR,
)
from v8unpack_agent.modules import MODULE_KINDS, OWNER_KINDS, ModuleKind, OwnerKind
from v8unpack_agent.runner import MODULE_GROUPS
from v8unpack_agent.search_api import (
    DEMO_EMBEDDER_NAME,
    HashedBagOfWordsDemoEmbedder,
    SearchCorpusError,
    build_object_snapshot,
    build_search_snapshot,
    collect_search_corpus,
    search_with_context,
)
from v8unpack_agent.search_contract import (
    ARTIFACT_KINDS,
    ArtifactKind,
    SearchFilters,
    SearchQuery,
    to_canonical_json,
)
from v8unpack_agent.search_fragments import FragmentationResult, fragment_corpus
from v8unpack_agent.search_index import (
    SEARCH_INDEX_SCHEMA,
    SEARCH_INDEX_SCHEMA_V2,
    Embedder,
    SearchIndex,
    SearchIndexError,
    load_index,
    object_level,
)
from v8unpack_agent.search_local_model import (
    DEVICES,
    LOCAL_MODEL_EMBEDDER_NAME,
    PRECISIONS,
    LocalModelError,
    SentenceTransformerEmbedder,
)

__all__ = ["SEARCH_CLI_SCHEMA", "build_parser", "main"]

SEARCH_CLI_SCHEMA = "search_cli/1"

_PROGRAM = "python -m v8unpack_agent.search_cli"
_MODES = ("config", "external")
_SEARCH_MODES = ("auto", "exact", "semantic")
_SNAPSHOT_FORMATS = {"1": SEARCH_INDEX_SCHEMA, "2": SEARCH_INDEX_SCHEMA_V2}
_DEGRADED_SEARCH_ERRORS = frozenset({"incompatible_index", "index_not_ready"})
_OK_CONTEXT = frozenset({"built", "truncated"})
_SNAPSHOT_NOT_FOUND = "snapshot_not_found"
_SNAPSHOT_WRITE_FAILED = "snapshot_write_failed"
_INTERNAL_ERROR = "internal_error"
_MESSAGES = {
    "common_modules_failed": "common module discovery failed",
    "embedder_failed": "embedder failed",
    "embedder_unavailable": "local model extra is not installed",
    "model_load_failed": "local model could not be loaded",
    "internal_error": "internal error",
    "invalid_vector": "embedder returned an invalid vector",
    "modules_failed": "module discovery failed",
    "scan_failed": "form scan failed",
    "snapshot_write_failed": "snapshot could not be written",
}
_STATUS_BY_CODE = {
    EXIT_OK: "ok",
    EXIT_DEGRADED: "degraded",
    EXIT_FATAL: "error",
    EXIT_WRITE_ERROR: "error",
}


class _Parser(argparse.ArgumentParser):
    """argparse: одна санитизированная строка ошибки без usage, код 2."""

    def error(self, message: str) -> NoReturn:
        self.exit(
            EXIT_BAD_INPUT,
            f"{self.prog}: ошибка: {sanitize_diagnostic(message)}\n",
        )


def _add_corpus_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "export_root", type=Path, help="корень уже распакованной выгрузки"
    )
    parser.add_argument(
        "--mode",
        choices=_MODES,
        default="config",
        help="режим сканирования форм",
    )
    parser.add_argument(
        "--skip-common-modules",
        action="store_true",
        help="не обнаруживать и не читать общие модули",
    )
    parser.add_argument(
        "--module-group",
        action="append",
        default=None,
        metavar="GROUP",
        help=(
            "включить только эту группу модулей, флаг повторяемый "
            f"({', '.join(MODULE_GROUPS)})"
        ),
    )
    parser.add_argument(
        "--skip-module-group",
        action="append",
        default=None,
        metavar="GROUP",
        help="исключить группу модулей, флаг повторяемый",
    )


def _add_embedder_argument(
    parser: argparse.ArgumentParser, *, required: bool
) -> None:
    parser.add_argument(
        "--embedder",
        choices=(DEMO_EMBEDDER_NAME, LOCAL_MODEL_EMBEDDER_NAME),
        default=None,
        required=required,
        help=(
            f"{DEMO_EMBEDDER_NAME}: демонстрационный хэшированный мешок слов; "
            "механика построения и запроса индекса, а не качество поиска. "
            f"{LOCAL_MODEL_EMBEDDER_NAME}: локальная модель (--model), extra "
            "local-model, без сети"
        ),
    )
    group = parser.add_argument_group("локальная модель (#407)")
    group.add_argument("--model", type=Path, default=None,
                       help="каталог локальной модели sentence-transformers")
    group.add_argument("--model-name", default=None,
                       help="имя модели для идентичности снимка, например BAAI/bge-m3")
    group.add_argument("--model-revision", default=None,
                       help="ревизия модели для идентичности снимка")
    group.add_argument("--device", choices=DEVICES, default="auto",
                       help="auto — cuda при наличии, иначе cpu")
    group.add_argument("--precision", choices=PRECISIONS, default="fp32",
                       help="fp16 — только cuda; входит в идентичность снимка")
    group.add_argument("--max-tokens", type=int, default=512,
                       help="предел длины фрагмента в токенах модели")
    group.add_argument("--batch-size", type=int, default=32,
                       help="размер пакета векторизации")
    group.add_argument("--cache-dir", type=Path, default=None,
                       help="каталог возобновляемого кэша векторов")
    group.add_argument("--progress", action="store_true",
                       help="печатать устройство и прогресс векторизации в stderr")


def build_parser() -> argparse.ArgumentParser:
    """Собрать разборщик аргументов команд ``build`` и ``query``."""
    parser = _Parser(
        prog=_PROGRAM,
        description="Opt-in общий поиск по формам и модулям выгрузки (#328).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser(
        "build", help="построить и сохранить смысловой снимок"
    )
    _add_corpus_arguments(build)
    build.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="путь к JSON-файлу снимка; каталог должен существовать",
    )
    build.add_argument(
        "--object-snapshot",
        type=Path,
        default=None,
        help="дополнительно снимок уровня объектов (первый фрагмент артефакта, #407)",
    )
    build.add_argument(
        "--snapshot-format",
        choices=tuple(_SNAPSHOT_FORMATS),
        default=None,
        help=(
            "1 — search_index/1, один JSON; 2 — search_index/2: метаданные и "
            "файл векторов рядом (#413). По умолчанию 1, для "
            f"{LOCAL_MODEL_EMBEDDER_NAME} — 2"
        ),
    )
    _add_embedder_argument(build, required=True)

    query = commands.add_parser(
        "query", help="выполнить запрос и собрать контекст"
    )
    _add_corpus_arguments(query)
    query.add_argument("--query", required=True, help="текст запроса")
    query.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="снимок, построенный командой build; необязателен",
    )
    query.add_argument(
        "--object-snapshot",
        type=Path,
        default=None,
        help="снимок уровня объектов: слияние рангов двух уровней (#407)",
    )
    query.add_argument(
        "--search-mode",
        choices=_SEARCH_MODES,
        default="auto",
        help="auto — точный поиск, затем смысловой; по умолчанию auto",
    )
    query.add_argument(
        "--top-k", type=int, default=5, help="число смысловых кандидатов"
    )
    for flag, help_text in (
        ("--artifact-kind", "вид артефакта: " + ", ".join(sorted(ARTIFACT_KINDS))),
        ("--module-kind", "вид модуля (module_kind)"),
        ("--owner-kind", "вид владельца (owner_kind)"),
        ("--metadata-type", "тип метаданных владельца"),
        ("--owner-name", "имя владельца"),
    ):
        query.add_argument(
            flag,
            action="append",
            default=None,
            metavar="VALUE",
            help=help_text + "; флаг повторяемый",
        )
    query.add_argument(
        "--max-chars",
        type=int,
        default=-1,
        help="лимит длины контекста; -1 без ограничения",
    )
    query.add_argument(
        "--include-text",
        action="store_true",
        help="включить исходный текст в JSON",
    )
    _add_embedder_argument(query, required=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Выполнить команду и вернуть код выхода без завершения процесса."""
    _configure_output()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        module_groups = _resolve_module_groups(parser, args)
        _check_embedder_arguments(parser, args)
        filters = _resolve_query(parser, args) if args.command == "query" else None
    except SystemExit as exc:
        return _exit_code_from_system_exit(exc)

    export_root = Path(args.export_root)
    if not export_root.is_dir():
        print(
            f"{_PROGRAM}: корень выгрузки не найден или не является каталогом",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT

    payload = _base_payload(args.command)
    try:
        if args.command == "build":
            code = _run_build(args, export_root, module_groups, payload)
        else:
            code = _run_query(args, export_root, module_groups, filters, payload)
    except Exception as exc:  # noqa: BLE001 — внешняя fail-closed граница CLI
        # str/repr исключения не печатается: в нём может быть путь.
        payload = _base_payload(args.command)
        code = _fail(payload, _INTERNAL_ERROR, EXIT_FATAL)
        print(
            f"{_PROGRAM}: внутренняя ошибка: {_INTERNAL_ERROR} "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        sys.stdout.write(to_canonical_json(payload))
        return code
    sys.stdout.write(to_canonical_json(payload))
    _report(payload, code)
    return code


def _base_payload(command: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SEARCH_CLI_SCHEMA,
        "command": command,
        "status": "ok",
        "error": None,
    }
    if command == "build":
        payload.update({"corpus": None, "fragments": None, "snapshot": None})
    else:
        payload.update({"snapshot": None, "context": None})
    return payload


def _fail(payload: dict[str, Any], error_code: str, code: int) -> int:
    payload["status"] = _STATUS_BY_CODE[code]
    payload["error"] = {"code": error_code, "message": _MESSAGES[error_code]}
    return code


def _fragmentation(
    args: argparse.Namespace,
    export_root: Path,
    module_groups: tuple[str, ...] | None,
) -> FragmentationResult:
    corpus = collect_search_corpus(
        export_root,
        mode=args.mode,
        include_common_modules=not args.skip_common_modules,
        module_groups=module_groups,
    )
    return fragment_corpus(corpus)


def _corpus_summary(fragmentation: FragmentationResult) -> dict[str, Any]:
    report = fragmentation.corpus_report
    if report is None:
        raise RuntimeError("corpus report is missing")
    return {"total": len(report.items), "counts": report.counts()}


def _run_build(
    args: argparse.Namespace,
    export_root: Path,
    module_groups: tuple[str, ...] | None,
    payload: dict[str, Any],
) -> int:
    try:
        fragmentation = _fragmentation(args, export_root, module_groups)
    except SearchCorpusError as exc:
        return _fail(payload, exc.code, EXIT_FATAL)
    payload["corpus"] = _corpus_summary(fragmentation)
    payload["fragments"] = len(fragmentation.fragments)
    try:
        embedder = _make_embedder(args)
    except LocalModelError as exc:
        return _fail(payload, exc.code, EXIT_FATAL)
    fmt = args.snapshot_format or (
        "2" if args.embedder == LOCAL_MODEL_EMBEDDER_NAME else "1"
    )
    schema = _SNAPSHOT_FORMATS[fmt]
    try:
        index = build_search_snapshot(
            fragmentation, embedder, Path(args.snapshot), schema=schema,
        )
        object_index = None
        if args.object_snapshot is not None:
            object_index = build_object_snapshot(
                fragmentation, embedder, Path(args.object_snapshot), schema=schema,
            )
    except SearchIndexError as exc:
        if exc.code not in ("embedder_failed", "invalid_vector"):
            raise
        return _fail(payload, exc.code, EXIT_FATAL)
    except OSError:
        return _fail(payload, _SNAPSHOT_WRITE_FAILED, EXIT_WRITE_ERROR)
    payload["snapshot"] = {
        "schema": schema,
        "representation_version": index.representation_version,
        "embedder": index.embedder.to_dict(),
        "entries": len(index.entries),
    }
    if object_index is not None:
        payload["object_snapshot"] = {
            "schema": schema,
            "entries": len(object_index.entries),
        }
    return EXIT_OK


def _load_snapshot(path: Path | None) -> tuple[SearchIndex | None, str | None]:
    if path is None:
        return None, None
    if not path.exists():
        return None, _SNAPSHOT_NOT_FOUND
    try:
        return load_index(path), None
    except SearchIndexError as exc:
        return None, exc.code


def _run_query(
    args: argparse.Namespace,
    export_root: Path,
    module_groups: tuple[str, ...] | None,
    filters: SearchFilters | None,
    payload: dict[str, Any],
) -> int:
    try:
        fragmentation = _fragmentation(args, export_root, module_groups)
    except SearchCorpusError as exc:
        return _fail(payload, exc.code, EXIT_FATAL)

    snapshot_path = None if args.snapshot is None else Path(args.snapshot)
    index, error_code = _load_snapshot(snapshot_path)
    try:
        embedder = _make_embedder(args) if args.embedder is not None else None
    except LocalModelError as exc:
        return _fail(payload, exc.code, EXIT_FATAL)
    object_index, object_error = _load_snapshot(
        None if args.object_snapshot is None else Path(args.object_snapshot)
    )
    object_degraded = False
    if args.object_snapshot is not None:
        object_status: dict[str, Any] = {
            "loaded": object_index is not None,
            "error_code": object_error,
            "compatibility": None,
            "freshness": None,
        }
        if object_index is not None and embedder is not None:
            ostatus = object_index.assess(embedder.identity, object_level(fragmentation))
            object_status["compatibility"] = ostatus.compatibility
            object_status["freshness"] = ostatus.freshness
        payload["object_snapshot"] = object_status
        object_degraded = object_error is not None or (
            object_status["compatibility"] == "incompatible"
            or object_status["freshness"] == "stale"
        )
    compatibility: str | None = None
    freshness: str | None = None
    if index is not None and embedder is not None:
        status = index.assess(embedder.identity, fragmentation)
        compatibility, freshness = status.compatibility, status.freshness
    payload["snapshot"] = {
        "provided": snapshot_path is not None,
        "loaded": index is not None,
        "error_code": error_code,
        "compatibility": compatibility,
        "freshness": freshness,
    }

    context = search_with_context(
        fragmentation,
        args.query,
        search_mode=args.search_mode,
        top_k=args.top_k,
        filters=filters,
        index=index,
        embedder=embedder,
        object_index=object_index,
        max_chars=args.max_chars,
    )
    payload["context"] = context.to_dict(include_text=args.include_text)

    code = EXIT_OK
    search_error = context.dispatch.result.error
    if search_error is not None:
        payload["error"] = {
            "code": search_error.code,
            "message": search_error.message,
        }
        if search_error.code in _DEGRADED_SEARCH_ERRORS:
            code = EXIT_DEGRADED
        else:
            code = EXIT_FATAL
    snapshot_degraded = object_degraded or error_code is not None or (
        compatibility == "incompatible" or freshness == "stale"
    )
    if code == EXIT_OK and (snapshot_degraded or context.status not in _OK_CONTEXT):
        code = EXIT_DEGRADED
    payload["status"] = _STATUS_BY_CODE[code]
    return code


def _report(payload: dict[str, Any], code: int) -> None:
    """Однострочная сводка в stderr для неуспешного завершения."""
    if code == EXIT_OK:
        return
    reasons: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict):
        reasons.append(str(error["code"]))
    snapshot = payload.get("snapshot")
    if payload["command"] == "query" and isinstance(snapshot, dict):
        if snapshot["error_code"] is not None:
            reasons.append("snapshot:" + str(snapshot["error_code"]))
        for key in ("compatibility", "freshness"):
            if snapshot[key] in ("incompatible", "stale"):
                reasons.append("snapshot:" + str(snapshot[key]))
    context = payload.get("context")
    if isinstance(context, dict) and context["status"] not in _OK_CONTEXT:
        reasons.append("context:" + str(context["status"]))
    print(
        f"{_PROGRAM}: {payload['status']}: {', '.join(reasons)}",
        file=sys.stderr,
    )


_LOCAL_FLAGS = ("model", "model_name", "model_revision", "cache_dir")


def _check_embedder_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Параметры локальной модели — только с её эмбеддером (код 2)."""
    if args.embedder == LOCAL_MODEL_EMBEDDER_NAME:
        if args.model is None or args.model_name is None:
            parser.error("--embedder sentence-transformers требует --model и --model-name")
        if not Path(args.model).is_dir():
            parser.error("каталог --model не найден")
        if args.max_tokens < 1 or args.batch_size < 1:
            parser.error("--max-tokens и --batch-size: ожидается число >= 1")
        return
    if (any(getattr(args, name) is not None for name in _LOCAL_FLAGS) or args.progress
            or args.precision != "fp32"):
        parser.error("параметры локальной модели допустимы только с --embedder sentence-transformers")


def _make_embedder(args: argparse.Namespace) -> Embedder:
    if args.embedder != LOCAL_MODEL_EMBEDDER_NAME:
        return HashedBagOfWordsDemoEmbedder()

    def progress(line: str) -> None:
        print(f"{_PROGRAM}: {sanitize_diagnostic(line)}", file=sys.stderr, flush=True)

    return SentenceTransformerEmbedder(
        args.model,
        model_name=args.model_name,
        model_revision=args.model_revision,
        device=args.device,
        max_tokens=args.max_tokens,
        batch_size=args.batch_size,
        precision=args.precision,
        cache_dir=args.cache_dir,
        progress=progress if args.progress else None,
    )


def _resolve_module_groups(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[str, ...] | None:
    """Свести флаги групп к набору по семантике #346.

    Индекс модулей в поиске строится всегда, поэтому флаг
    ``--include-module-index`` не нужен. Введённые значения в сообщениях не
    повторяются.
    """
    include = args.module_group
    skip = args.skip_module_group
    if include is None and skip is None:
        return None
    if include is not None and skip is not None:
        parser.error("--module-group и --skip-module-group несовместимы")
    names = include if include is not None else skip
    if any(name not in MODULE_GROUPS for name in names):
        parser.error(
            "неизвестная группа модулей; допустимые: " + ", ".join(MODULE_GROUPS)
        )
    chosen = frozenset(names)
    if include is not None:
        return tuple(name for name in MODULE_GROUPS if name in chosen)
    rest = tuple(name for name in MODULE_GROUPS if name not in chosen)
    if not rest:
        parser.error("исключены все группы модулей")
    return rest


def _values(
    parser: argparse.ArgumentParser,
    raw: list[str] | None,
    allowed: frozenset[str] | None,
    flag: str,
) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if allowed is not None and any(value not in allowed for value in raw):
        parser.error(
            f"недопустимое значение {flag}; допустимые: " + ", ".join(sorted(allowed))
        )
    return tuple(raw)


def _resolve_query(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> SearchFilters:
    """Проверить запрос, фильтры и бюджет до сбора корпуса (код 2)."""
    if args.max_chars < -1:
        parser.error("--max-chars: ожидается -1 или неотрицательное число")
    artifact_kinds = _values(
        parser, args.artifact_kind, ARTIFACT_KINDS, "--artifact-kind"
    )
    module_kinds = _values(parser, args.module_kind, MODULE_KINDS, "--module-kind")
    owner_kinds = _values(parser, args.owner_kind, OWNER_KINDS, "--owner-kind")
    try:
        filters = SearchFilters(
            artifact_kinds=cast(tuple[ArtifactKind, ...] | None, artifact_kinds),
            module_kinds=cast(tuple[ModuleKind, ...] | None, module_kinds),
            owner_kinds=cast(tuple[OwnerKind, ...] | None, owner_kinds),
            metadata_types=_values(
                parser, args.metadata_type, None, "--metadata-type"
            ),
            owner_names=_values(parser, args.owner_name, None, "--owner-name"),
        )
    except (TypeError, ValueError):
        parser.error("некорректный фильтр")
    try:
        SearchQuery(
            args.query, mode=args.search_mode, top_k=args.top_k, filters=filters
        )
    except (TypeError, ValueError):
        parser.error(
            "некорректный запрос: пустой текст, управляющие символы или --top-k < 1"
        )
    return filters


def _exit_code_from_system_exit(exc: SystemExit) -> int:
    """``SystemExit`` argparse → код: ``--help`` — 0, ошибка аргументов — 2."""
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    return EXIT_BAD_INPUT


def _configure_output() -> None:
    """Перевести стандартные потоки в UTF-8 для Windows-консоли и CI."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
