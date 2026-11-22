"""Capture reversible source data from a normalized v8unpack extraction."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from v8unpack_agent.form_identity import validate_form_id

SCHEMA_VERSION = "ordinary-form/1"
_SUFFIXES = (".json", ".id.json", ".elem.json", ".obj.bsl")
_OWNER_FILES = {"cf": "Configuration.json", "epf": "ExternalDataProcessor.json"}


class OrdinaryFormDataError(ValueError):
    """The input is missing, inconsistent or outside the supported format."""


def _read_source(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise OrdinaryFormDataError(f"cannot read {path.suffix} source") from exc


def _read_json_source(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_source(path))
    except json.JSONDecodeError as exc:
        raise OrdinaryFormDataError("invalid JSON in upstream source") from exc
    if not isinstance(data, dict):
        raise OrdinaryFormDataError("expected a JSON object in upstream source")
    return data


def _validate_location(form_dir: Any, stem: Any) -> None:
    if not isinstance(form_dir, str) or not isinstance(stem, str):
        raise OrdinaryFormDataError("expected a form directory and file stem")
    try:
        validate_form_id(form_dir)
        validate_form_id(stem)
    except ValueError as exc:
        raise OrdinaryFormDataError("invalid form location") from exc
    if (
        "/" in stem or not PurePosixPath(form_dir).parts or not PurePosixPath(stem).parts
        or PurePosixPath(form_dir).as_posix() != form_dir
        or any(char in form_dir + stem for char in ':*?"<>|\0')
    ):
        raise OrdinaryFormDataError("invalid form location")


def _validate_uuid(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise OrdinaryFormDataError(f"missing {label} UUID")
    try:
        UUID(value)
    except ValueError as exc:
        raise OrdinaryFormDataError(f"invalid {label} UUID") from exc


def validate_ordinary_form_data(data: Any) -> None:
    """Validate the supported 0-27 subset before writing any build inputs."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise OrdinaryFormDataError("unsupported ordinary form data schema")
    if data.keys() != {
        "schema_version", "owner_kind", "owner_uuid", "form_dir", "stem",
        "metadata", "identity", "elements", "module",
    }:
        raise OrdinaryFormDataError("incomplete or unexpected form data fields")
    owner_kind = data["owner_kind"]
    if owner_kind not in ("cf", "epf"):
        raise OrdinaryFormDataError("expected a configuration or external processor")
    _validate_uuid(data["owner_uuid"], "container owner")
    _validate_location(data["form_dir"], data["stem"])
    metadata = data["metadata"]
    if not isinstance(metadata, dict) or (
        metadata.get("Тип формы") != "0" or metadata.get("Версия элементов формы") != "0-27"
    ):
        raise OrdinaryFormDataError("expected an ordinary form with element version 0-27")
    if (
        not isinstance(metadata.get("header"), list) or not metadata["header"]
        or not isinstance(metadata.get("form"), list) or not metadata["form"]
        or not isinstance(metadata["form"][0], list) or not metadata["form"][0]
        or type(metadata.get("code_info_obj")) is not int or metadata["code_info_obj"] != 1
    ):
        raise OrdinaryFormDataError("missing ordinary form header, layout or module metadata")
    identity = data["identity"]
    if not isinstance(identity, dict):
        raise OrdinaryFormDataError("missing form identity")
    _validate_uuid(identity.get("uuid"), "form")
    if not isinstance(data["module"], str):
        raise OrdinaryFormDataError("missing form module, expected text (possibly empty)")
    elements = data["elements"]
    if not isinstance(elements, dict) or not isinstance(elements.get("data"), dict):
        raise OrdinaryFormDataError("missing raw element data")
    for key in ("props", "params", "commands", "tree"):
        if not isinstance(elements.get(key), list):
            raise OrdinaryFormDataError(f"missing element section: {key}")
    if not elements["tree"] or not elements["data"]:
        raise OrdinaryFormDataError("empty or unsupported element tree")
    pending = [(node, "") for node in elements["tree"]]
    seen: set[str] = set()
    while pending:
        node, parent = pending.pop()
        if not isinstance(node, dict) or not isinstance(node.get("name"), str):
            raise OrdinaryFormDataError("invalid element tree node")
        if node["name"].startswith(("include_", "includr_")):
            raise OrdinaryFormDataError("split form areas are unsupported")
        page = node.get("page")
        if page is not None and not isinstance(page, str):
            raise OrdinaryFormDataError("invalid element page")
        key = "/".join(part for part in (parent, page, node["name"]) if part)
        raw_record = elements["data"].get(key)
        if (
            key in seen or not isinstance(raw_record, dict)
            or not isinstance(raw_record.get("raw"), list) or not raw_record["raw"]
        ):
            raise OrdinaryFormDataError("missing or ambiguous raw element record")
        seen.add(key)
        children = node.get("child", [])
        if not isinstance(children, list):
            raise OrdinaryFormDataError("split or invalid element children")
        pending.extend((child, key) for child in children)


def capture_ordinary_form(extraction_root: Path, form_dir: str) -> dict[str, Any]:
    """Read one supported form from a CF/EPF tree produced by v8unpack.extract.

    Preserve the parsed header and every element and raw block. The module is
    read as UTF-8 with optional BOM, retaining its original line endings.
    """
    root = Path(extraction_root).resolve()
    _validate_location(form_dir, "Form")
    folder = root.joinpath(*PurePosixPath(form_dir).parts)
    if not folder.is_dir() or not folder.resolve().is_relative_to(root):
        raise OrdinaryFormDataError("form directory is absent from the extraction")
    owners = [(kind, root / name) for kind, name in _OWNER_FILES.items() if (root / name).is_file()]
    if len(owners) != 1:
        raise OrdinaryFormDataError("expected one CF or EPF owner metadata file at the extraction root")
    owner_kind, owner_path = owners[0]
    element_files = list(folder.glob("*.elem.json"))
    if len(element_files) != 1:
        raise OrdinaryFormDataError("expected one raw .elem.json file in the form directory")
    stem = element_files[0].name.removesuffix(".elem.json")
    _validate_location(form_dir, stem)
    metadata, identity, elements, module = (folder / (stem + suffix) for suffix in _SUFFIXES)
    result = {
        "schema_version": SCHEMA_VERSION,
        "owner_kind": owner_kind,
        "owner_uuid": _read_json_source(owner_path).get("uuid"),
        "form_dir": form_dir,
        "stem": stem,
        "metadata": _read_json_source(metadata),
        "identity": _read_json_source(identity),
        "elements": _read_json_source(elements),
        "module": _read_source(module),
    }
    validate_ordinary_form_data(result)
    return result
