"""The document side of the in-place save: patch bookkeeping, an unchanged document afterwards, the legacy generation case and the rebase (ACT3 design 3, 6, 12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core import Content, Redirect
from nova_editor.document import LazyDocument, _lazy_document
from tests.nova_editor.document.helpers_save import JOIN_SECONDS, content_of, open_pread_document, run_in_place, run_save

ROW = 20
DATA = b"".join(b"row %02d xxxxxxxxxxxx\n" % i for i in range(12))
EMPTY = Content.from_pieces((), 0)


def _overwrite(doc: LazyDocument, start: int, data: bytes) -> Content:
    removed = doc.splice_bytes(start, start + len(data), content_of(doc, data)).removed
    assert removed is not None
    return removed


def test_plan_in_place_after_a_job_leaves_the_document_unchanged(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        _overwrite(doc, 45, b"ZZZ")
        _overwrite(doc, 100, b"Q")
        edits, layout, entries = doc.revision, doc._table.layout_range(0, doc.length), doc._line_index.stored_entries()
        before = doc.snapshot()
        run_in_place(doc, path)
        assert doc.revision == edits
        assert doc._table.layout_range(0, doc.length) == layout
        assert doc._line_index.stored_entries() == entries  # no rescan
        assert doc.snapshot() == before
        assert doc.read_bytes(0, doc.length) == path.read_bytes()
        assert doc.plan_in_place() is not None
        assert doc._source.read(45, 3) == DATA[45:48]  # the line index still walks the pre-patch bytes
    finally:
        doc.close()


def test_a_second_job_with_nothing_new_rewrites_the_dirty_ranges(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        _overwrite(doc, 45, b"ZZZ")
        assert run_in_place(doc, path).written == 3
        assert run_in_place(doc, path).written == 3  # a repeat save writes every dirty range again (design 6.6)
        assert path.read_bytes() == DATA[:45] + b"ZZZ" + DATA[48:]
    finally:
        doc.close()


def test_rollback_removes_only_the_ranges_added_by_the_job(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        _overwrite(doc, 10, b"ab")
        run_in_place(doc, path)
        assert doc._patched == [(10, 12)]
        _overwrite(doc, 11, b"cd")  # (11, 13): one byte patched already
        plan = doc.plan_in_place()
        assert plan is not None
        copies = doc.prepare_patch(plan.ranges, 4, lambda: None)
        assert [(copy.a, copy.b) for copy in copies] == [(12, 13)]
        doc.begin_patch(copies, plan.ranges)
        assert doc._patched == [(10, 13)]
        doc.end_patch(None)
        assert doc._patched == [(10, 12)]
        doc.abort_patch()  # nothing is open: harmless
        assert doc._patched == [(10, 12)]
        run_in_place(doc, path)  # the overlay that stayed does not clash with the next job
        assert path.read_bytes() == doc.read_bytes(0, doc.length)
    finally:
        doc.close()


def test_a_closed_document_refuses_a_patch(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)
    with pytest.raises(_lazy_document.RowUnavailable):
        doc.prepare_patch([(0, 1)], 4, lambda: None)
    with pytest.raises(_lazy_document.RowUnavailable):
        doc.begin_patch([], [(0, 1)])


def test_a_streaming_save_clears_the_patched_ranges(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        _overwrite(doc, 45, b"ZZZ")
        run_in_place(doc, path)
        assert doc._patched == [(45, 48)]
        assert doc._overlaid == [(45, 48)]
        old = doc._source
        run_save(doc, path, [])  # the streaming save installs the written file as the new original
        assert doc.wait_rebased(JOIN_SECONDS)
        assert doc._source is not old
        assert doc._patched == []
        assert doc._overlaid == []
        plan = doc.plan_in_place()
        assert plan is not None
        assert plan.ranges == ()
        _overwrite(doc, 5, b"Q")
        plan = doc.plan_in_place()
        assert plan is not None
        assert plan.ranges == ((5, 6),)  # the earlier patch is not remembered against the new original
    finally:
        doc.close()


def test_a_legacy_generation_run_at_an_original_offset_is_written_from_the_legacy_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", 4)
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        removed = doc.splice_bytes(2 * ROW, 3 * ROW, EMPTY).removed
        assert removed is not None
        _, rebase = run_save(doc, path, [removed])
        assert doc.wait_rebased(JOIN_SECONDS)
        retained = rebase.contents[0]  # names the legacy generation: the old file with the deleted row
        assert doc._table.legacy
        doc.splice_bytes(2 * ROW, 2 * ROW, retained)  # undo the delete: the row comes back from the legacy source
        doc.splice_bytes(3 * ROW, 4 * ROW, EMPTY)  # delete the next row of the new original: the length equals the original's again
        assert doc.length == len(DATA) - ROW
        plan = doc.plan_in_place()
        assert plan is not None
        assert plan.ranges == ((2 * ROW, 3 * ROW),)
        expected = doc.read_bytes(0, doc.length)
        assert expected[2 * ROW : 3 * ROW] == DATA[2 * ROW : 3 * ROW]
        assert path.read_bytes()[2 * ROW : 3 * ROW] == DATA[3 * ROW : 4 * ROW]
        result = run_in_place(doc, path)
        assert result.written == ROW
        assert path.read_bytes() == expected
    finally:
        doc.close()


def test_redirected_history_content_survives_a_job(tmp_path: Path) -> None:
    doc, _ = open_pread_document(tmp_path, DATA)
    path = tmp_path / "doc.bin"
    try:
        removed = _overwrite(doc, 45, b"ZZZ")
        result = run_in_place(doc, path)
        source = doc._source
        redirected = Redirect(result.copies, source, doc._add_store).apply(removed)
        assert doc._table.content_bytes(redirected, 0, redirected.length) == DATA[45:48]
    finally:
        doc.close()
