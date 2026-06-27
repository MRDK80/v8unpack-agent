from pathlib import Path

import pytest

from v8unpack_agent import FormArtifact


def test_ok_artifact_needs_no_warnings():
    art = FormArtifact.for_form(Path("/dump"), "ФормаЭлемента")
    assert art.extraction_ok is True
    assert art.extraction_warnings == []
    assert art.paths["object_module"].name == "Form.obj.bsl"


def test_partial_without_warnings_is_rejected():
    with pytest.raises(ValueError):
        FormArtifact.for_form(Path("/dump"), "ФормаСписка", extraction_ok=False)


def test_partial_with_warnings_is_allowed():
    art = FormArtifact.for_form(
        Path("/dump"),
        "ФормаСписка",
        extraction_ok=False,
        extraction_warnings=["вложенная панель не распакована"],
    )
    assert art.extraction_ok is False
    assert art.extraction_warnings == ["вложенная панель не распакована"]


def test_artifact_is_frozen():
    art = FormArtifact.for_form(Path("/dump"), "ФормаЭлемента")
    with pytest.raises(Exception):
        art.extraction_ok = False  # type: ignore[misc]


def test_fields_match_article():
    art = FormArtifact.for_form(Path("/dump"), "Ф")
    # точные имена полей из статьи
    assert set(art.__dataclass_fields__) == {
        "name",
        "paths",
        "extraction_ok",
        "extraction_warnings",
        "skd_extracted",
        "elem_index_ok",
    }
