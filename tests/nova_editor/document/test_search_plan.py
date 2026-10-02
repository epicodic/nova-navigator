"""`LazyDocument.search_plan` and `revision` (ACT6 design 6): one lock hold, the open tail of a scan, no change of revision by a rebase."""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from nova_editor.core import BytesSource
from nova_editor.core.save import PlanPart
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from tests.nova_editor.document.helpers_save import SMALL, Session, open_pread_document
from tests.nova_editor.helpers_view import GateSource

JOIN_SECONDS = 10.0
TEXT = b"alpha\nbeta\r\ngamma\n" * 12


def _bytes_of(parts: list[PlanPart]) -> bytes:
    return b"".join(part.source.read(part.a, part.b - part.a) for part in parts)


def _document(config: LazyConfig = SMALL) -> LazyDocument:
    doc = LazyDocument(BytesSource(TEXT), config)
    assert doc.wait_indexed(JOIN_SECONDS)
    return doc


def _close(doc: LazyDocument) -> None:
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)


def _same_parts(left: list[PlanPart], right: list[PlanPart]) -> bool:
    return [(p.src, p.a, p.b) for p in left] == [(p.src, p.a, p.b) for p in right]


def test_search_plan_matches_plan_length_and_revision() -> None:
    doc = _document()
    try:
        doc.replace_range((1, 2), (1, 2), "XYZ")
        for offset, limit in ((0, doc.length), (7, 20), (doc.length - 3, 100), (doc.length + 5, 4)):
            found = doc.search_plan(offset, limit)
            assert _same_parts(found.parts, doc.plan(offset, limit, False))
            assert found.length == doc.length
            assert found.revision == doc.revision
    finally:
        _close(doc)


def test_revision_grows_on_every_kind_of_edit(tmp_path: Path) -> None:
    doc, data = open_pread_document(tmp_path, TEXT)
    session = Session(doc, data, tmp_path)
    try:
        seen = [doc.revision]
        session.edit((0, 1), (0, 1), "typed")
        seen.append(doc.revision)
        session.edit((1, 0), (1, 2), "")
        seen.append(doc.revision)
        session.copy((2, 0), (2, 3))
        session.paste((3, 0))
        seen.append(doc.revision)
        assert session.undo()
        seen.append(doc.revision)
        assert session.redo()
        seen.append(doc.revision)
        assert all(later > earlier for earlier, later in pairwise(seen)), seen
    finally:
        _close(doc)


def test_revision_does_not_grow_by_a_save_and_its_rebase(tmp_path: Path) -> None:
    doc, data = open_pread_document(tmp_path, TEXT)
    session = Session(doc, data, tmp_path)
    try:
        session.edit((0, 1), (0, 1), "typed")
        before = doc.revision
        session.save(tmp_path / "saved.bin")
        assert session.saves == 1
        assert doc.revision == before
        assert doc.search_plan(0, doc.length).revision == before
    finally:
        _close(doc)


def test_search_plan_of_a_closed_document_raises() -> None:
    doc = _document()
    _close(doc)
    with pytest.raises(RowUnavailable):
        doc.search_plan(0, 10)


def test_search_plan_over_an_unfinished_scan_does_not_wait(tmp_path: Path) -> None:
    path = tmp_path / "held.txt"
    path.write_bytes(TEXT)
    source = GateSource(path)
    source.gate.clear()  # the line scan blocks in its first read
    doc = LazyDocument(source, replace(SMALL, sync_scan_limit=0))
    try:
        assert not doc.wait_indexed(0.0)
        found = doc.search_plan(0, len(TEXT))
        assert found.length == len(TEXT)
        assert _bytes_of(found.parts) == TEXT
        tail = doc.search_plan(len(TEXT) - 9, 100)
        assert _bytes_of(tail.parts) == TEXT[-9:]
    finally:
        source.gate.set()
        _close(doc)


def test_search_plan_of_an_edited_document_reads_the_model_bytes() -> None:
    doc = _document()
    try:
        model = bytearray(TEXT)
        doc.replace_range((0, 2), (0, 2), "\u00e9\u00e9")
        model[2:2] = "\u00e9\u00e9".encode()
        found = doc.search_plan(0, doc.length)
        assert any(part.src != 0 for part in found.parts)
        assert _bytes_of(found.parts) == bytes(model)
        assert found.length == len(model)
    finally:
        _close(doc)
