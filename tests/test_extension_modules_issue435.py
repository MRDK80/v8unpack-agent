"""Модули расширения конфигурации (issue #435).

Синтетическое расширение повторяет layout распакованного ``.cfe``
(docs/research/bsl_remainder_issue432.md): корень ``ConfigurationExtension.json``,
объекты в normalized-layout, модули уровня расширения
``ConfigurationExtension.app.bsl`` и ``.ssn.bsl``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from v8unpack_agent.extension_modules import (
    EXTENSION_MODULE_FILES,
    apply_extension,
    extension_name,
    scan_extension_modules,
)
from v8unpack_agent.modules import ModuleEntry, ModuleIndex
from v8unpack_agent.runner import RunOptions, run_pipeline
from v8unpack_agent.search_api import collect_search_corpus
from v8unpack_agent.search_contract import ArtifactRef, OwnerRef

EXT = "Расширение1"
BSL = "Процедура Тест()\nКонецПроцедуры\n".encode()
PREFIX = f"extension:{EXT}/"


def _write(root: Path, relative: str, payload: bytes) -> None:
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _metadata(name: object = EXT) -> bytes:
    return json.dumps({"name": name, "v8unpack": "1.2.13"}, ensure_ascii=False).encode()


def _build_extension(root: Path) -> None:
    _write(root, "ConfigurationExtension.json", _metadata())
    _write(root, "ConfigurationExtension.app.bsl", BSL)
    _write(root, "ConfigurationExtension.ssn.bsl", BSL)
    # собственный справочник: модуль объекта, менеджера и форма
    _write(root, "Catalog/Свой/Catalog.obj.bsl", BSL)
    _write(root, "Catalog/Свой/Catalog.mgr.bsl", BSL)
    _write(root, "Catalog/Свой/CatalogForm/Форма/CatalogForm.json", b"{}")
    _write(root, "Catalog/Свой/CatalogForm/Форма/CatalogForm.obj.bsl", BSL)
    # заимствованный справочник без модулей
    _write(root, "Catalog/Чужой/Catalog.json", b"{}")
    # заимствованный документ с модулем объекта
    _write(root, "Document/Покупка/Document.obj.bsl", BSL)
    _write(root, "CommonModule/Общий/CommonModule.obj.bsl", BSL)


def _build_configuration(root: Path) -> None:
    _write(root, "Configuration.json", b"{}")
    _write(root, "Configuration.app.bsl", BSL)
    _write(root, "Catalog/Чужой/Catalog.obj.bsl", BSL)
    _write(root, "Document/Покупка/Document.obj.bsl", BSL)
    _write(root, "Document/Покупка/DocumentForm/Форма/DocumentForm.json", b"{}")
    _write(root, "Document/Покупка/DocumentForm/Форма/DocumentForm.obj.bsl", BSL)
    _write(root, "CommonModule/Общий/CommonModule.obj.bsl", BSL)


def _run(root: Path, **kwargs: Any) -> Any:
    return run_pipeline(
        RunOptions(export_root=root, include_skd=False, include_module_index=True, **kwargs)
    )


def _modules(outcome: Any) -> dict[str, tuple[str, str, str | None]]:
    return {
        item.object: (item.object_kind.value, item.status.value, item.reason_code)
        for item in outcome.report.objects
        if item.object_kind.value.startswith("module_")
    }


# --- распознавание расширения ----------------------------------------------


def test_extension_name(tmp_path: Path) -> None:
    assert extension_name(tmp_path) is None
    _write(tmp_path, "ConfigurationExtension.json", _metadata())
    assert extension_name(tmp_path) == EXT


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b"[]", b"{", _metadata(None), _metadata("a b"), _metadata(1)],
)
def test_invalid_extension_metadata_is_error(tmp_path: Path, payload: bytes) -> None:
    _write(tmp_path, "ConfigurationExtension.json", payload)
    with pytest.raises(ValueError):
        extension_name(tmp_path)


def test_configuration_entries_are_unchanged(tmp_path: Path) -> None:
    entry = ModuleEntry("object", "metadata_object", "Catalog", "А", "Catalog/А/Catalog.obj.bsl", "missing")
    assert apply_extension(tmp_path, [entry]) == [entry]
    assert entry.module_id == "metadata_object:Catalog:А:object"
    assert "extension" not in entry.to_dict()


# --- модули уровня расширения ----------------------------------------------


@pytest.mark.parametrize(
    ("payload", "status"),
    [(BSL, "ok"), (b"", "empty"), (b"\xef\xbb\xbf \n", "whitespace_only"), (b"\xff\xfe", "read_error")],
)
def test_extension_level_statuses(tmp_path: Path, payload: bytes, status: str) -> None:
    for name in EXTENSION_MODULE_FILES.values():
        _write(tmp_path, name, payload)
    index = scan_extension_modules(tmp_path, EXT)
    assert {e.module_kind: e.read_status for e in index} == {
        "managed_application": status,
        "session": status,
    }
    for entry in index:
        assert entry.owner_kind == "extension"
        assert entry.metadata_type == "ConfigurationExtension"
        assert entry.owner_name == EXT
        assert entry.module_id == f"{PREFIX}extension:ConfigurationExtension:{EXT}:{entry.module_kind}"


def test_absent_extension_level_module_gives_no_entry(tmp_path: Path) -> None:
    _write(tmp_path, "ConfigurationExtension.ssn.bsl", BSL)
    index = scan_extension_modules(tmp_path, EXT)
    assert [e.module_kind for e in index] == ["session"]


def test_symlink_extension_module_is_read_error(tmp_path: Path) -> None:
    target = tmp_path / "target.bsl"
    target.write_bytes(BSL)
    try:
        os.symlink(target, tmp_path / "ConfigurationExtension.app.bsl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен")
    index = scan_extension_modules(tmp_path, EXT)
    assert [(e.module_kind, e.read_status) for e in index] == [("managed_application", "read_error")]


# --- runner -----------------------------------------------------------------


def test_runner_on_extension(tmp_path: Path) -> None:
    _build_extension(tmp_path)
    outcome = _run(tmp_path)
    assert outcome.completed
    assert _modules(outcome) == {
        "ConfigurationExtension.app.bsl": ("module_managed_application", "complete", None),
        "ConfigurationExtension.ssn.bsl": ("module_session", "complete", None),
        "Catalog/Свой/Catalog.obj.bsl": ("module_object", "complete", None),
        "Catalog/Свой/Catalog.mgr.bsl": ("module_manager", "complete", None),
        "Document/Покупка/Document.obj.bsl": ("module_object", "complete", None),
    }


def test_no_missing_in_extension(tmp_path: Path) -> None:
    _build_extension(tmp_path)
    reasons = {reason for _kind, _status, reason in _modules(_run(tmp_path)).values()}
    assert "missing" not in reasons


def test_configuration_still_reports_missing(tmp_path: Path) -> None:
    _write(tmp_path, "Catalog/Чужой/Catalog.json", b"{}")
    reasons = {reason for _kind, _status, reason in _modules(_run(tmp_path)).values()}
    assert reasons == {"missing"}


def test_skip_configuration_group_skips_extension_level(tmp_path: Path) -> None:
    _build_extension(tmp_path)
    outcome = _run(tmp_path, module_groups=("metadata",))
    assert set(_modules(outcome)) == {
        "Catalog/Свой/Catalog.obj.bsl",
        "Catalog/Свой/Catalog.mgr.bsl",
        "Document/Покупка/Document.obj.bsl",
    }


def test_invalid_extension_metadata_fails_modules_stage(tmp_path: Path) -> None:
    _build_extension(tmp_path)
    _write(tmp_path, "ConfigurationExtension.json", b"{")
    outcome = _run(tmp_path)
    assert not outcome.completed


# --- идентификаторы и поиск ------------------------------------------------


def _artifacts(root: Path) -> dict[str, str]:
    corpus = collect_search_corpus(root)
    return {
        item.artifact_id: item.source_path or ""
        for item in corpus.report.items
        if item.artifact_id is not None
    }


def test_extension_artifact_ids_are_prefixed(tmp_path: Path) -> None:
    _build_extension(tmp_path)
    ids = _artifacts(tmp_path)
    assert ids
    assert all(PREFIX in artifact_id for artifact_id in ids)
    assert f"{PREFIX}metadata_object:Document:Покупка:object" in ids
    assert f"{PREFIX}common_module:CommonModule:Общий:common_module" in ids
    assert any(i.startswith(f"form:{PREFIX}") for i in ids)
    corpus = collect_search_corpus(tmp_path)
    for link in corpus.owner_links:
        assert link.owner.extension == EXT
        assert link.owner.owner_id.startswith(f"owner:{PREFIX}")


def test_extension_ids_do_not_collide_with_configuration(tmp_path: Path) -> None:
    _build_extension(tmp_path / "ext")
    _build_configuration(tmp_path / "cfg")
    ext = set(_artifacts(tmp_path / "ext"))
    cfg = set(_artifacts(tmp_path / "cfg"))
    assert "metadata_object:Document:Покупка:object" in cfg
    assert not ext & cfg
    assert all(PREFIX not in artifact_id for artifact_id in cfg)


def test_serialization_round_trip() -> None:
    entry = ModuleEntry(
        "object", "metadata_object", "Document", "Покупка",
        "Document/Покупка/Document.obj.bsl", "missing", extension=EXT,
    )
    data = entry.to_dict()
    assert data["extension"] == EXT
    assert ModuleEntry.from_dict(data) == entry
    index = ModuleIndex.from_entries([entry])
    assert ModuleIndex.from_dict(index.to_dict()) == index
    artifact = ArtifactRef.from_module_entry(entry)
    assert artifact.artifact_id == entry.module_id
    assert ArtifactRef.from_dict(artifact.to_dict()) == artifact
    owner = OwnerRef.from_module_entry(entry)
    assert OwnerRef.from_dict(owner.to_dict()) == owner


def test_mismatched_extension_is_rejected() -> None:
    owner = OwnerRef("metadata_object", "Catalog", "А", extension=EXT)
    with pytest.raises(ValueError):
        ArtifactRef(artifact_kind="module", owner=owner, module_kind="object")
    with pytest.raises(ValueError):
        ModuleEntry("object", "metadata_object", "Catalog", "А", "Catalog/А/Catalog.obj.bsl", "ok", extension="a b")
