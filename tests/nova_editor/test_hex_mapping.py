"""The cursor follows the switch between the text and the hex view by byte offset (REQ-2, S0003 ACT4 task 5, design 6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from textual.pilot import Pilot

from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document_view import DocumentView
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, gated_source, open_with_gated_line_scan, release_line_scan, wait_until
from tests.nova_editor.test_document_view_hex import ViewHost

SIZE = (80, 24)


def view_of(tmp_path: Path, data: bytes, *, config: LazyConfig | None = None) -> DocumentView:
    path = tmp_path / "doc.txt"
    path.write_bytes(data)
    return DocumentView(NovaTextArea.open(path, config=config, id="editor"), path)


async def to_hex(pilot: Pilot[None], view: DocumentView) -> None:
    view.show_hex(True)
    await wait_until(pilot, lambda: pilot.app.focused is view.hex_view)
    await pilot.pause()


async def to_text(pilot: Pilot[None], view: DocumentView) -> None:
    view.show_hex(False)
    await wait_until(pilot, lambda: pilot.app.focused is view.editor)
    await pilot.pause()


async def hex_offset_for(pilot: Pilot[None], view: DocumentView, location: tuple[int, int]) -> int:
    """Put the text cursor on `location`, switch to hex and return the hex cursor offset."""
    view.editor.cursor_location = location
    await pilot.pause()
    await to_hex(pilot, view)
    offset = view.hex_view.cursor.offset
    assert view.hex_view.cursor.nibble == 0
    await to_text(pilot, view)
    return offset


async def text_cursor_for(pilot: Pilot[None], view: DocumentView, offset: int) -> tuple[int, int]:
    """Put the hex cursor on `offset`, switch to text and return the text cursor location once the jump ended."""
    await to_hex(pilot, view)
    view.hex_view.set_cursor(offset)
    await to_text(pilot, view)
    await wait_until(pilot, lambda: view.editor.pending_progress is None)
    await pilot.pause()
    return view.editor.cursor_location


@pytest.mark.asyncio
async def test_text_to_hex_on_ascii(tmp_path: Path) -> None:
    view = view_of(tmp_path, b"abcdef\n")
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await hex_offset_for(pilot, view, (0, 3)) == 3


@pytest.mark.asyncio
async def test_text_to_hex_on_a_multibyte_character_is_its_first_byte(tmp_path: Path) -> None:
    view = view_of(tmp_path, "aäb\n".encode())
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await hex_offset_for(pilot, view, (0, 1)) == 1  # C3 of C3 A4
        assert await hex_offset_for(pilot, view, (0, 2)) == 3  # `b` after the two bytes of `ä`


@pytest.mark.asyncio
async def test_text_to_hex_at_the_end_of_the_document(tmp_path: Path) -> None:
    data = b"one\ntwo\n"
    view = view_of(tmp_path, data)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await hex_offset_for(pilot, view, (2, 0)) == len(data)


@pytest.mark.asyncio
async def test_text_to_hex_on_a_crlf_file(tmp_path: Path) -> None:
    view = view_of(tmp_path, b"ab\r\ncd\r\n")
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await hex_offset_for(pilot, view, (1, 1)) == 5


@pytest.mark.asyncio
async def test_text_to_hex_in_a_long_row(tmp_path: Path) -> None:
    data = b"x" * 3000 + b"\nend\n"
    view = view_of(tmp_path, data, config=LazyConfig(**LOWERED_OPTIONS))
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        view.editor.cursor_location = (0, 2500)
        await wait_until(pilot, lambda: view.editor.cursor_byte_offset == 2500)
        await to_hex(pilot, view)
        assert view.hex_view.cursor.offset == 2500


@pytest.mark.asyncio
async def test_an_unknown_text_offset_keeps_the_previous_hex_offset(tmp_path: Path) -> None:
    view = view_of(tmp_path, b"0123456789\n")
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await text_cursor_for(pilot, view, 5) == (0, 5)
        view.editor.cursor_location = (0, 0)
        await pilot.pause()
        with patch.object(NovaTextArea, "cursor_byte_offset", new_callable=PropertyMock, return_value=None):
            await to_hex(pilot, view)
        assert view.hex_view.cursor.offset == 5


@pytest.mark.asyncio
async def test_hex_to_text_on_both_bytes_of_a_multibyte_character(tmp_path: Path) -> None:
    view = view_of(tmp_path, "aäb\n".encode())
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await text_cursor_for(pilot, view, 1) == (0, 1)
        assert view.editor.cursor_byte_offset == 1
        assert await text_cursor_for(pilot, view, 2) == (0, 1)  # the second byte lands on the character
        assert view.editor.cursor_byte_offset == 1


@pytest.mark.asyncio
async def test_hex_to_text_on_the_line_break_of_a_crlf_file(tmp_path: Path) -> None:
    view = view_of(tmp_path, b"ab\r\ncd\r\n")
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await text_cursor_for(pilot, view, 2) == (0, 2)  # `\r`: the end of row 0
        assert await text_cursor_for(pilot, view, 3) == (0, 2)  # `\n` of the same terminator
        assert await text_cursor_for(pilot, view, 4) == (1, 0)


@pytest.mark.asyncio
async def test_hex_to_text_at_the_end_of_the_document(tmp_path: Path) -> None:
    data = b"one\ntwo\n"
    view = view_of(tmp_path, data)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert await text_cursor_for(pilot, view, len(data)) == (2, 0)
        assert view.editor.cursor_byte_offset == len(data)


@pytest.mark.asyncio
async def test_hex_to_text_at_an_unscanned_offset_waits_for_the_scan(tmp_path: Path) -> None:
    area, path = open_with_gated_line_scan(tmp_path, rows=3000)
    area.id = "editor"
    data = path.read_bytes()
    target = data.index(b"row 2500")
    view = DocumentView(area, path)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        try:
            await wait_until(pilot, gated_source(area).blocked.is_set)
            await to_hex(pilot, view)
            view.hex_view.set_cursor(target)
            await to_text(pilot, view)
            assert area.pending_progress is not None
            assert area.cursor_location == (0, 0)
            release_line_scan(area)
            await wait_until(pilot, lambda: area.pending_progress is None and area.cursor_byte_offset == target)
            assert area.cursor_location == (2500, 0)
        finally:
            release_line_scan(area)


@pytest.mark.asyncio
async def test_toggle_during_a_pending_goto_leaves_no_pending_progress(tmp_path: Path) -> None:
    area, path = open_with_gated_line_scan(tmp_path, rows=3000)
    area.id = "editor"
    target = path.read_bytes().index(b"row 2500")
    view = DocumentView(area, path)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        try:
            await wait_until(pilot, gated_source(area).blocked.is_set)
            area.goto_byte(target)
            await pilot.pause()
            assert area.pending_progress is not None
            await to_hex(pilot, view)
            assert area.pending_progress is None
            release_line_scan(area)
            await wait_until(pilot, lambda: area.document.wait_indexed(0))
            await pilot.pause(0.1)
            assert area.cursor_location == (0, 0)  # the dropped goto did not move the hidden cursor later
            assert view.hex_view.cursor.offset == 0
        finally:
            release_line_scan(area)


@pytest.mark.asyncio
async def test_the_selection_is_not_carried_in_either_direction(tmp_path: Path) -> None:
    view = view_of(tmp_path, b"abcdef\nghijkl\n")
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        view.editor.selection = Selection((0, 1), (0, 4))
        await pilot.pause()
        await to_hex(pilot, view)
        assert view.hex_view.selection is None
        assert view.hex_view.cursor.offset == 4
        view.hex_view.select_all()
        assert view.hex_view.selection is not None
        await to_text(pilot, view)
        assert view.editor.selection.is_empty
        assert view.editor.cursor_byte_offset == 14
        await to_hex(pilot, view)
        assert view.hex_view.selection is None


@pytest.mark.asyncio
async def test_a_far_offset_is_scrolled_into_view_in_both_directions(tmp_path: Path) -> None:
    data = b"".join(b"row %04d\n" % n for n in range(500))
    view = view_of(tmp_path, data)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert view.editor.document.wait_indexed(10)
        target = data.index(b"row 0400")
        assert await text_cursor_for(pilot, view, target) == (400, 0)
        top = view.editor.scroll_offset.y
        assert top <= 400 < top + view.editor.scrollable_content_region.height
        view.editor.cursor_location = (10, 0)
        await pilot.pause()
        view.editor.scroll_to(y=0, animate=False, immediate=True)
        view.editor.cursor_location = (450, 0)
        await to_hex(pilot, view)
        hex_row = view.hex_view.cursor.offset // view.hex_view.hex_layout.bytes_per_row
        assert view.hex_view.cursor.offset == data.index(b"row 0450")
        hex_top = view.hex_view.scroll_offset.y
        assert hex_top <= hex_row < hex_top + view.hex_view.scrollable_content_region.height


# Rapid toggles: a second switch before the first one finished must not read the stale cursor of the widget it leaves


LONG = b"".join(b"row %04d\n" % n for n in range(400))


async def settle(pilot: Pilot[None], view: DocumentView) -> None:
    """Wait until the shown widget has the focus and the text jump (if any) ended."""
    await wait_until(pilot, lambda: pilot.app.focused is view.shown and view.editor.pending_progress is None)
    await pilot.pause()


@pytest.mark.asyncio
async def test_a_double_toggle_from_hex_keeps_the_hex_cursor(tmp_path: Path) -> None:
    view = view_of(tmp_path, LONG)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await to_hex(pilot, view)
        view.hex_view.set_cursor(1000)
        view.show_hex(False)
        view.show_hex(True)  # before the refresh that would place the text cursor
        await settle(pilot, view)
        assert view.hex_mode
        assert view.hex_view.cursor.offset == 1000
        await to_text(pilot, view)
        await settle(pilot, view)
        assert view.editor.cursor_byte_offset == 1000


@pytest.mark.asyncio
async def test_a_double_toggle_from_text_keeps_the_text_cursor(tmp_path: Path) -> None:
    view = view_of(tmp_path, LONG)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await to_hex(pilot, view)
        view.hex_view.set_cursor(0)
        await to_text(pilot, view)
        view.editor.cursor_location = (111, 3)
        await pilot.pause()
        offset = view.editor.cursor_byte_offset
        assert offset == 111 * 9 + 3
        view.show_hex(True)
        view.show_hex(False)  # before the refresh that would place the hex cursor
        await settle(pilot, view)
        assert not view.hex_mode
        assert view.editor.cursor_byte_offset == offset
        assert view.editor.cursor_location == (111, 3)


@pytest.mark.asyncio
async def test_a_triple_toggle_from_hex_ends_in_text_on_the_hex_byte(tmp_path: Path) -> None:
    view = view_of(tmp_path, LONG)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await to_hex(pilot, view)
        view.hex_view.set_cursor(1000)
        view.show_hex(False)
        view.show_hex(True)
        view.show_hex(False)
        await settle(pilot, view)
        assert not view.hex_mode
        assert view.editor.cursor_byte_offset == 1000


@pytest.mark.asyncio
async def test_a_pending_switch_to_text_drops_the_old_text_selection_at_once(tmp_path: Path) -> None:
    area, path = open_with_gated_line_scan(tmp_path, rows=3000)
    area.id = "editor"
    target = path.read_bytes().index(b"row 2500")
    view = DocumentView(area, path)
    async with ViewHost(view).run_test(size=SIZE) as pilot:
        try:
            await wait_until(pilot, gated_source(area).blocked.is_set)
            area.selection = Selection((0, 1), (1, 2))
            await pilot.pause()
            await to_hex(pilot, view)
            view.hex_view.set_cursor(target)
            await to_text(pilot, view)
            assert area.pending_progress is not None
            assert area.selection.is_empty
            release_line_scan(area)
            await wait_until(pilot, lambda: area.pending_progress is None and area.cursor_byte_offset == target)
            assert area.selection.is_empty
            assert area.cursor_location == (2500, 0)
        finally:
            release_line_scan(area)
