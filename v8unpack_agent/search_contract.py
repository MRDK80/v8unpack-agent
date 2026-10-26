"""Типизированный контракт общего поиска по формам и модулям 1С (issue #322).

Модуль задаёт неизменяемые модели поискового документа, запроса, результата
и связи владельца с артефактом. Он не читает файлы, не вызывает эмбеддер, не
хранит индекс и не знает конкретного layout выгрузки. Контракт описан в
``docs/search_contract.md``.

Идентичность модуля совпадает с ``ModuleEntry.module_id`` (#203); ключ формы
повторяет четвёрку ``(object_type, object_name, container_name, form_name)``
из ``FormRagIndex`` и ``drift_checker.form_key``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, get_args

from v8unpack_agent.modules import (
    MODULE_KINDS,
    OWNER_KINDS,
    ModuleEntry,
    ModuleKind,
    OwnerKind,
)

__all__ = [
    "ARTIFACT_KINDS",
    "FORM_ID_PREFIX",
    "LINK_BASES",
    "MATCH_FIELDS",
    "MATCH_METHODS",
    "OWNER_ID_PREFIX",
    "QUERY_MODES",
    "SEARCH_CONTRACT_SCHEMA",
    "SEARCH_ERROR_CODES",
    "SEARCH_STATUSES",
    "ArtifactKind",
    "ArtifactRef",
    "FormKey",
    "LinkBasis",
    "MatchField",
    "MatchMethod",
    "OwnerLink",
    "OwnerRef",
    "QueryMode",
    "SearchDocument",
    "SearchDocumentSet",
    "SearchError",
    "SearchErrorCode",
    "SearchFilters",
    "SearchHit",
    "SearchQuery",
    "SearchResult",
    "SearchStatus",
    "TextSpan",
    "encode_id_segment",
    "identity_key",
    "to_canonical_json",
    "validate_relative_source_path",
]

SEARCH_CONTRACT_SCHEMA = "search_contract/1"

ArtifactKind = Literal["form", "module", "owner"]
SearchStatus = Literal["ambiguous", "empty", "error", "exact", "semantic"]
MatchMethod = Literal["exact_id", "exact_name", "semantic"]
MatchField = Literal["artifact_id", "form_name", "owner_name"]
QueryMode = Literal["auto", "exact", "semantic"]
LinkBasis = Literal["form_key", "module_entry", "unconfirmed"]
SearchErrorCode = Literal[
    "embedder_failed",
    "incompatible_index",
    "index_not_ready",
    "internal",
    "invalid_vector",
]
FormKey = tuple[str, str, str, str]

ARTIFACT_KINDS: frozenset[str] = frozenset(get_args(ArtifactKind))
SEARCH_STATUSES: frozenset[str] = frozenset(get_args(SearchStatus))
MATCH_METHODS: frozenset[str] = frozenset(get_args(MatchMethod))
MATCH_FIELDS: frozenset[str] = frozenset(get_args(MatchField))
QUERY_MODES: frozenset[str] = frozenset(get_args(QueryMode))
LINK_BASES: frozenset[str] = frozenset(get_args(LinkBasis))
SEARCH_ERROR_CODES: frozenset[str] = frozenset(get_args(SearchErrorCode))

FORM_ID_PREFIX = "form"
OWNER_ID_PREFIX = "owner"

_EXACT_METHODS = frozenset({"exact_id", "exact_name"})
_ALLOWED_QUERY_CONTROLS = frozenset({chr(9), chr(10), chr(13)})
_BACKSLASH = chr(92)
_METADATA_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_SEGMENT_ESCAPES = (("%", "%25"), ("/", "%2F"), (":", "%3A"), ("#", "%23"))
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_ERROR_MESSAGES: Mapping[str, str] = {
    "embedder_failed": "embedder failed",
    "incompatible_index": "index snapshot is incompatible or stale",
    "index_not_ready": "index is not ready",
    "internal": "internal search error",
    "invalid_vector": "embedder returned an invalid vector",
}


# ---------------------------------------------------------------------------
# Общие проверки и идентичность
# ---------------------------------------------------------------------------


def _has_control_chars(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def identity_key(value: str) -> str:
    """Ключ сравнения идентификаторов без учёта регистра.

    Имена 1С регистронезависимы, а ``ModuleIndex`` отклоняет дубликаты
    ``module_id`` через ``casefold``. Здесь дополнительно применяется
    NFC-нормализация, чтобы составная и предсоставленная кириллица (``й``)
    давали один ключ. Сам идентификатор хранится без изменений.
    """
    if not isinstance(value, str):
        raise TypeError("identity value must be str")
    return unicodedata.normalize("NFC", value).casefold()


def encode_id_segment(value: str) -> str:
    """Экранировать сегмент идентификатора: ``%``, ``/``, ``:``, ``#``.

    Замена обратима (``%25``, ``%2F``, ``%3A``, ``%23``) и не трогает
    остальные символы, включая кириллицу, пробелы и точки. Управляющие
    символы отклоняются. Для имён модулей по #203 результат совпадает со
    входом, потому что зарезервированных символов в них не бывает.
    """
    if not isinstance(value, str):
        raise TypeError("id segment must be str")
    if _has_control_chars(value):
        raise ValueError("id segment must not contain control characters")
    for raw, escaped in _SEGMENT_ESCAPES:
        value = value.replace(raw, escaped)
    return value


def validate_relative_source_path(value: str) -> str:
    """Проверить относительный POSIX-путь источника и вернуть его без изменений.

    Правила те же, что у ``validate_relative_module_path`` (#203), но без
    требования суффикса ``.bsl``: источником формы может быть каталог или
    файл метаданных. Абсолютный путь, диск, UNC, обратная косая черта,
    ``..``, ``.``, пустой сегмент, ``:`` и имена устройств отклоняются.
    """
    if not isinstance(value, str):
        raise TypeError("source_path must be str")
    if not value:
        raise ValueError("source_path must not be empty")
    if _has_control_chars(value):
        raise ValueError("source_path must not contain control characters")
    if _BACKSLASH in value:
        raise ValueError("source_path must use '/' as separator")
    windows = PureWindowsPath(value)
    if (
        PurePosixPath(value).is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
    ):
        raise ValueError("source_path must be relative to export root")
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise ValueError(
                "source_path must not contain empty, '.' or '..' segments"
            )
        if ":" in segment:
            raise ValueError("source_path segment must not contain ':'")
        if segment != segment.rstrip(" ."):
            raise ValueError("source_path segment must not end with space or dot")
        if segment.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ValueError(
                "source_path segment must not be a reserved device name"
            )
    return value


def _check_keys(data: Mapping[str, Any], expected: frozenset[str], what: str) -> None:
    if not isinstance(data, Mapping):
        raise TypeError(f"{what} must be a mapping")
    if frozenset(data) != expected:
        raise ValueError(f"{what} keys do not match schema")


def _check_identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        raise ValueError(f"{what} must be a 1C identifier")
    return value


def _check_int(value: object, what: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{what} must be an int >= {minimum}")
    return value


def to_canonical_json(data: Mapping[str, Any]) -> str:
    """Детерминированный JSON: сортированные ключи, UTF-8 текст, LF в конце.

    NaN и бесконечности запрещены: ``allow_nan=False``.
    """
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# Владелец и артефакт
# ---------------------------------------------------------------------------


_OWNER_KEYS = frozenset(["owner_id", "owner_kind", "metadata_type", "owner_name"])


@dataclass(frozen=True)
class OwnerRef:
    """Владелец артефакта в терминах ``ModuleEntry`` (#203).

    Правила совпадают с ``ModuleEntry``: у ``configuration`` нет
    ``metadata_type`` и ``owner_name``, у остальных видов оба поля
    обязательны; ``metadata_type`` — ASCII-идентификатор, ``owner_name`` —
    идентификаторы 1С через точку.
    """

    owner_kind: OwnerKind
    metadata_type: str | None
    owner_name: str | None

    def __post_init__(self) -> None:
        if self.owner_kind not in OWNER_KINDS:
            raise ValueError(f"unknown owner_kind: {self.owner_kind!r}")
        if self.owner_kind == "configuration":
            if self.metadata_type is not None or self.owner_name is not None:
                raise ValueError(
                    "configuration owner requires metadata_type and "
                    "owner_name to be None"
                )
            return
        if self.metadata_type is None or self.owner_name is None:
            raise ValueError(
                "non-configuration owner requires metadata_type and owner_name"
            )
        if not isinstance(self.metadata_type, str) or not (
            _METADATA_TYPE_RE.fullmatch(self.metadata_type)
        ):
            raise ValueError("metadata_type must be an ASCII identifier")
        if not isinstance(self.owner_name, str):
            raise TypeError("owner_name must be str or None")
        for part in self.owner_name.split("."):
            _check_identifier(part, "owner_name part")

    @property
    def owner_id(self) -> str:
        """``owner:<owner_kind>:<metadata_type>:<owner_name>``."""
        return ":".join(
            [
                OWNER_ID_PREFIX,
                self.owner_kind,
                self.metadata_type or "",
                self.owner_name or "",
            ]
        )

    @classmethod
    def from_module_entry(cls, entry: ModuleEntry) -> OwnerRef:
        """Владелец записи модуля без изменения значений."""
        if not isinstance(entry, ModuleEntry):
            raise TypeError("entry must be ModuleEntry")
        return cls(
            owner_kind=entry.owner_kind,
            metadata_type=entry.metadata_type,
            owner_name=entry.owner_name,
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "metadata_type": self.metadata_type,
            "owner_name": self.owner_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OwnerRef:
        """Восстановить из ``to_dict()`` со строгой проверкой ключей и id."""
        _check_keys(data, _OWNER_KEYS, "owner")
        owner = cls(
            owner_kind=data["owner_kind"],
            metadata_type=data["metadata_type"],
            owner_name=data["owner_name"],
        )
        if owner.owner_id != data["owner_id"]:
            raise ValueError("owner_id does not match owner fields")
        return owner


def _same_owner(left: OwnerRef | None, right: OwnerRef | None) -> bool:
    if left is None or right is None:
        return left is right
    return identity_key(left.owner_id) == identity_key(right.owner_id)


def _checked_form_key(value: object) -> FormKey:
    if not isinstance(value, tuple) or len(value) != 4:
        raise ValueError("form artifact requires form_key of four strings")
    for part in value:
        if not isinstance(part, str):
            raise TypeError("form_key parts must be str")
        if _has_control_chars(part):
            raise ValueError("form_key parts must not contain control characters")
    if not value[3]:
        raise ValueError("form_key form_name must not be empty")
    return (value[0], value[1], value[2], value[3])


_ARTIFACT_KEYS = frozenset(
    [
        "artifact_id",
        "artifact_kind",
        "owner",
        "module_kind",
        "form_key",
        "source_path",
    ]
)


@dataclass(frozen=True)
class ArtifactRef:
    """Идентичность искомого артефакта: модуль, форма или карточка владельца.

    * ``module`` — ``owner`` и ``module_kind`` обязательны, ``artifact_id``
      совпадает с ``ModuleEntry.module_id``.
    * ``form`` — обязателен ``form_key``; ``owner`` может быть ``None``,
      если адаптер не установил владельца.
    * ``owner`` — обязателен ``owner``, ``artifact_id`` равен ``owner_id``.

    ``source_path`` — относительный POSIX-путь от корня выгрузки или ``None``.
    """

    artifact_kind: ArtifactKind
    owner: OwnerRef | None = None
    module_kind: ModuleKind | None = None
    form_key: FormKey | None = None
    source_path: str | None = None

    def __post_init__(self) -> None:
        kind = self.artifact_kind
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unknown artifact_kind: {kind!r}")
        if self.owner is not None and not isinstance(self.owner, OwnerRef):
            raise TypeError("owner must be OwnerRef or None")
        if self.module_kind is not None and self.module_kind not in MODULE_KINDS:
            raise ValueError(f"unknown module_kind: {self.module_kind!r}")
        if kind == "module":
            if self.owner is None or self.module_kind is None:
                raise ValueError("module artifact requires owner and module_kind")
            if self.form_key is not None:
                raise ValueError("module artifact must not have form_key")
        elif kind == "form":
            if self.module_kind is not None:
                raise ValueError("form artifact must not have module_kind")
            object.__setattr__(self, "form_key", _checked_form_key(self.form_key))
        else:
            if self.owner is None:
                raise ValueError("owner artifact requires owner")
            if self.module_kind is not None or self.form_key is not None:
                raise ValueError(
                    "owner artifact must not have module_kind or form_key"
                )
        if self.source_path is not None:
            validate_relative_source_path(self.source_path)

    @property
    def artifact_id(self) -> str:
        """Стабильный идентификатор артефакта, не зависящий от ОС и layout."""
        owner = self.owner
        if self.artifact_kind == "module":
            module_kind = self.module_kind
            if owner is None or module_kind is None:
                raise RuntimeError("module artifact invariant violated")
            return ":".join(
                [
                    owner.owner_kind,
                    owner.metadata_type or "",
                    owner.owner_name or "",
                    module_kind,
                ]
            )
        if self.artifact_kind == "form":
            form_key = self.form_key
            if form_key is None:
                raise RuntimeError("form artifact invariant violated")
            return FORM_ID_PREFIX + ":" + "/".join(
                encode_id_segment(part) for part in form_key
            )
        if owner is None:
            raise RuntimeError("owner artifact invariant violated")
        return owner.owner_id

    @property
    def identity(self) -> str:
        """Ключ сравнения ``artifact_id`` (см. :func:`identity_key`)."""
        return identity_key(self.artifact_id)

    def sort_key(self) -> tuple[str, str]:
        """Детерминированный ключ сортировки."""
        return (self.identity, self.artifact_id)

    @classmethod
    def from_module_entry(cls, entry: ModuleEntry) -> ArtifactRef:
        """Артефакт модуля по записи #203: id равен ``entry.module_id``."""
        return cls(
            artifact_kind="module",
            owner=OwnerRef.from_module_entry(entry),
            module_kind=entry.module_kind,
            source_path=entry.relative_path,
        )

    @classmethod
    def form(
        cls,
        object_type: str,
        object_name: str,
        container_name: str,
        form_name: str,
        *,
        owner: OwnerRef | None = None,
        source_path: str | None = None,
    ) -> ArtifactRef:
        """Артефакт формы по ключу ``FormRagIndex`` / ``form_key``."""
        return cls(
            artifact_kind="form",
            owner=owner,
            form_key=(object_type, object_name, container_name, form_name),
            source_path=source_path,
        )

    @classmethod
    def owner_card(
        cls, owner: OwnerRef, *, source_path: str | None = None
    ) -> ArtifactRef:
        """Артефакт карточки владельца."""
        return cls(artifact_kind="owner", owner=owner, source_path=source_path)

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без текста и абсолютных путей."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "owner": None if self.owner is None else self.owner.to_dict(),
            "module_kind": self.module_kind,
            "form_key": None if self.form_key is None else list(self.form_key),
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactRef:
        """Восстановить из ``to_dict()`` со строгой проверкой ключей и id."""
        _check_keys(data, _ARTIFACT_KEYS, "artifact")
        raw_owner = data["owner"]
        raw_key = data["form_key"]
        if raw_key is not None and not isinstance(raw_key, list):
            raise ValueError("form_key must be a list or null")
        artifact = cls(
            artifact_kind=data["artifact_kind"],
            owner=None if raw_owner is None else OwnerRef.from_dict(raw_owner),
            module_kind=data["module_kind"],
            form_key=None if raw_key is None else _checked_form_key(tuple(raw_key)),
            source_path=data["source_path"],
        )
        if artifact.artifact_id != data["artifact_id"]:
            raise ValueError("artifact_id does not match artifact fields")
        return artifact


# ---------------------------------------------------------------------------
# Документ и набор документов
# ---------------------------------------------------------------------------


_SPAN_KEYS = frozenset(["start_line", "end_line"])


@dataclass(frozen=True)
class TextSpan:
    """Диапазон строк исходного текста: 1-based, обе границы включительно.

    Строки считаются после удаления ведущего UTF-8 BOM и нормализации
    CRLF и CR в LF — так же, как в ``to_llm_module_fragment`` (#345).
    """

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        _check_int(self.start_line, "start_line", 1)
        _check_int(self.end_line, "end_line", 1)
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {"start_line": self.start_line, "end_line": self.end_line}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TextSpan:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _SPAN_KEYS, "span")
        return cls(start_line=data["start_line"], end_line=data["end_line"])


_DOCUMENT_KEYS = frozenset(
    ["document_id", "artifact", "procedure", "span", "text_chars", "text_sha256"]
)


@dataclass(frozen=True)
class SearchDocument:
    """Поисковый документ: артефакт целиком или его фрагмент.

    * Целый артефакт — ``procedure`` и ``span`` равны ``None``,
      ``document_id == artifact_id``.
    * Процедура — ``document_id == <artifact_id>#<procedure>``; ``span``
      необязателен и описывает строки процедуры.
    * Окно строк без процедуры — ``document_id == <artifact_id>#L<a>-<b>``.

    ``text`` — непустой текст для индексации. Он не попадает в
    ``to_dict()`` без явного ``include_text=True``: исходный код не
    считается обезличенным автоматически.
    """

    artifact: ArtifactRef
    text: str
    procedure: str | None = None
    span: TextSpan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            raise TypeError("artifact must be ArtifactRef")
        if not isinstance(self.text, str):
            raise TypeError("text must be str")
        if not self.text.strip():
            raise ValueError("document text must not be empty or whitespace")
        if self.procedure is not None:
            _check_identifier(self.procedure, "procedure")
        if self.span is not None and not isinstance(self.span, TextSpan):
            raise TypeError("span must be TextSpan or None")

    @property
    def document_id(self) -> str:
        """Идентификатор фрагмента или артефакта."""
        base = self.artifact.artifact_id
        if self.procedure is not None:
            return f"{base}#{self.procedure}"
        if self.span is not None:
            return f"{base}#L{self.span.start_line}-{self.span.end_line}"
        return base

    @property
    def text_sha256(self) -> str:
        """SHA-256 текста в UTF-8."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def sort_key(self) -> tuple[str, str, int, int, int, str, str]:
        """Порядок: артефакт, затем целый, процедуры, окна по номерам строк."""
        if self.procedure is not None:
            rank = 1
        elif self.span is not None:
            rank = 2
        else:
            rank = 0
        start = self.span.start_line if self.span is not None else 0
        end = self.span.end_line if self.span is not None else 0
        procedure = self.procedure or ""
        return (
            self.artifact.identity,
            self.artifact.artifact_id,
            rank,
            start,
            end,
            identity_key(procedure),
            procedure,
        )

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        """Сериализуемое представление; текст — только по явному запросу."""
        data: dict[str, Any] = {
            "document_id": self.document_id,
            "artifact": self.artifact.to_dict(),
            "procedure": self.procedure,
            "span": None if self.span is None else self.span.to_dict(),
            "text_chars": len(self.text),
            "text_sha256": self.text_sha256,
        }
        if include_text:
            data["text"] = self.text
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchDocument:
        """Восстановить из ``to_dict(include_text=True)``."""
        _check_keys(data, _DOCUMENT_KEYS | {"text"}, "document")
        raw_span = data["span"]
        document = cls(
            artifact=ArtifactRef.from_dict(data["artifact"]),
            text=data["text"],
            procedure=data["procedure"],
            span=None if raw_span is None else TextSpan.from_dict(raw_span),
        )
        if document.document_id != data["document_id"]:
            raise ValueError("document_id does not match document fields")
        if (
            document.text_sha256 != data["text_sha256"]
            or len(document.text) != data["text_chars"]
        ):
            raise ValueError("text does not match text_sha256 or text_chars")
        return document


@dataclass(frozen=True)
class SearchDocumentSet:
    """Неизменяемый детерминированно отсортированный набор документов.

    Допустимо: несколько документов одного артефакта (фрагменты),
    одинаковый текст у разных документов, одинаковые короткие имена у
    разных владельцев. Отклоняется: повтор ``document_id`` без учёта
    регистра и разные описания одного ``artifact_id``.
    """

    documents: tuple[SearchDocument, ...] = ()

    def __post_init__(self) -> None:
        documents = tuple(self.documents)
        seen_documents: set[str] = set()
        artifacts: dict[str, ArtifactRef] = {}
        for document in documents:
            if not isinstance(document, SearchDocument):
                raise TypeError("SearchDocumentSet accepts only SearchDocument")
            key = identity_key(document.document_id)
            if key in seen_documents:
                raise ValueError(f"duplicate document_id: {document.document_id!r}")
            seen_documents.add(key)
            known = artifacts.setdefault(document.artifact.identity, document.artifact)
            if known != document.artifact:
                raise ValueError(
                    "conflicting artifact description: "
                    f"{document.artifact.artifact_id!r}"
                )
        ordered = tuple(sorted(documents, key=SearchDocument.sort_key))
        object.__setattr__(self, "documents", ordered)

    @classmethod
    def from_documents(cls, documents: Iterable[SearchDocument]) -> SearchDocumentSet:
        """Построить набор из произвольной последовательности."""
        return cls(documents=tuple(documents))

    def __len__(self) -> int:
        return len(self.documents)

    def artifacts(self) -> tuple[ArtifactRef, ...]:
        """Уникальные артефакты набора в детерминированном порядке."""
        unique: dict[str, ArtifactRef] = {}
        for document in self.documents:
            unique.setdefault(document.artifact.identity, document.artifact)
        return tuple(sorted(unique.values(), key=ArtifactRef.sort_key))

    def select(self, filters: SearchFilters) -> SearchDocumentSet:
        """Документы, чьи артефакты проходят фильтры."""
        if not isinstance(filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")
        return SearchDocumentSet(
            documents=tuple(
                document
                for document in self.documents
                if filters.matches(document.artifact)
            )
        )

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        """Сериализуемое представление набора."""
        return {
            "schema": SEARCH_CONTRACT_SCHEMA,
            "total": len(self.documents),
            "documents": [
                document.to_dict(include_text=include_text)
                for document in self.documents
            ],
        }

    def to_json(self, *, include_text: bool = False) -> str:
        """Детерминированный JSON набора."""
        return to_canonical_json(self.to_dict(include_text=include_text))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchDocumentSet:
        """Восстановить из ``to_dict(include_text=True)``."""
        _check_keys(data, frozenset(["schema", "total", "documents"]), "document set")
        if data["schema"] != SEARCH_CONTRACT_SCHEMA:
            raise ValueError("unsupported search contract schema")
        raw = data["documents"]
        if not isinstance(raw, list):
            raise TypeError("documents must be a list")
        result = cls.from_documents(SearchDocument.from_dict(item) for item in raw)
        if data["total"] != len(result):
            raise ValueError("total does not match documents")
        return result


# ---------------------------------------------------------------------------
# Связь владельца с артефактом
# ---------------------------------------------------------------------------


_LINK_KEYS = frozenset(["owner", "artifact", "basis", "confirmed"])


@dataclass(frozen=True)
class OwnerLink:
    """Связь владельца с модулем или формой и её основание.

    * ``module_entry`` — владелец взят из ``ModuleEntry`` этого модуля.
    * ``form_key`` — ``metadata_type`` и ``owner_name`` владельца совпадают
      с ``object_type`` и ``object_name`` ключа формы.
    * ``unconfirmed`` — связь предполагается, но не доказана.
    """

    owner: OwnerRef
    artifact: ArtifactRef
    basis: LinkBasis

    def __post_init__(self) -> None:
        if not isinstance(self.owner, OwnerRef):
            raise TypeError("owner must be OwnerRef")
        if not isinstance(self.artifact, ArtifactRef):
            raise TypeError("artifact must be ArtifactRef")
        if self.basis not in LINK_BASES:
            raise ValueError(f"unknown link basis: {self.basis!r}")
        kind = self.artifact.artifact_kind
        if kind == "owner":
            raise ValueError("link target must be a module or form")
        if self.basis == "module_entry":
            if kind != "module" or not _same_owner(self.owner, self.artifact.owner):
                raise ValueError("module_entry link requires the module's own owner")
        elif self.basis == "form_key":
            key = self.artifact.form_key
            if (
                kind != "form"
                or key is None
                or self.owner.metadata_type != key[0]
                or self.owner.owner_name is None
                or identity_key(self.owner.owner_name) != identity_key(key[1])
            ):
                raise ValueError("form_key link requires owner matching form key")

    @property
    def confirmed(self) -> bool:
        """``True``, если основание связи доказано."""
        return self.basis != "unconfirmed"

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            "owner": self.owner.to_dict(),
            "artifact": self.artifact.to_dict(),
            "basis": self.basis,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OwnerLink:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _LINK_KEYS, "link")
        link = cls(
            owner=OwnerRef.from_dict(data["owner"]),
            artifact=ArtifactRef.from_dict(data["artifact"]),
            basis=data["basis"],
        )
        if link.confirmed is not data["confirmed"]:
            raise ValueError("confirmed does not match basis")
        return link


# ---------------------------------------------------------------------------
# Запрос и фильтры
# ---------------------------------------------------------------------------


def _normalized_values(
    values: object, what: str, allowed: frozenset[str] | None
) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise TypeError(f"{what} must be an iterable of str or None")
    items = tuple(values)
    if not items:
        raise ValueError(f"{what} must not be empty; use None for no filter")
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{what} items must be str")
        if allowed is not None and item not in allowed:
            raise ValueError(f"unknown {what} value: {item!r}")
    unique: dict[str, str] = {}
    for item in items:
        unique.setdefault(identity_key(item), item)
    return tuple(sorted(unique.values(), key=lambda v: (identity_key(v), v)))


def _tuple_or_none(value: list[Any] | None) -> Any:
    return None if value is None else tuple(value)


_FILTER_KEYS = frozenset(
    ["artifact_kinds", "module_kinds", "owner_kinds", "metadata_types", "owner_names"]
)


@dataclass(frozen=True)
class SearchFilters:
    """Фильтры выдачи. ``None`` — измерение не ограничено.

    Внутри измерения значения объединяются через ИЛИ, между измерениями —
    через И. ``module_kinds`` пропускает только модули. Фильтры владельца
    не пропускают артефакты без владельца. ``owner_names`` сравниваются
    без учёта регистра, ``metadata_types`` — точно.
    """

    artifact_kinds: tuple[ArtifactKind, ...] | None = None
    module_kinds: tuple[ModuleKind, ...] | None = None
    owner_kinds: tuple[OwnerKind, ...] | None = None
    metadata_types: tuple[str, ...] | None = None
    owner_names: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_kinds",
            _normalized_values(self.artifact_kinds, "artifact_kinds", ARTIFACT_KINDS),
        )
        object.__setattr__(
            self,
            "module_kinds",
            _normalized_values(self.module_kinds, "module_kinds", MODULE_KINDS),
        )
        object.__setattr__(
            self,
            "owner_kinds",
            _normalized_values(self.owner_kinds, "owner_kinds", OWNER_KINDS),
        )
        metadata_types = _normalized_values(
            self.metadata_types, "metadata_types", None
        )
        if metadata_types is not None:
            for value in metadata_types:
                if not _METADATA_TYPE_RE.fullmatch(value):
                    raise ValueError("metadata_types items must be ASCII identifiers")
        object.__setattr__(self, "metadata_types", metadata_types)
        object.__setattr__(
            self,
            "owner_names",
            _normalized_values(self.owner_names, "owner_names", None),
        )

    @property
    def is_empty(self) -> bool:
        """``True``, если ни одно измерение не ограничено."""
        return all(
            value is None
            for value in (
                self.artifact_kinds,
                self.module_kinds,
                self.owner_kinds,
                self.metadata_types,
                self.owner_names,
            )
        )

    def matches(self, artifact: ArtifactRef) -> bool:
        """Проходит ли артефакт все заданные измерения."""
        if not isinstance(artifact, ArtifactRef):
            raise TypeError("artifact must be ArtifactRef")
        if (
            self.artifact_kinds is not None
            and artifact.artifact_kind not in self.artifact_kinds
        ):
            return False
        module_kinds = self.module_kinds
        if module_kinds is not None and (
            artifact.module_kind is None or artifact.module_kind not in module_kinds
        ):
            return False
        owner_filtered = (
            self.owner_kinds is not None
            or self.metadata_types is not None
            or self.owner_names is not None
        )
        if not owner_filtered:
            return True
        owner = artifact.owner
        if owner is None:
            return False
        if self.owner_kinds is not None and owner.owner_kind not in self.owner_kinds:
            return False
        if self.metadata_types is not None and (
            owner.metadata_type not in self.metadata_types
        ):
            return False
        if self.owner_names is not None:
            if owner.owner_name is None:
                return False
            names = {identity_key(name) for name in self.owner_names}
            if identity_key(owner.owner_name) not in names:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            name: None if value is None else list(value)
            for name, value in (
                ("artifact_kinds", self.artifact_kinds),
                ("module_kinds", self.module_kinds),
                ("owner_kinds", self.owner_kinds),
                ("metadata_types", self.metadata_types),
                ("owner_names", self.owner_names),
            )
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchFilters:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _FILTER_KEYS, "filters")
        for name in sorted(_FILTER_KEYS):
            if data[name] is not None and not isinstance(data[name], list):
                raise ValueError(f"{name} must be a list or null")
        return cls(
            artifact_kinds=_tuple_or_none(data["artifact_kinds"]),
            module_kinds=_tuple_or_none(data["module_kinds"]),
            owner_kinds=_tuple_or_none(data["owner_kinds"]),
            metadata_types=_tuple_or_none(data["metadata_types"]),
            owner_names=_tuple_or_none(data["owner_names"]),
        )


_QUERY_KEYS = frozenset(["text", "mode", "top_k", "filters"])


@dataclass(frozen=True)
class SearchQuery:
    """Запрос пользователя.

    ``mode``: ``auto`` — точное разрешение, затем смысловой поиск;
    ``exact`` — только точное разрешение; ``semantic`` — только смысловой
    поиск. ``top_k`` ограничивает смысловую выдачу и не ограничивает
    множество кандидатов неоднозначного точного совпадения.
    """

    text: str
    mode: QueryMode = "auto"
    top_k: int = 5
    filters: SearchFilters = field(default_factory=SearchFilters)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("query text must be str")
        if not self.text.strip():
            raise ValueError("query text must not be empty or whitespace")
        if any(
            unicodedata.category(char) == "Cc" and char not in _ALLOWED_QUERY_CONTROLS
            for char in self.text
        ):
            raise ValueError("query text must not contain control characters")
        if self.mode not in QUERY_MODES:
            raise ValueError(f"unknown query mode: {self.mode!r}")
        _check_int(self.top_k, "top_k", 1)
        if not isinstance(self.filters, SearchFilters):
            raise TypeError("filters must be SearchFilters")

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            "text": self.text,
            "mode": self.mode,
            "top_k": self.top_k,
            "filters": self.filters.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchQuery:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _QUERY_KEYS, "query")
        return cls(
            text=data["text"],
            mode=data["mode"],
            top_k=data["top_k"],
            filters=SearchFilters.from_dict(data["filters"]),
        )


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


_HIT_KEYS = frozenset(
    ["artifact", "method", "matched_field", "document_id", "similarity"]
)


@dataclass(frozen=True)
class SearchHit:
    """Один найденный артефакт.

    Происхождение (``method``, ``matched_field``, ``document_id``) и оценка
    сходства (``similarity``) — разные поля. ``similarity`` есть только у
    смысловой выдачи; это конечное число в шкале конкретного поисковика,
    а не вероятность правильного ответа, и между поисковиками оно не
    сравнимо.
    """

    artifact: ArtifactRef
    method: MatchMethod
    matched_field: MatchField | None = None
    document_id: str | None = None
    similarity: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            raise TypeError("artifact must be ArtifactRef")
        if self.method not in MATCH_METHODS:
            raise ValueError(f"unknown match method: {self.method!r}")
        if self.matched_field is not None and self.matched_field not in MATCH_FIELDS:
            raise ValueError(f"unknown matched_field: {self.matched_field!r}")
        if self.method == "exact_id":
            if self.matched_field != "artifact_id":
                raise ValueError("exact_id hit requires matched_field 'artifact_id'")
        elif self.method == "exact_name":
            if self.matched_field not in ("form_name", "owner_name"):
                raise ValueError(
                    "exact_name hit requires matched_field 'owner_name' or 'form_name'"
                )
        elif self.matched_field is not None:
            raise ValueError("semantic hit must not have matched_field")
        if self.method in _EXACT_METHODS:
            if self.similarity is not None or self.document_id is not None:
                raise ValueError("exact hit must not have similarity or document_id")
            return
        if self.document_id is None or self.similarity is None:
            raise ValueError("semantic hit requires document_id and similarity")
        if isinstance(self.similarity, bool) or not isinstance(
            self.similarity, (int, float)
        ):
            raise TypeError("similarity must be a number")
        if not math.isfinite(float(self.similarity)):
            raise ValueError("similarity must be finite")
        object.__setattr__(self, "similarity", float(self.similarity))
        base = self.artifact.artifact_id
        if not isinstance(self.document_id, str) or not (
            self.document_id == base or self.document_id.startswith(base + "#")
        ):
            raise ValueError("document_id must belong to the hit artifact")

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {
            "artifact": self.artifact.to_dict(),
            "method": self.method,
            "matched_field": self.matched_field,
            "document_id": self.document_id,
            "similarity": self.similarity,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchHit:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _HIT_KEYS, "hit")
        return cls(
            artifact=ArtifactRef.from_dict(data["artifact"]),
            method=data["method"],
            matched_field=data["matched_field"],
            document_id=data["document_id"],
            similarity=data["similarity"],
        )


def _hit_sort_key(hit: SearchHit) -> tuple[float, str, str, str]:
    similarity = -hit.similarity if hit.similarity is not None else 0.0
    document = hit.document_id or ""
    return (similarity, hit.artifact.identity, hit.artifact.artifact_id, document)


_ERROR_KEYS = frozenset(["code", "message"])


@dataclass(frozen=True)
class SearchError:
    """Типизированная ошибка поиска с фиксированным текстом без деталей.

    Сообщение выбирается по коду; исходные исключения, пути и ответы
    провайдера в него не попадают.
    """

    code: SearchErrorCode

    def __post_init__(self) -> None:
        if self.code not in SEARCH_ERROR_CODES:
            raise ValueError(f"unknown search error code: {self.code!r}")

    @property
    def message(self) -> str:
        """Стабильное сообщение для кода."""
        return _ERROR_MESSAGES[self.code]

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление."""
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchError:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _ERROR_KEYS, "error")
        error = cls(code=data["code"])
        if error.message != data["message"]:
            raise ValueError("error message does not match code")
        return error


