import pytest

from v8unpack_agent import BinaryExtractor, ExtractionResult


def test_failure_requires_notes():
    with pytest.raises(ValueError):
        ExtractionResult(extraction_ok=False)


def test_failure_with_notes_is_allowed():
    r = ExtractionResult(extraction_ok=False, notes=("boom",))
    assert r.extraction_ok is False
    assert r.notes == ("boom",)


def test_success_needs_no_notes():
    r = ExtractionResult(extraction_ok=True)
    assert r.extraction_ok is True
    assert r.notes == ()


def test_result_is_frozen():
    r = ExtractionResult(extraction_ok=True)
    with pytest.raises(Exception):
        r.extraction_ok = False  # type: ignore[misc]


def test_protocol_runtime_checkable():
    class Good:
        def extract(self, source, shadow_root):  # noqa: ANN001
            return ExtractionResult(extraction_ok=True)

    class Bad:
        pass

    assert isinstance(Good(), BinaryExtractor)
    assert not isinstance(Bad(), BinaryExtractor)
