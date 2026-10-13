"""Pilot tests of the text view working through the history that belongs to the document (S0003 ACT2 task 4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nova_editor.core import Content
from nova_editor.document._byte_edit import ByteEdit
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.document.test_lazy_byte_edit import DATA, Stepped
from tests.nova_editor.helpers_view import HostApp
from tests.nova_editor.save_widget_helpers import SaveHost, wait_saved

BODY = "".join(f"row {n}\n" for n in range(20))


@pytest.mark.asyncio
async def test_history_is_the_documents_history() -> None:
    area = NovaTextArea("one")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.history is area.document.history
        area.load_text("two")
        assert area.history is area.document.history
    assert NovaTextArea(max_checkpoints=3).history.max_checkpoints == 3


@pytest.mark.asyncio
async def test_max_checkpoints_survives_a_new_document() -> None:
    area = NovaTextArea("one", max_checkpoints=3)
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.load_text("two")
        assert area.history.max_checkpoints == 3


@pytest.mark.asyncio
async def test_undo_and_redo_go_through_the_shared_history() -> None:
    area = NovaTextArea("abc")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.insert("x", (0, 0))
        history = area.document.history
        with patch.object(history, "undo", wraps=history.undo) as undo_spy:
            area.undo()
        assert undo_spy.call_count == 1
        with patch.object(history, "redo", wraps=history.redo) as redo_spy:
            area.redo()
        assert redo_spy.call_count == 1
        assert area.text == "xabc"


@pytest.mark.asyncio
async def test_undo_after_save_and_modified_follow_the_shared_flag(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x", "y")
        assert area.modified == area.document.history.modified is True
        assert area.save() is True
        await wait_saved(pilot, area)
        assert area.modified == area.document.history.modified is False
        area.undo()
        assert area.modified is True
        assert area.modified == area.document.history.modified
        area.redo()
        assert area.modified == area.document.history.modified is False


@pytest.mark.asyncio
async def test_references_roundtrip_through_the_history_api() -> None:
    area = NovaTextArea("hello world\nsecond")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.delete((0, 0), (0, 6))
        area.select_all()
        area.action_copy()
        record = area._clipboard_record
        assert record is not None
        reachable = area._reachable_contents()
        assert reachable == [*area.history.references(), record.content]
        area._apply_translated(list(reachable))
        assert area._reachable_contents() == reachable


EMPTY = Content.from_pieces((), 0)


def hex_edit(document: LazyDocument, start: int, end: int, data: bytes) -> ByteEdit:
    """Splice `document` and record the splice in its history as the hex view does (S0003 ACT2 task 5)."""
    content = document._table.add(data) if data else EMPTY
    result = document.splice_bytes(start, end, content)
    assert result.removed is not None
    assert result.inserted is not None
    entry = ByteEdit(start, result.removed, result.inserted, origin="hex")
    document.history.record(entry)
    return entry


def all_bytes(document: LazyDocument) -> bytes:
    return document.read_bytes(0, document.length)


@pytest.mark.asyncio
async def test_foreign_entry_undo_restores_bytes_and_places_the_cursor() -> None:
    area = NovaTextArea("hello\nworld\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        hex_edit(document, 6, 6, b"XY")
        assert all_bytes(document) == b"hello\nXYworld\n"
        area.undo()
        await pilot.pause()
        assert all_bytes(document) == b"hello\nworld\n"
        assert area.cursor_byte_offset == 6
        assert area.modified is False
        area.redo()
        await pilot.pause()
        assert all_bytes(document) == b"hello\nXYworld\n"
        assert area.cursor_byte_offset == 8
        assert area.modified is True


@pytest.mark.asyncio
async def test_undo_of_an_append_at_the_end_lands_on_the_end_at_once() -> None:
    area = NovaTextArea("abc\ndef\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        end = document.length
        hex_edit(document, end, end, b"tail")
        area.undo()
        assert area.pending_progress is None
        assert area.cursor_byte_offset == end == document.length
        area.redo()
        assert area.pending_progress is None
        assert area.cursor_byte_offset == document.length


@pytest.mark.asyncio
async def test_foreign_entry_beyond_the_scan_frontier_waits() -> None:
    case = Stepped(DATA)
    area = NovaTextArea(_prebuilt_document=case.doc)
    host = HostApp(area)
    try:
        async with host.run_test() as pilot:
            await pilot.pause()
            start = len(DATA) - 2
            assert case.doc.snapshot().scanned_bytes < start
            hex_edit(case.doc, start, start, b"ZZ")
            area.undo()
            assert all_bytes(case.doc) == DATA
            assert area.pending_progress is not None
            case.finish()
            await pilot.pause(delay=0.2)
            assert area.pending_progress is None
    finally:
        case.source.stop()


@pytest.mark.asyncio
async def test_wrap_update_for_foreign_entries_uses_the_start_row() -> None:
    area = NovaTextArea("aa\nbb\ncc\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        hex_edit(document, 3, 3, b"X")
        with patch.object(area.wrapped_document, "wrap_range", wraps=area.wrapped_document.wrap_range) as spy:
            area.undo()
        found = document.row_at_offset(3)
        row = found[0] if found is not None else 0
        spy.assert_called_once_with((row, 0), (row, 0), (row, 0))


@pytest.mark.asyncio
async def test_hidden_widget_marks_itself_stale_on_foreign_edits() -> None:
    area = NovaTextArea("abc\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area._stale is False
        area.document.splice_bytes(0, 0, area.document._table.add(b"Q"))
        assert area._stale is True
        area._stale = False
        area.insert("x", (0, 0))
        assert area._stale is False
        area.undo()
        area.redo()
        assert area._stale is False


@pytest.mark.asyncio
async def test_listener_removed_on_replace_and_close() -> None:
    area = NovaTextArea("one")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        old = area.document
        assert len(old._change_listeners) == 1
        area.load_text("two")
        assert old._change_listeners == []
        assert len(area.document._change_listeners) == 1
        current = area.document
        area.close()
        assert current._change_listeners == []


@pytest.mark.asyncio
async def test_mixed_batch_treated_by_origin() -> None:
    area = NovaTextArea("abc\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        area.insert("T", (0, 0))
        hex_edit(document, document.length, document.length, b"H")
        assert all_bytes(document) == b"Tabc\nH"
        assert len(document.history.undo_stack) == 2
        area.undo()
        assert all_bytes(document) == b"Tabc\n"
        area.undo()
        assert all_bytes(document) == b"abc\n"
