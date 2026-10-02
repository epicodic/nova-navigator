"""Second property family (ACT6 design 12): `SearchJob` over a real `LazyDocument` after random splices, against the brute-force reference."""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.search import SearchJob, SearchResult, SearchSettings, SearchSpec
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.core.search_reference import boundaries, reference_search
from tests.nova_editor.core.test_search_properties import LARGE_CHUNK, MAX_CHUNK, documents, needles
from tests.nova_editor.document.helpers_save import SMALL, Session
from tests.nova_editor.document.reference_text import decode

EXAMPLES = 400
MAX_SLICE = 8
JOIN_SECONDS = 10.0
DOCUMENT_SETTINGS = settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
OPERATIONS = st.lists(st.sampled_from(["insert", "delete", "replace", "undo", "redo"]), max_size=6)
SEARCH_CHUNKS = [*range(1, MAX_CHUNK + 1), LARGE_CHUNK]


def _location(session: Session, draw: st.DataObject) -> tuple[int, int]:
    rows = session.ref.rows
    row = draw.draw(st.integers(0, len(rows) - 1))
    return row, draw.draw(st.integers(0, len(rows[row])))


def _apply(session: Session, draw: st.DataObject, operation: str) -> None:
    if operation == "undo":
        session.undo()
        return
    if operation == "redo":
        session.redo()
        return
    first, second = sorted((_location(session, draw), _location(session, draw)))
    text = decode(draw.draw(documents)) if operation != "delete" else ""
    if operation == "insert":
        second = first
    session.edit(first, second, text)


def _build(draw: st.DataObject, original: bytes, operations: list[str]) -> tuple[LazyDocument, bytes]:
    doc = LazyDocument.from_bytes(original, SMALL)
    assert doc.wait_indexed(JOIN_SECONDS)
    session = Session(doc, original, Path())
    for operation in operations:
        _apply(session, draw, operation)
    return doc, session.ref.data()


def _needle(draw: st.DataObject, model: bytes) -> str:
    """A drawn needle, or a substring of the edited text (up to `MAX_SLICE` characters) so that long needles do match."""
    text = decode(model)
    if text and draw.draw(st.booleans()):
        start = draw.draw(st.integers(0, len(text) - 1))
        return text[start : start + draw.draw(st.integers(1, MAX_SLICE))]
    return draw.draw(needles)


@DOCUMENT_SETTINGS
@given(st.data(), documents, OPERATIONS, st.booleans(), st.booleans(), st.booleans(), st.sampled_from(SEARCH_CHUNKS))
def test_job_over_an_edited_lazy_document_matches_the_reference(draw: st.DataObject, original: bytes, operations: list[str], case_sensitive: bool, backward: bool, wrap: bool, chunk: int) -> None:
    doc, model = _build(draw, original, operations)
    try:
        needle = _needle(draw, model)
        origin = draw.draw(st.sampled_from(boundaries(model)))
        spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap)
        found = reference_search(model, needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap, origin=origin)
        expected = None if found is None else SearchResult(*found)
        assert SearchJob(doc, spec, origin, SearchSettings(chunk=chunk)).run() == expected
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


@DOCUMENT_SETTINGS
@given(st.data(), documents, OPERATIONS)
def test_search_plan_parts_concatenate_to_the_model_bytes(draw: st.DataObject, original: bytes, operations: list[str]) -> None:
    doc, model = _build(draw, original, operations)
    try:
        plan = doc.search_plan(0, doc.length)
        assert plan.length == len(model)
        assert b"".join(part.source.read(part.a, part.b - part.a) for part in plan.parts) == model
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)
