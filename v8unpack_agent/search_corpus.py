"""Адаптеры форм и модулей к общему поисковому корпусу (issue #323).

Модуль превращает уже построенные результаты существующих сканеров —
``FormContext`` (#77), ``ModuleIndex`` (#203–#207, #337, #351) и
``CommonModuleIndex`` (#151) — в документы контракта ``search_contract/1``
(#322), связи владельцев и карточки владельцев. Параллельного обхода
выгрузки нет: текст модуля читается только по ``relative_path`` записи,
которую уже построил сканер. Контракт описан в ``docs/search_corpus.md``.

Модуль не вызывает эмбеддер, не строит индекс, не фрагментирует код (#324)
и не исполняет найденный код.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, get_args

from v8unpack_agent.modules import (
    ModuleEntry,
    ModuleIndex,
    ModuleReadStatus,
    classify_bsl_bytes,
    module_entry_from_common_module,
)
from v8unpack_agent.search_contract import (
    ArtifactRef,
    LinkBasis,
    OwnerLink,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    identity_key,
    to_canonical_json,
    validate_relative_source_path,
)

if TYPE_CHECKING:
    from v8unpack_agent.common_modules import CommonModuleIndex
    from v8unpack_agent.form_context import FormContext

__all__ = [
    "CORPUS_REASONS",
    "CORPUS_SOURCES",
    "SEARCH_CORPUS_SCHEMA",
    "CorpusItem",
    "CorpusReason",
    "CorpusReport",
    "CorpusSource",
    "FormReadFailure",
    "ModuleText",
    "SearchCorpus",
    "build_form_inputs",
    "build_search_corpus",
    "read_module_text",
]

SEARCH_CORPUS_SCHEMA = "search_corpus/1"

CorpusSource = Literal["common_modules", "forms", "module_index", "owner_cards"]
CorpusReason = Literal[
    "content_changed",
    "duplicate_artifact",
    "empty",
    "form_module_via_form_context",
    "invalid_identity",
    "missing",
    "read_error",
    "unsafe_path",
    "whitespace_only",
]

CORPUS_SOURCES: frozenset[str] = frozenset(get_args(CorpusSource))
CORPUS_REASONS: frozenset[str] = frozenset(get_args(CorpusReason))

_SOURCE_ORDER: Mapping[str, int] = {
    "module_index": 0,
    "common_modules": 1,
    "forms": 2,
    "owner_cards": 3,
}
_EXTERNAL_FORM_TYPES = frozenset({"ExternalDataProcessor", "ExternalReport"})
_EXTERNAL_FORM_CONTAINERS = frozenset({"Form", "ReportForm"})
_COMMON_FORM = "CommonForm"
_BOM = chr(0xFEFF)
_LF = chr(10)
_CR = chr(13)


def _normalize_text(text: str) -> str:
    """Удалить ведущий BOM и привести переводы строк к LF (как #345)."""
    if text.startswith(_BOM):
        text = text[1:]
    return text.replace(_CR + _LF, _LF).replace(_CR, _LF)


def _text_status(text: str | None) -> ModuleReadStatus:
    if text is None:
        return "missing"
    normalized = _normalize_text(text)
    if normalized == "":
        return "empty"
    if normalized.strip() == "":
        return "whitespace_only"
    return "ok"


def _reason(status: str) -> CorpusReason:
    if status not in CORPUS_REASONS:
        raise ValueError(f"unknown corpus reason: {status!r}")
    return cast("CorpusReason", status)


# ---------------------------------------------------------------------------
# Чтение текста модуля
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleText:
    """Результат чтения модуля по записи ``ModuleEntry``.

    ``text`` задан только при ``reason is None``: текст без ведущего BOM,
    переводы строк — LF. ``reason`` — причина, по которой текста нет.
    """

    entry: ModuleEntry
    text: str | None
    reason: CorpusReason | None


def _read_bytes(export_root: Path, relative_path: str) -> tuple[bytes | None, str]:
    """Прочитать байты файла внутри корня; вернуть (данные, статус)."""
    root = Path(export_root)
    path = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "read_error"
    if not stat.S_ISREG(info.st_mode):
        return None, "unsafe_path"
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
        if resolved_root not in resolved.parents:
            return None, "unsafe_path"
        return path.read_bytes(), "ok"
    except OSError:
        return None, "read_error"


def _decoded(data: bytes) -> str:
    return _normalize_text(data.decode("utf-8-sig"))


def read_module_text(export_root: Path, entry: ModuleEntry) -> ModuleText:
    """Прочитать текст модуля по ``entry.relative_path`` без угадывания layout.

    Запись со статусом сканера, отличным от ``ok``, не читается: её статус
    становится причиной исключения. Для ``ok`` файл перечитывается внутри
    ``export_root``: symlink, не обычный файл и выход за корень дают
    ``unsafe_path``, ``OSError`` и невалидный UTF-8 — ``read_error``,
    исчезнувший файл — ``missing``. Если в записи есть ``sha256`` и он не
    совпадает с прочитанными байтами, причина — ``content_changed``.
    """
    if not isinstance(entry, ModuleEntry):
        raise TypeError("entry must be ModuleEntry")
    if entry.read_status != "ok":
        return ModuleText(entry=entry, text=None, reason=_reason(entry.read_status))
    data, status = _read_bytes(export_root, entry.relative_path)
    if data is None:
        return ModuleText(entry=entry, text=None, reason=_reason(status))
    if entry.sha256 is not None and hashlib.sha256(data).hexdigest() != entry.sha256:
        return ModuleText(entry=entry, text=None, reason="content_changed")
    classified = classify_bsl_bytes(data)
    if classified != "ok":
        return ModuleText(entry=entry, text=None, reason=_reason(classified))
    return ModuleText(entry=entry, text=_decoded(data), reason=None)


# ---------------------------------------------------------------------------
# Вход форм
# ---------------------------------------------------------------------------


class _FormContextLike(Protocol):
    """Поля ``FormContext``, которые читает адаптер."""

    @property
    def form_name(self) -> str: ...

    @property
    def container_name(self) -> str: ...

    @property
    def object_type(self) -> str: ...

    @property
    def object_name(self) -> str: ...

    @property
    def bsl_text(self) -> str | None: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FormReadFailure:
    """Форма, контекст которой не удалось построить (``read_error``)."""

    object_type: str
    object_name: str
    container_name: str
    form_name: str


def build_form_inputs(
    form_entries: Iterable[Any],
    unpacked_root: Path,
    **kwargs: Any,
) -> tuple[FormContext | FormReadFailure, ...]:
    """Построить ``FormContext`` через существующий ``build_form_context``.

    ``form_entries`` — записи ``scan_forms.FormEntry``. ``OSError`` и
    ``UnicodeDecodeError`` при построении контекста не прерывают сборку:
    форма возвращается как :class:`FormReadFailure` и попадает в отчёт с
    причиной ``read_error``. ``kwargs`` передаются в ``build_form_context``
    без изменений (например, ``type_resolver``).
    """
    from v8unpack_agent.form_context import build_form_context

    result: list[FormContext | FormReadFailure] = []
    for entry in form_entries:
        try:
            result.append(build_form_context(entry, unpacked_root, **kwargs))
        except (OSError, UnicodeDecodeError):
            result.append(
                FormReadFailure(
                    object_type=str(entry.object_type or ""),
                    object_name=str(entry.object_name or ""),
                    container_name=str(entry.container_name or ""),
                    form_name=str(entry.form_name or ""),
                )
            )
    return tuple(result)


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusItem:
    """Строка отчёта: один входной артефакт и его судьба.

    ``included`` — артефакт дал документ корпуса; иначе задан ``reason``.
    ``artifact_id`` равен ``None`` только при ``invalid_identity``.
    ``owner_id`` и ``owner_basis`` описывают связь с владельцем, если она
    есть; ``owner_basis`` — значение ``OwnerLink.basis``.
    """

    artifact_id: str | None
    artifact_kind: str
    source: CorpusSource
    source_path: str | None
    included: bool
    reason: CorpusReason | None = None
    owner_id: str | None = None
    owner_basis: LinkBasis | None = None

    def __post_init__(self) -> None:
        if self.source not in CORPUS_SOURCES:
            raise ValueError(f"unknown corpus source: {self.source!r}")
        if self.included != (self.reason is None):
            raise ValueError("reason is required for excluded items only")
        if self.reason is not None and self.reason not in CORPUS_REASONS:
            raise ValueError(f"unknown corpus reason: {self.reason!r}")
        if (self.artifact_id is None) != (self.reason == "invalid_identity"):
            raise ValueError("artifact_id may be None only for invalid_identity")
        if self.source_path is not None:
            validate_relative_source_path(self.source_path)

    def sort_key(self) -> tuple[str, str, int, str, str]:
        """Детерминированный порядок строк отчёта."""
        artifact = self.artifact_id or ""
        return (
            identity_key(artifact),
            artifact,
            _SOURCE_ORDER[self.source],
            self.source_path or "",
            self.reason or "",
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без текста и абсолютных путей."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "source": self.source,
            "source_path": self.source_path,
            "included": self.included,
            "reason": self.reason,
            "owner_id": self.owner_id,
            "owner_basis": self.owner_basis,
        }


@dataclass(frozen=True)
class CorpusReport:
    """Детерминированный отчёт о включённых и исключённых артефактах."""

    items: tuple[CorpusItem, ...] = ()

    def __post_init__(self) -> None:
        items = tuple(self.items)
        for item in items:
            if not isinstance(item, CorpusItem):
                raise TypeError("CorpusReport accepts only CorpusItem")
        object.__setattr__(self, "items", tuple(sorted(items, key=CorpusItem.sort_key)))

    @property
    def included(self) -> tuple[CorpusItem, ...]:
        """Строки, давшие документ."""
        return tuple(item for item in self.items if item.included)

    @property
    def excluded(self) -> tuple[CorpusItem, ...]:
        """Строки без документа."""
        return tuple(item for item in self.items if not item.included)

    @property
    def read_errors(self) -> tuple[CorpusItem, ...]:
        """Строки с ``read_error`` или ``unsafe_path`` — ошибки чтения."""
        return tuple(
            item for item in self.items if item.reason in ("read_error", "unsafe_path")
        )

    def counts(self) -> dict[str, dict[str, int]]:
        """Число строк по источнику и исходу (``included`` или причина)."""
        result: dict[str, dict[str, int]] = {}
        for item in self.items:
            outcome = "included" if item.reason is None else item.reason
            bucket = result.setdefault(item.source, {})
            bucket[outcome] = bucket.get(outcome, 0) + 1
        return {
            source: dict(sorted(bucket.items())) for source, bucket in sorted(result.items())
        }

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление отчёта."""
        return {
            "schema": SEARCH_CORPUS_SCHEMA,
            "total": len(self.items),
            "counts": self.counts(),
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self) -> str:
        """Детерминированный JSON отчёта."""
        return to_canonical_json(self.to_dict())


# ---------------------------------------------------------------------------
# Корпус
# ---------------------------------------------------------------------------


def _link_sort_key(link: OwnerLink) -> tuple[str, str, str]:
    return (link.artifact.identity, identity_key(link.owner.owner_id), link.basis)


@dataclass(frozen=True)
class SearchCorpus:
    """Результат адаптеров: документы, связи владельцев и отчёт."""

    documents: SearchDocumentSet
    owner_links: tuple[OwnerLink, ...]
    report: CorpusReport

    def __post_init__(self) -> None:
        if not isinstance(self.documents, SearchDocumentSet):
            raise TypeError("documents must be SearchDocumentSet")
        if not isinstance(self.report, CorpusReport):
            raise TypeError("report must be CorpusReport")
        links = tuple(self.owner_links)
        for link in links:
            if not isinstance(link, OwnerLink):
                raise TypeError("owner_links accepts only OwnerLink")
        object.__setattr__(self, "owner_links", tuple(sorted(links, key=_link_sort_key)))

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без текста документов."""
        return {
            "schema": SEARCH_CORPUS_SCHEMA,
            "documents": self.documents.to_dict(),
            "owner_links": [link.to_dict() for link in self.owner_links],
            "report": self.report.to_dict(),
        }

    def to_json(self) -> str:
        """Детерминированный JSON корпуса без текста документов."""
        return to_canonical_json(self.to_dict())


@dataclass
class _Builder:
    documents: list[SearchDocument]
    links: list[OwnerLink]
    items: list[CorpusItem]
    seen: set[str]

    def claim(self, artifact: ArtifactRef) -> bool:
        if artifact.identity in self.seen:
            return False
        self.seen.add(artifact.identity)
        return True


def _safe_source_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return validate_relative_source_path(value)
    except (TypeError, ValueError):
        return None


def _add_module(builder: _Builder, read: ModuleText, source: CorpusSource) -> None:
    entry = read.entry
    artifact = ArtifactRef.from_module_entry(entry)
    owner = artifact.owner
    if owner is None:
        raise RuntimeError("module artifact invariant violated")
    if entry.module_kind == "form":
        builder.items.append(
            CorpusItem(
                artifact_id=artifact.artifact_id,
                artifact_kind="module",
                source=source,
                source_path=entry.relative_path,
                included=False,
                reason="form_module_via_form_context",
            )
        )
        return
    if not builder.claim(artifact):
        builder.items.append(
            CorpusItem(
                artifact_id=artifact.artifact_id,
                artifact_kind="module",
                source=source,
                source_path=entry.relative_path,
                included=False,
                reason="duplicate_artifact",
            )
        )
        return
    builder.links.append(OwnerLink(owner=owner, artifact=artifact, basis="module_entry"))
    included = read.text is not None
    if read.text is not None:
        builder.documents.append(SearchDocument(artifact=artifact, text=read.text))
    builder.items.append(
        CorpusItem(
            artifact_id=artifact.artifact_id,
            artifact_kind="module",
            source=source,
            source_path=entry.relative_path,
            included=included,
            reason=read.reason,
            owner_id=owner.owner_id,
            owner_basis="module_entry",
        )
    )


def _common_module_reads(
    export_root: Path, common_modules: CommonModuleIndex
) -> list[ModuleText]:
    reads: list[ModuleText] = []
    for common in common_modules.modules:
        relative = Path(common.bsl_path).as_posix()
        data, status = _read_bytes(export_root, relative)
        if data is None:
            read_status: ModuleReadStatus = "missing" if status == "missing" else "read_error"
            entry = module_entry_from_common_module(common, read_status)
            reads.append(ModuleText(entry=entry, text=None, reason=_reason(status)))
            continue
        classified = classify_bsl_bytes(data)
        entry = module_entry_from_common_module(
            common,
            classified,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        if classified != "ok":
            reads.append(ModuleText(entry=entry, text=None, reason=_reason(classified)))
        else:
            reads.append(ModuleText(entry=entry, text=_decoded(data), reason=None))
    return reads


def _form_parts(source_path: str | None) -> tuple[str, ...]:
    return () if source_path is None else tuple(source_path.split("/"))


def _metadata_form_owner(
    key: tuple[str, str, str, str], parts: tuple[str, ...]
) -> OwnerRef | None:
    """Владелец формы 4-уровневого layout, доказанного путём ``scan_forms``."""
    object_type, object_name, _container, _form = key
    if object_type in _EXTERNAL_FORM_TYPES or object_type == _COMMON_FORM:
        return None
    if parts != key or not object_name:
        return None
    try:
        return OwnerRef("metadata_object", object_type, object_name)
    except (TypeError, ValueError):
        return None


def _candidate_owner(
    key: tuple[str, str, str, str],
    parts: tuple[str, ...],
    external_owners: Mapping[str, OwnerRef],
) -> OwnerRef | None:
    """Предполагаемый владелец без основания в контракте #322."""
    object_type, object_name, container, form_name = key
    if (
        object_type == _COMMON_FORM
        and container == _COMMON_FORM
        and not object_name
        and parts == (_COMMON_FORM, form_name)
    ):
        try:
            return OwnerRef("common_form", _COMMON_FORM, form_name)
        except (TypeError, ValueError):
            return None
    if (
        object_type in _EXTERNAL_FORM_TYPES
        and container in _EXTERNAL_FORM_CONTAINERS
        and len(parts) >= 3
        and parts[-3:] == (object_name, container, form_name)
    ):
        artifact_dir = "/".join(parts[:-2])
        owner = external_owners.get(identity_key(artifact_dir))
        if owner is not None and owner.metadata_type == object_type:
            return owner
    return None


def _add_form(
    builder: _Builder,
    form: _FormContextLike | FormReadFailure,
    external_owners: Mapping[str, OwnerRef],
) -> None:
    key = (
        str(form.object_type or ""),
        str(form.object_name or ""),
        str(form.container_name or ""),
        str(form.form_name or ""),
    )
    source_path = (
        None
        if isinstance(form, FormReadFailure)
        else _safe_source_path(form.metadata.get("form_path"))
    )
    parts = _form_parts(source_path)
    owner = _metadata_form_owner(key, parts)
    try:
        artifact = ArtifactRef.form(*key, owner=owner, source_path=source_path)
    except (TypeError, ValueError):
        builder.items.append(
            CorpusItem(
                artifact_id=None,
                artifact_kind="form",
                source="forms",
                source_path=source_path,
                included=False,
                reason="invalid_identity",
            )
        )
        return
    if not builder.claim(artifact):
        builder.items.append(
            CorpusItem(
                artifact_id=artifact.artifact_id,
                artifact_kind="form",
                source="forms",
                source_path=source_path,
                included=False,
                reason="duplicate_artifact",
            )
        )
        return
    basis: LinkBasis | None = None
    link_owner: OwnerRef | None = None
    if owner is not None:
        basis, link_owner = "form_key", owner
    else:
        candidate = _candidate_owner(key, parts, external_owners)
        if candidate is not None:
            basis, link_owner = "unconfirmed", candidate
    if basis is not None and link_owner is not None:
        builder.links.append(OwnerLink(owner=link_owner, artifact=artifact, basis=basis))
    reason: CorpusReason | None = "read_error"
    if not isinstance(form, FormReadFailure):
        status = _text_status(form.bsl_text)
        reason = None if status == "ok" else _reason(status)
        if reason is None and form.bsl_text is not None:
            builder.documents.append(
                SearchDocument(artifact=artifact, text=_normalize_text(form.bsl_text))
            )
    builder.items.append(
        CorpusItem(
            artifact_id=artifact.artifact_id,
            artifact_kind="form",
            source="forms",
            source_path=source_path,
            included=reason is None,
            reason=reason,
            owner_id=None if link_owner is None else link_owner.owner_id,
            owner_basis=basis,
        )
    )


def _card_text(owner: OwnerRef, links: Iterable[OwnerLink]) -> str:
    lines = ["OWNER", f"owner_kind: {owner.owner_kind}"]
    if owner.metadata_type is not None:
        lines.append(f"metadata_type: {owner.metadata_type}")
    if owner.owner_name is not None:
        lines.append(f"owner: {owner.owner_name}")
    lines.append("links:")
    lines.extend(
        f"{link.artifact.artifact_kind} {link.artifact.artifact_id} basis={link.basis}"
        for link in sorted(links, key=_link_sort_key)
    )
    return _LF.join(lines) + _LF


def _add_owner_cards(builder: _Builder) -> None:
    owners: dict[str, OwnerRef] = {}
    confirmed: dict[str, list[OwnerLink]] = {}
    for link in builder.links:
        if not link.confirmed:
            continue
        key = identity_key(link.owner.owner_id)
        owners.setdefault(key, link.owner)
        confirmed.setdefault(key, []).append(link)
    for key in sorted(owners):
        owner = owners[key]
        artifact = ArtifactRef.owner_card(owner)
        if not builder.claim(artifact):
            builder.items.append(
                CorpusItem(
                    artifact_id=artifact.artifact_id,
                    artifact_kind="owner",
                    source="owner_cards",
                    source_path=None,
                    included=False,
                    reason="duplicate_artifact",
                )
            )
            continue
        builder.documents.append(
            SearchDocument(artifact=artifact, text=_card_text(owner, confirmed[key]))
        )
        builder.items.append(
            CorpusItem(
                artifact_id=artifact.artifact_id,
                artifact_kind="owner",
                source="owner_cards",
                source_path=None,
                included=True,
            )
        )


def _module_sort_key(read: ModuleText) -> tuple[str, str, str]:
    entry = read.entry
    return (identity_key(entry.module_id), entry.relative_path.casefold(), entry.relative_path)


def _form_sort_key(form: _FormContextLike | FormReadFailure) -> tuple[str, str, str]:
    key = "/".join(
        str(part or "")
        for part in (form.object_type, form.object_name, form.container_name, form.form_name)
    )
    if isinstance(form, FormReadFailure):
        return (identity_key(key), key, "1:")
    return (identity_key(key), key, "0:" + str(form.metadata.get("form_path") or ""))


def build_search_corpus(
    export_root: Path,
    *,
    module_index: ModuleIndex | None = None,
    common_modules: CommonModuleIndex | None = None,
    forms: Iterable[_FormContextLike | FormReadFailure] = (),
) -> SearchCorpus:
    """Собрать поисковый корпус из результатов существующих сканеров.

    ``module_index`` — результат ``scan_*_modules`` (#204–#207, #337, #351),
    ``common_modules`` — ``scan_common_modules`` (#151), ``forms`` —
    ``FormContext`` и :class:`FormReadFailure` (см. :func:`build_form_inputs`).
    Текст модулей читается из ``export_root`` по ``relative_path`` записей.

    Документ создаётся только из прочитанного непустого текста. Модуль формы
    учитывается только как артефакт формы, общий модуль — один раз при
    повторе в нескольких входах. Порядок результата не зависит от порядка
    входа.
    """
    root = Path(export_root)
    builder = _Builder(documents=[], links=[], items=[], seen=set())

    module_reads: list[ModuleText] = []
    if module_index is not None:
        if not isinstance(module_index, ModuleIndex):
            raise TypeError("module_index must be ModuleIndex")
        module_reads = sorted(
            (read_module_text(root, entry) for entry in module_index),
            key=_module_sort_key,
        )
    for read in module_reads:
        _add_module(builder, read, "module_index")

    if common_modules is not None:
        for read in sorted(_common_module_reads(root, common_modules), key=_module_sort_key):
            _add_module(builder, read, "common_modules")

    external_owners: dict[str, OwnerRef] = {}
    for read in module_reads:
        module = read.entry
        if module.owner_kind in ("external_data_processor", "external_report"):
            artifact_dir = PurePosixPath(module.relative_path).parent.as_posix()
            external_owners.setdefault(
                identity_key(artifact_dir), OwnerRef.from_module_entry(module)
            )

    for form in sorted(forms, key=_form_sort_key):
        _add_form(builder, form, external_owners)

    _add_owner_cards(builder)

    return SearchCorpus(
        documents=SearchDocumentSet.from_documents(builder.documents),
        owner_links=tuple(builder.links),
        report=CorpusReport(items=tuple(builder.items)),
    )
