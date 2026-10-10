"""Адаптеры форм и модулей к общему поисковому корпусу (issue #323).

Все данные синтетические, реальная выгрузка не используется. Абсолютных
путей и литеральных разделителей Windows в тестах нет: обратная косая черта
собирается через ``chr(92)``. Файлы fixture пишутся через ``write_bytes``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.command_modules import scan_command_modules
from v8unpack_agent.common_modules import scan_common_modules
from v8unpack_agent.configuration_modules import (
    CONFIGURATION_MODULE_FILES,
    scan_configuration_modules,
)
from v8unpack_agent.external_object_modules import scan_external_object_modules
from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)
from v8unpack_agent.modules import MODULE_KINDS, ModuleEntry, ModuleIndex
from v8unpack_agent.record_set_modules import (
    RECORD_SET_MODULE_FILES,
    scan_record_set_modules,
)
from v8unpack_agent.scan_forms import FormEntry
from v8unpack_agent.search_contract import SearchDocumentSet
from v8unpack_agent.search_corpus import (
    SEARCH_CORPUS_SCHEMA,
    CorpusItem,
    FormReadFailure,
    SearchCorpus,
    build_form_inputs,
    build_search_corpus,
    read_module_text,
)
from v8unpack_agent.service_modules import scan_service_modules
from v8unpack_agent.value_manager_modules import scan_value_manager_modules

REPO_ROOT = Path(__file__).resolve().parents[1]
BS = chr(92)
BSL = "Процедура Тест()\nКонецПроцедуры\n".encode()
OWNER = "Alpha"
COMMAND_TYPES = ("Catalog", "DataProcessor", "Document", "InformationRegister", "Report")
SERVICE_TYPES = ("HTTPService", "WebService")
FORM_DIR = "Catalog/Alpha/CatalogForm/Main"
COMMON_FORM_DIR = "CommonForm/Shared"
COMMON_MODULE = "CommonModule/Shared/CommonModule.obj.bsl"
EXTERNAL_DIR = "loader.epf"


def _write(root: Path, relative: str, payload: bytes) -> None:
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _supported() -> dict[str, str]:
    paths: dict[str, str] = {}
    for kind, name in CONFIGURATION_MODULE_FILES.items():
        paths[name] = kind
    for metadata_type, layout in METADATA_OBJECT_MODULE_LAYOUTS.items():
        for kind, name in layout.items():
            paths[f"{metadata_type}/{OWNER}/{name}"] = kind
    for metadata_type, name in RECORD_SET_MODULE_FILES.items():
        paths[f"{metadata_type}/{OWNER}/{name}"] = "record_set"
    paths[f"Constant/{OWNER}/Constant.obj.bsl"] = "value_manager"
    for metadata_type in SERVICE_TYPES:
        paths[f"{metadata_type}/{OWNER}/{metadata_type}.obj.bsl"] = "service"
    paths[f"CommonCommand/{OWNER}/CommonCommand.obj.bsl"] = "command"
    for metadata_type in COMMAND_TYPES:
        container = f"{metadata_type}Command"
        paths[f"{metadata_type}/{OWNER}/{container}/Run/{container}.obj.bsl"] = "command"
    return paths


def _build_export(root: Path) -> None:
    for relative in _supported():
        _write(root, relative, BSL)
    _write(root, "Catalog/Beta/Catalog.obj.bsl", b"")
    _write(root, "InformationRegister/Beta/InformationRegister.obj.bsl", b"  \n")
    _write(root, "Document/Beta/Document.obj.bsl", b"\xff\xfe\xfa")
    root.joinpath("Constant", "Beta").mkdir(parents=True, exist_ok=True)
    _write(root, f"{FORM_DIR}/CatalogForm.obj.bsl", BSL)
    _write(root, f"{COMMON_FORM_DIR}/CommonForm.obj.bsl", BSL)
    _write(root, COMMON_MODULE, BSL)


def _module_index(root: Path) -> ModuleIndex:
    entries: list[ModuleEntry] = []
    for scanner in (
        scan_configuration_modules,
        scan_metadata_object_modules,
        scan_record_set_modules,
        scan_command_modules,
        scan_value_manager_modules,
        scan_service_modules,
    ):
        entries.extend(scanner(root))
    return ModuleIndex.from_entries(entries)


def _form_entry(
    root: Path,
    form_dir: str,
    object_type: str,
    object_name: str,
    container: str,
    *,
    bsl_name: str | None = None,
) -> FormEntry:
    directory = root.joinpath(*form_dir.split("/"))
    directory.mkdir(parents=True, exist_ok=True)
    name = bsl_name or f"{container}.obj.bsl"
    return FormEntry(
        object_type=object_type,
        object_name=object_name,
        container_name=container,
        form_name=form_dir.split("/")[-1],
        form_path=directory.resolve(),
        bsl_path=(directory / name).resolve(),
        json_path=(directory / f"{container}.json").resolve(),
    )


def _forms(root: Path) -> list[FormEntry]:
    return [
        _form_entry(root, FORM_DIR, "Catalog", OWNER, "CatalogForm"),
        _form_entry(root, COMMON_FORM_DIR, "CommonForm", "", "CommonForm"),
    ]


def _corpus(root: Path, *, reverse: bool = False) -> SearchCorpus:
    index = _module_index(root)
    contexts = list(build_form_inputs(_forms(root), root))
    if reverse:
        contexts.reverse()
    return build_search_corpus(
        root,
        module_index=index,
        common_modules=scan_common_modules(root),
        forms=contexts,
    )


def _items(corpus: SearchCorpus) -> dict[str | None, CorpusItem]:
    return {item.artifact_id: item for item in corpus.report.items}


def _doc_ids(corpus: SearchCorpus) -> set[str]:
    return {document.document_id for document in corpus.documents.documents}


# ---------------------------------------------------------------------------
# Смешанный корпус и все виды модулей
# ---------------------------------------------------------------------------


def test_mixed_corpus_is_reproducible(tmp_path: Path) -> None:
    _build_export(tmp_path)
    first = _corpus(tmp_path)
    second = _corpus(tmp_path, reverse=True)
    assert first.to_json() == second.to_json()
    assert first.documents.to_json(include_text=True) == second.documents.to_json(
        include_text=True
    )
    data = json.loads(first.to_json())
    assert data["schema"] == SEARCH_CORPUS_SCHEMA
    assert data["report"]["schema"] == SEARCH_CORPUS_SCHEMA
    restored = SearchDocumentSet.from_dict(
        json.loads(first.documents.to_json(include_text=True))
    )
    assert restored == first.documents


def test_every_accepted_module_kind_goes_through_contract(tmp_path: Path) -> None:
    _build_export(tmp_path)
    corpus = _corpus(tmp_path)
    kinds = {
        document.artifact.module_kind
        for document in corpus.documents.documents
        if document.artifact.artifact_kind == "module"
    }
    assert kinds == set(MODULE_KINDS) - {"form"}
    owner_kinds = {
        document.artifact.owner.owner_kind
        for document in corpus.documents.documents
        if document.artifact.owner is not None
        and document.artifact.artifact_kind == "module"
    }
    assert {"common_command", "metadata_object_command", "common_module"} <= owner_kinds
    for entry in _module_index(tmp_path):
        if entry.read_status == "ok":
            assert entry.module_id in _doc_ids(corpus)
    modules = [i for i in corpus.report.items if i.artifact_kind == "module" and i.included]
    assert all(item.owner_basis == "module_entry" for item in modules)


def test_external_object_module_and_form(tmp_path: Path) -> None:
    _write(
        tmp_path,
        f"{EXTERNAL_DIR}/ExternalDataProcessor.json",
        json.dumps({"name": "Loader"}).encode(),
    )
    _write(tmp_path, f"{EXTERNAL_DIR}/ExternalDataProcessor.obj.bsl", BSL)
    form_dir = f"{EXTERNAL_DIR}/Form/Main"
    _write(tmp_path, f"{form_dir}/Form.obj.bsl", BSL)
    index = scan_external_object_modules(tmp_path)
    assert len(index) == 1
    entry = _form_entry(
        tmp_path,
        form_dir,
        "ExternalDataProcessor",
        EXTERNAL_DIR,
        "Form",
        bsl_name="Form.obj.bsl",
    )
    corpus = build_search_corpus(
        tmp_path,
        module_index=index,
        forms=build_form_inputs([entry], tmp_path),
    )
    module_id = next(iter(index)).module_id
    assert module_id in _doc_ids(corpus)
    form_item = next(i for i in corpus.report.items if i.artifact_kind == "form")
    assert form_item.included
    assert form_item.owner_basis == "unconfirmed"
    assert form_item.owner_id == "owner:external_data_processor:ExternalDataProcessor:Loader"
    form_doc = next(
        d for d in corpus.documents.documents if d.artifact.artifact_kind == "form"
    )
    assert form_doc.artifact.owner is None
    card = next(
        d for d in corpus.documents.documents if d.artifact.artifact_kind == "owner"
    )
    assert form_item.artifact_id is not None
    assert form_item.artifact_id not in card.text


# ---------------------------------------------------------------------------
# Дедупликация
# ---------------------------------------------------------------------------


def test_form_module_is_not_second_instance(tmp_path: Path) -> None:
    _build_export(tmp_path)
    form_module = ModuleEntry(
        module_kind="form",
        owner_kind="metadata_object_form",
        metadata_type="Catalog",
        owner_name=f"{OWNER}.Main",
        relative_path=f"{FORM_DIR}/CatalogForm.obj.bsl",
        read_status="ok",
        size_bytes=len(BSL),
        sha256=hashlib.sha256(BSL).hexdigest(),
    )
    index = ModuleIndex.from_entries([*_module_index(tmp_path), form_module])
    corpus = build_search_corpus(
        tmp_path,
        module_index=index,
        forms=build_form_inputs(_forms(tmp_path), tmp_path),
    )
    item = _items(corpus)[form_module.module_id]
    assert not item.included
    assert item.reason == "form_module_via_form_context"
    assert form_module.module_id not in _doc_ids(corpus)
    form_texts = [
        d for d in corpus.documents.documents if d.artifact.source_path == FORM_DIR
    ]
    assert len(form_texts) == 1


def test_common_module_counted_once(tmp_path: Path) -> None:
    _build_export(tmp_path)
    common = next(iter(scan_common_modules(tmp_path).modules))
    duplicate = ModuleEntry(
        module_kind="common_module",
        owner_kind="common_module",
        metadata_type="CommonModule",
        owner_name=common.name,
        relative_path=COMMON_MODULE,
        read_status="ok",
    )
    index = ModuleIndex.from_entries([*_module_index(tmp_path), duplicate])
    corpus = build_search_corpus(
        tmp_path, module_index=index, common_modules=scan_common_modules(tmp_path)
    )
    rows = [i for i in corpus.report.items if i.artifact_id == duplicate.module_id]
    assert sorted((r.source, r.included, r.reason) for r in rows) == [
        ("common_modules", False, "duplicate_artifact"),
        ("module_index", True, None),
    ]
    docs = [d for d in corpus.documents.documents if d.document_id == duplicate.module_id]
    assert len(docs) == 1


# ---------------------------------------------------------------------------
# Статусы без текста и ошибки
# ---------------------------------------------------------------------------


def test_statuses_without_text_are_visible(tmp_path: Path) -> None:
    _build_export(tmp_path)
    corpus = _corpus(tmp_path)
    items = _items(corpus)
    expected = {
        "metadata_object:Catalog:Beta:object": "empty",
        "metadata_object:InformationRegister:Beta:record_set": "whitespace_only",
        "metadata_object:Document:Beta:object": "read_error",
        "metadata_object:Constant:Beta:value_manager": "missing",
    }
    for artifact_id, reason in expected.items():
        assert items[artifact_id].reason == reason
        assert not items[artifact_id].included
        assert artifact_id not in _doc_ids(corpus)
    errors = {item.artifact_id for item in corpus.report.read_errors}
    assert "metadata_object:Document:Beta:object" in errors
    assert all(d.text.strip() for d in corpus.documents.documents)


def _catalog_entry(name: str, kind: str, relative: str, **extra: Any) -> ModuleEntry:
    return ModuleEntry(
        module_kind=kind,
        owner_kind="metadata_object",
        metadata_type="Catalog",
        owner_name=name,
        relative_path=relative,
        read_status="ok",
        **extra,
    )


def test_changed_and_unreadable_files(tmp_path: Path) -> None:
    _write(tmp_path, "Catalog/Alpha/Catalog.obj.bsl", BSL)
    changed = _catalog_entry(
        OWNER, "object", "Catalog/Alpha/Catalog.obj.bsl", sha256="0" * 64
    )
    assert read_module_text(tmp_path, changed).reason == "content_changed"
    gone = _catalog_entry(OWNER, "manager", "Catalog/Alpha/Catalog.mgr.bsl")
    assert read_module_text(tmp_path, gone).reason == "missing"
    _write(tmp_path, "Catalog/Gamma/Catalog.obj.bsl", b"\xff\xfe")
    broken = _catalog_entry("Gamma", "object", "Catalog/Gamma/Catalog.obj.bsl")
    assert read_module_text(tmp_path, broken).reason == "read_error"


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bsl"
    outside.write_bytes(BSL)
    root = tmp_path / "export"
    link = root / "Catalog" / OWNER / "Catalog.obj.bsl"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is not available")
    entry = _catalog_entry(OWNER, "object", "Catalog/Alpha/Catalog.obj.bsl")
    read = read_module_text(root, entry)
    assert read.text is None
    assert read.reason == "unsafe_path"


def test_text_is_normalized(tmp_path: Path) -> None:
    payload = b"\xef\xbb\xbf" + "Процедура А()\r\nКонецПроцедуры\r".encode()
    _write(tmp_path, "Catalog/Alpha/Catalog.obj.bsl", payload)
    entry = _catalog_entry(
        OWNER,
        "object",
        "Catalog/Alpha/Catalog.obj.bsl",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert read_module_text(tmp_path, entry).text == "Процедура А()\nКонецПроцедуры\n"


def test_form_statuses_and_read_failure(tmp_path: Path) -> None:
    def entry(name: str) -> FormEntry:
        return _form_entry(
            tmp_path, f"Catalog/Alpha/CatalogForm/{name}", "Catalog", OWNER, "CatalogForm"
        )

    empty, blank, absent, broken = (entry(n) for n in ("Empty", "Blank", "Absent", "Broken"))
    Path(empty.bsl_path).write_bytes(b"")
    Path(blank.bsl_path).write_bytes(b" \n")
    Path(broken.bsl_path).write_bytes(b"\xff\xfe")
    inputs = build_form_inputs([empty, blank, absent, broken], tmp_path)
    assert isinstance(inputs[3], FormReadFailure)
    corpus = build_search_corpus(tmp_path, forms=inputs)
    reasons = {
        item.artifact_id.rsplit("/", 1)[-1]: item.reason
        for item in corpus.report.items
        if item.artifact_id is not None and item.artifact_kind == "form"
    }
    assert reasons == {
        "Empty": "empty",
        "Blank": "whitespace_only",
        "Absent": "missing",
        "Broken": "read_error",
    }
    assert not [d for d in corpus.documents.documents if d.artifact.artifact_kind == "form"]


# ---------------------------------------------------------------------------
# Владельцы и карточки
# ---------------------------------------------------------------------------


def test_owner_links_and_cards(tmp_path: Path) -> None:
    _build_export(tmp_path)
    corpus = _corpus(tmp_path)
    form_id = f"form:{FORM_DIR}"
    common_form_id = "form:CommonForm//CommonForm/Shared"
    links = {link.artifact.artifact_id: link for link in corpus.owner_links}
    assert links[form_id].basis == "form_key"
    assert links[form_id].owner.owner_id == "owner:metadata_object:Catalog:Alpha"
    assert links[common_form_id].basis == "unconfirmed"
    assert links[common_form_id].owner.owner_id == "owner:common_form:CommonForm:Shared"
    documents = {d.document_id: d for d in corpus.documents.documents}
    assert documents[form_id].artifact.owner is not None
    assert documents[common_form_id].artifact.owner is None
    card = documents["owner:metadata_object:Catalog:Alpha"]
    assert f"form {form_id} basis=form_key" in card.text
    assert "module metadata_object:Catalog:Alpha:object basis=module_entry" in card.text
    assert "owner:common_form:CommonForm:Shared" not in documents
    for document in corpus.documents.documents:
        if document.artifact.artifact_kind == "owner":
            assert "unconfirmed" not in document.text
            assert "synonym" not in document.text


def test_output_has_no_absolute_paths(tmp_path: Path) -> None:
    _build_export(tmp_path)
    corpus = _corpus(tmp_path)
    payload = corpus.to_json() + corpus.documents.to_json(include_text=True)
    for marker in {str(tmp_path), tmp_path.as_posix(), str(tmp_path.resolve())}:
        assert marker not in payload
    assert BS not in corpus.to_json()
    for item in corpus.report.items:
        if item.source_path is not None:
            assert not item.source_path.startswith("/")


# ---------------------------------------------------------------------------
# Совместимость форм
# ---------------------------------------------------------------------------


def test_form_context_unchanged_by_adapter(tmp_path: Path) -> None:
    _build_export(tmp_path)
    entry = _forms(tmp_path)[0]
    before = build_form_context(entry, tmp_path)
    fragment = to_llm_prompt_fragment(before)
    corpus = build_search_corpus(tmp_path, forms=[before])
    after = build_form_context(entry, tmp_path)
    assert before == after
    assert to_llm_prompt_fragment(before) == fragment
    document = next(d for d in corpus.documents.documents if d.document_id == f"form:{FORM_DIR}")
    assert document.text == BSL.decode()


def test_root_package_does_not_export_corpus() -> None:
    import v8unpack_agent

    assert "build_search_corpus" not in v8unpack_agent.__all__


def test_report_item_rules() -> None:
    with pytest.raises(ValueError, match="reason"):
        CorpusItem(
            artifact_id="x",
            artifact_kind="module",
            source="forms",
            source_path=None,
            included=False,
        )
    with pytest.raises(ValueError, match="source_path"):
        CorpusItem(
            artifact_id="x",
            artifact_kind="module",
            source="forms",
            source_path="/abs",
            included=True,
        )


def test_docs_and_changelog_reference_issue() -> None:
    doc = (REPO_ROOT / "docs" / "search_corpus.md").read_text(encoding="utf-8")
    assert "build_search_corpus" in doc
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "(#323)" in changelog
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/search_corpus.md" in readme
