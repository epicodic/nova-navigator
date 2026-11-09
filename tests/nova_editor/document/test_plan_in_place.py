"""`LazyDocument.plan_in_place`: the in-place plan for the layouts of ACT3 design 2 (no byte is read, the table is not changed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core import Content, PreadSource
from nova_editor.document import LazyDocument, RowUnavailable, _lazy_document
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import InPlacePlan
from tests.nova_editor.document.helpers_save import SMALL, open_pread_document, run_save

ROW = 20
DATA = b"".join(b"row %02d xxxxxxxxxxxx\n" % i for i in range(12))
EMPTY = Content.from_pieces((), 0)


def _overwrite(doc: LazyDocument, start: int, data: bytes) -> None:
    doc.splice_bytes(start, start + len(data), doc._table.add(data))


def test_the_row_constant_matches_the_data() -> None:
    assert len(DATA) == 12 * ROW


def test_an_unedited_document_has_a_plan_without_ranges(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ())
    finally:
        doc.close()


def test_a_same_length_overwrite_yields_its_range(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        _overwrite(doc, 45, b"ZZZ")
        _overwrite(doc, 100, b"Q")
        plan = doc.plan_in_place()
        assert plan == InPlacePlan(len(DATA), ((45, 48), (100, 101)))
    finally:
        doc.close()


def test_an_undone_overwrite_leaves_no_ranges(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        removed = doc.splice_bytes(45, 48, doc._table.add(b"ZZZ")).removed
        assert removed is not None
        assert doc.splice_bytes(45, 48, removed).inserted is not None
        assert doc.read_bytes(0, doc.length) == DATA
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ())
    finally:
        doc.close()


def test_insert_and_delete_that_restore_the_length_stream(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        _overwrite(doc, 5, b"A")
        doc.splice_bytes(60, 60, doc._table.add(b"123"))
        doc.splice_bytes(100, 103, EMPTY)
        assert doc.length == len(DATA)
        assert doc.plan_in_place() is None
    finally:
        doc.close()


def test_a_different_length_streams(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        doc.splice_bytes(60, 60, doc._table.add(b"123"))
        assert doc.plan_in_place() is None
    finally:
        doc.close()


def test_a_closed_document_raises(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    doc.close()
    assert doc.wait_closed(10.0)
    with pytest.raises(RowUnavailable):
        doc.plan_in_place()


def test_a_memory_document_has_a_plan() -> None:
    doc = LazyDocument.from_bytes(DATA, SMALL)
    try:
        _overwrite(doc, 3, b"Z")
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ((3, 4),))
    finally:
        doc.close()


def test_an_unscanned_document_plans_without_filling_the_table(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(DATA)
    doc = LazyDocument(PreadSource(path), LazyConfig(stride=2, scan_block=8, sync_scan_limit=0), autostart=False)
    try:
        assert doc._table.has_unknown
        _overwrite(doc, 200, b"Z")
        layout = doc._table.layout_range(0, doc.length)
        unknown = doc._table.tree.unknown
        assert unknown > 0
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ((200, 201),))
        assert doc._table.layout_range(0, doc.length) == layout
        assert doc._table.tree.unknown == unknown
    finally:
        doc.close()


def test_a_legacy_generation_piece_is_a_dirty_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", 4)
    target = tmp_path / "doc.bin"
    doc, _ = open_pread_document(tmp_path, DATA)
    try:
        removed = doc.splice_bytes(2 * ROW, 3 * ROW, EMPTY).removed
        assert removed is not None
        _, rebase = run_save(doc, target, [removed])
        retained = rebase.contents[0]  # the translated content: its pieces now name the legacy generation
        assert doc._table.legacy
        assert doc.plan_in_place() == InPlacePlan(len(DATA) - ROW, ())
        doc.splice_bytes(ROW, 2 * ROW, retained)  # overwrite the row at ROW with the retained (legacy) content
        plan = doc.plan_in_place()
        assert plan == InPlacePlan(len(DATA) - ROW, ((ROW, 2 * ROW),))
    finally:
        doc.close()