_RESULT_KEYS = frozenset(["schema", "status", "query", "hits", "error"])


@dataclass(frozen=True)
class SearchResult:
    """Результат поиска с различимым состоянием.

    * ``exact`` — ровно одно точное совпадение.
    * ``ambiguous`` — два и более точных кандидата, полный список без
      ограничения ``top_k``.
    * ``semantic`` — смысловые кандидаты, не более ``top_k``, по убыванию
      ``similarity``.
    * ``empty`` — кандидатов нет, ошибки нет.
    * ``error`` — поиск не выполнен, кандидатов нет, задан ``error``.

    Один артефакт встречается в выдаче не более одного раза; все попадания
    проходят фильтры запроса. Порядок задаётся контрактом и не зависит от
    порядка входа.
    """

    status: SearchStatus
    query: SearchQuery
    hits: tuple[SearchHit, ...] = ()
    error: SearchError | None = None

    def __post_init__(self) -> None:
        if self.status not in SEARCH_STATUSES:
            raise ValueError(f"unknown search status: {self.status!r}")
        if not isinstance(self.query, SearchQuery):
            raise TypeError("query must be SearchQuery")
        hits = tuple(self.hits)
        for hit in hits:
            if not isinstance(hit, SearchHit):
                raise TypeError("hits must contain only SearchHit")
        if self.error is not None and not isinstance(self.error, SearchError):
            raise TypeError("error must be SearchError or None")
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error is required for status 'error' only")
        if self.status in ("empty", "error"):
            if hits:
                raise ValueError(f"status {self.status!r} must not have hits")
        elif self.status == "exact":
            if len(hits) != 1 or hits[0].method not in _EXACT_METHODS:
                raise ValueError("status 'exact' requires exactly one exact hit")
        elif self.status == "ambiguous":
            if len(hits) < 2 or any(h.method not in _EXACT_METHODS for h in hits):
                raise ValueError("status 'ambiguous' requires two or more exact hits")
        else:
            if not hits or any(h.method != "semantic" for h in hits):
                raise ValueError("status 'semantic' requires semantic hits")
            if len(hits) > self.query.top_k:
                raise ValueError("semantic hits exceed query top_k")
        if self.status in ("exact", "ambiguous") and self.query.mode == "semantic":
            raise ValueError("semantic-only query cannot produce exact results")
        if self.status == "semantic" and self.query.mode == "exact":
            raise ValueError("exact-only query cannot produce semantic results")
        seen: set[str] = set()
        for hit in hits:
            if hit.artifact.identity in seen:
                raise ValueError(
                    f"duplicate artifact in hits: {hit.artifact.artifact_id!r}"
                )
            seen.add(hit.artifact.identity)
            if not self.query.filters.matches(hit.artifact):
                raise ValueError("hit does not match query filters")
        object.__setattr__(self, "hits", tuple(sorted(hits, key=_hit_sort_key)))

    @classmethod
    def exact(cls, query: SearchQuery, hit: SearchHit) -> SearchResult:
        """Однозначное точное совпадение."""
        return cls(status="exact", query=query, hits=(hit,))

    @classmethod
    def ambiguous(cls, query: SearchQuery, hits: Iterable[SearchHit]) -> SearchResult:
        """Несколько точных кандидатов."""
        return cls(status="ambiguous", query=query, hits=tuple(hits))

    @classmethod
    def semantic(cls, query: SearchQuery, hits: Iterable[SearchHit]) -> SearchResult:
        """Смысловая выдача."""
        return cls(status="semantic", query=query, hits=tuple(hits))

    @classmethod
    def empty(cls, query: SearchQuery) -> SearchResult:
        """Пустой результат без ошибки."""
        return cls(status="empty", query=query)

    @classmethod
    def failed(cls, query: SearchQuery, code: SearchErrorCode) -> SearchResult:
        """Ошибка поиска с типизированным кодом."""
        return cls(status="error", query=query, error=SearchError(code=code))

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемое представление без исходного текста документов."""
        return {
            "schema": SEARCH_CONTRACT_SCHEMA,
            "status": self.status,
            "query": self.query.to_dict(),
            "hits": [hit.to_dict() for hit in self.hits],
            "error": None if self.error is None else self.error.to_dict(),
        }

    def to_json(self) -> str:
        """Детерминированный JSON результата."""
        return to_canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchResult:
        """Восстановить из ``to_dict()``."""
        _check_keys(data, _RESULT_KEYS, "result")
        if data["schema"] != SEARCH_CONTRACT_SCHEMA:
            raise ValueError("unsupported search contract schema")
        raw_hits = data["hits"]
        if not isinstance(raw_hits, list):
            raise TypeError("hits must be a list")
        raw_error = data["error"]
        return cls(
            status=data["status"],
            query=SearchQuery.from_dict(data["query"]),
            hits=tuple(SearchHit.from_dict(item) for item in raw_hits),
            error=None if raw_error is None else SearchError.from_dict(raw_error),
        )
