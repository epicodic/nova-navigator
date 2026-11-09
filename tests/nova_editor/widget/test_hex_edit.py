"""Typing, Insert, Delete, Backspace, read-only and the edit lock in the hex view (S0003 ACT5 commit 3, REQ-9, REQ-10, REQ-11)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual import events
from textual.pilot import Pilot

from nova_editor.document import LazyDocument
from nova_editor.widget import HexView
from nova_editor.widget.hex_layout import Pane
from nova_editor.widget.hex_view import ByteSelection, HexCursor
from tests.nova_editor.widget.hex_helpers import HexHost, opened, row_texts, write_file


@dataclass
class Session:
    """One running hex view over a document, with spies for the bell and the notifications."""

    document: LazyDocument
    view: HexView
    host: HexHost
    pilot: Pilot[None]
    bell: MagicMock
    notify: MagicMock

    async def press(self, *keys: str) -> HexCursor:
        """Press `keys`, let the app settle and return the cursor."""
        await self.pilot.press(*keys)
        await self.pilot.pause()
        return self.view.cursor

    async def send(self, key: str, character: str) -> HexCursor:
        """Send a key event with a character, as a terminal does for Enter and the control keys (the Pilot gives those none)."""
        self.view.post_message(events.Key(key, character))
        await self.pilot.pause()
        return self.view.cursor

    def data(self) -> bytes:
        """The whole document."""
        return self.document.read_bytes(0, self.document.length)


@asynccontextmanager
async def session(tmp_path: Path, data: bytes, *, size: tuple[int, int] = (80, 24), read_only: bool = False) -> AsyncIterator[Session]:
    with opened(write_file(tmp_path, data)) as document:
        view = HexView(lambda: document)
        if read_only:
            view.is_read_only = lambda: True
        host = HexHost(view)
        with patch.object(host, "bell") as bell, patch.object(host, "notify") as notify:
            async with host.run_test(size=size) as pilot:
                await pilot.pause()
                yield Session(document, view, host, pilot, bell, notify)


# -- REQ-9: Insert key and modes --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_view_starts_in_overwrite_mode_and_is_writable(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        assert s.view.insert_mode is False
        assert s.view.is_read_only() is False


@pytest.mark.asyncio
async def test_insert_toggles_the_mode_and_posts_mode_changed(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        await s.press("insert")
        assert s.view.insert_mode is True
        await s.press("insert")
        assert s.view.insert_mode is False
        assert s.host.modes == [True, False]
        assert s.document.history.modified is False


@pytest.mark.asyncio
async def test_toggling_insert_starts_a_new_undo_batch(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x10, 0x20, 0x30])) as s:
        await s.press("a")
        await s.press("insert", "insert")
        await s.press("b")
        assert s.data() == bytes([0xAB, 0x20, 0x30])
        assert s.document.history.undo(s.document) is not None
        assert s.data() == bytes([0xA0, 0x20, 0x30])


@pytest.mark.asyncio
async def test_overwrite_high_digit_keeps_the_low_nibble(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x12, 0x34])) as s:
        assert await s.press("a") == HexCursor(0, 1, "hex")
        assert s.data() == bytes([0xA2, 0x34])
        assert s.document.length == 2


@pytest.mark.asyncio
async def test_overwrite_low_digit_keeps_the_high_nibble_and_moves_to_the_next_byte(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x12, 0x34])) as s:
        assert await s.press("a", "b") == HexCursor(1, 0, "hex")
        assert s.data() == bytes([0xAB, 0x34])
        assert await s.press("c", "d") == HexCursor(2, 0, "hex")
        assert s.data() == bytes([0xAB, 0xCD])
        assert row_texts(s.view)[0].startswith("00000000  AB CD")


@pytest.mark.asyncio
async def test_upper_and_lower_case_digits_are_accepted(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(3)) as s:
        await s.press("A", "b", "C", "d", "0", "f")
        assert s.data() == bytes([0xAB, 0xCD, 0x0F])


@pytest.mark.asyncio
async def test_insert_mode_inserts_x0_before_the_byte_and_the_next_digit_sets_the_low_nibble(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x12, 0x34])) as s:
        await s.press("insert")
        assert await s.press("a") == HexCursor(0, 1, "hex")
        assert s.data() == bytes([0xA0, 0x12, 0x34])
        assert await s.press("b") == HexCursor(1, 0, "hex")
        assert s.data() == bytes([0xAB, 0x12, 0x34])
        assert s.document.length == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("insert", [False, True])
async def test_a_digit_at_the_end_position_appends_in_both_modes(tmp_path: Path, insert: bool) -> None:
    async with session(tmp_path, bytes([0x12])) as s:
        if insert:
            await s.press("insert")
        assert await s.press("ctrl+end") == HexCursor(1, 0, "hex")
        assert await s.press("f") == HexCursor(1, 1, "hex")
        assert s.data() == bytes([0x12, 0xF0])
        assert await s.press("1") == HexCursor(2, 0, "hex")
        assert s.data() == bytes([0x12, 0xF1])


@pytest.mark.asyncio
async def test_typing_into_an_empty_document_appends(tmp_path: Path) -> None:
    async with session(tmp_path, b"") as s:
        assert await s.press("4", "1") == HexCursor(1, 0, "hex")
        assert s.data() == b"\x41"


@pytest.mark.asyncio
async def test_keys_that_are_not_hex_digits_are_silent_in_the_hex_pane(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x12, 0x34])) as s:
        before = s.view.cursor
        assert await s.press("g", "z", "enter", "space", "ä", "plus") == before
        assert s.data() == bytes([0x12, 0x34])
        assert s.bell.call_count == 0
        assert s.host.edited == 0


@pytest.mark.asyncio
async def test_typing_with_a_selection_collapses_it_to_its_start(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x11, 0x22, 0x33, 0x44])) as s:
        await s.press("right", "right")
        await s.press("shift+right", "shift+right", "shift+right")
        assert s.view.selection is not None
        assert await s.press("a") == HexCursor(1, 1, "hex")
        assert s.view.selection is None
        assert s.data() == bytes([0x11, 0xA2, 0x33, 0x44])


@pytest.mark.asyncio
async def test_two_digits_typed_in_insert_mode_undo_together(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x10, 0x20])) as s:
        await s.press("insert", "4", "1")
        assert s.data() == bytes([0x41, 0x10, 0x20])
        assert s.document.history.undo(s.document) is not None
        assert s.data() == bytes([0x10, 0x20])


@pytest.mark.asyncio
async def test_a_cursor_move_between_the_digits_splits_the_undo_step(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x10, 0x20])) as s:
        await s.press("insert", "4")
        await s.press("right", "left")
        await s.press("1")
        assert s.data() == bytes([0x41, 0x10, 0x20])
        assert s.document.history.undo(s.document) is not None
        assert s.data() == bytes([0x40, 0x10, 0x20])


@pytest.mark.asyncio
async def test_two_overwrite_bytes_typed_in_a_row_undo_together(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x10, 0x20, 0x30])) as s:
        await s.press("a", "b", "c", "d")
        assert s.data() == bytes([0xAB, 0xCD, 0x30])
        assert s.document.history.undo(s.document) is not None
        assert s.data() == bytes([0x10, 0x20, 0x30])


@pytest.mark.asyncio
async def test_an_edit_marks_the_document_modified_and_posts_edited(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(4)) as s:
        assert s.document.history.modified is False
        await s.press("1")
        assert s.document.history.modified is True
        await s.press("2")
        assert s.host.edited == 2


@pytest.mark.asyncio
async def test_an_edit_repaints_the_row_and_keeps_the_cursor_in_view(tmp_path: Path) -> None:
    async with session(tmp_path, bytes(2000)) as s:
        await s.press("ctrl+end")
        await s.press("f", "f")
        height = s.view.scrollable_content_region.height
        row = s.view.cursor.offset // s.view.hex_layout.bytes_per_row
        assert s.view.scroll_offset.y <= row < s.view.scroll_offset.y + height
        assert s.document.length == 2001
        assert "FF" in row_texts(s.view)[row - s.view.scroll_offset.y]
        assert s.view.virtual_size.height == s.document.length // s.view.hex_layout.bytes_per_row + 1


# -- REQ-10: the character pane ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_printable_character_overwrites_in_the_character_pane(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab", "right")
        assert await s.press("X") == HexCursor(2, 0, "char")
        assert s.data() == b"aXcd"
        assert s.bell.call_count == 0


@pytest.mark.asyncio
async def test_a_printable_character_inserts_in_insert_mode(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("insert", "tab", "right")
        assert await s.press("X", "space") == HexCursor(3, 0, "char")
        assert s.data() == b"aX bcd"


@pytest.mark.asyncio
@pytest.mark.parametrize("insert", [False, True])
async def test_a_character_at_the_end_position_appends(tmp_path: Path, insert: bool) -> None:
    async with session(tmp_path, b"ab") as s:
        if insert:
            await s.press("insert")
        await s.press("tab", "ctrl+end")
        assert await s.press("Z") == HexCursor(3, 0, "char")
        assert s.data() == b"abZ"


@pytest.mark.asyncio
async def test_the_last_byte_overwritten_in_the_character_pane_lands_on_the_end_position(tmp_path: Path) -> None:
    async with session(tmp_path, b"ab") as s:
        await s.press("tab", "right")
        assert await s.press("Q") == HexCursor(2, 0, "char")
        assert s.data() == b"aQ"


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "character"), [("ä", "ä"), ("enter", "\r"), ("ctrl+g", "\x07"), ("euro_sign", "€")])
async def test_other_characters_change_nothing_and_ring_the_bell(tmp_path: Path, key: str, character: str) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab")
        assert await s.send(key, character) == HexCursor(0, 0, "char")
        assert s.data() == b"abcd"
        assert s.bell.call_count == 1
        assert s.document.history.modified is False


@pytest.mark.asyncio
async def test_a_bound_control_character_is_not_typing_and_does_not_ring(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab")
        await s.send("tab", "\t")
        await s.send("backspace", "\x7f")
        assert s.data() == b"abcd"
        assert s.bell.call_count == 0


@pytest.mark.asyncio
async def test_escape_is_not_typing(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab")
        await s.send("escape", "\x1b")
        assert s.bell.call_count == 0


@pytest.mark.asyncio
async def test_keys_without_a_character_are_not_typing(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab")
        await s.press("f5", "f6", "escape")
        assert s.data() == b"abcd"
        assert s.bell.call_count == 0


# -- REQ-11: Delete and Backspace -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("insert", [False, True])
async def test_delete_in_the_hex_pane_removes_the_byte_whichever_digit_the_cursor_is_on(tmp_path: Path, insert: bool) -> None:
    async with session(tmp_path, b"abcd") as s:
        if insert:
            await s.press("insert")
        await s.press("right", "right", "right")  # byte 1, low digit
        assert s.view.cursor == HexCursor(1, 1, "hex")
        assert await s.press("delete") == HexCursor(1, 0, "hex")
        assert s.data() == b"acd"


@pytest.mark.asyncio
async def test_delete_in_the_character_pane(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab", "right")
        assert await s.press("delete") == HexCursor(1, 0, "char")
        assert s.data() == b"acd"


@pytest.mark.asyncio
async def test_backspace_removes_the_byte_before_the_cursor_whichever_digit_it_is_on(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("right", "right", "right")  # byte 1, low digit
        assert await s.press("backspace") == HexCursor(0, 0, "hex")
        assert s.data() == b"bcd"


@pytest.mark.asyncio
async def test_backspace_in_the_character_pane(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("tab", "right", "right")
        assert await s.press("backspace") == HexCursor(1, 0, "char")
        assert s.data() == b"acd"


@pytest.mark.asyncio
async def test_delete_at_the_end_position_and_backspace_at_zero_do_nothing(tmp_path: Path) -> None:
    async with session(tmp_path, b"ab") as s:
        assert await s.press("backspace") == HexCursor(0, 0, "hex")
        await s.press("ctrl+end")
        assert await s.press("delete") == HexCursor(2, 0, "hex")
        assert s.data() == b"ab"
        assert s.document.history.modified is False
        assert s.bell.call_count == 0
        assert s.host.edited == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["delete", "backspace"])
@pytest.mark.parametrize("pane", ["hex", "char"])
async def test_a_selection_is_removed_by_delete_and_backspace(tmp_path: Path, key: str, pane: Pane) -> None:
    async with session(tmp_path, b"abcdef") as s:
        if pane == "hex":
            await s.press("right", "right", "right", "right")
        else:
            await s.press("tab", "right", "right")
        await s.press("shift+right", "shift+right")
        assert s.view.selection == ByteSelection(2, 4)
        assert await s.press(key) == HexCursor(2, 0, pane)
        assert s.data() == b"abef"
        assert s.view.selection is None


@pytest.mark.asyncio
async def test_a_backward_selection_is_removed_from_its_start(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcdef") as s:
        await s.press("tab", "ctrl+end")
        await s.press("shift+left", "shift+left")
        assert await s.press("backspace") == HexCursor(4, 0, "char")
        assert s.data() == b"abcd"


@pytest.mark.asyncio
async def test_a_deletion_shortens_the_document_and_marks_it_modified(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("delete")
        assert s.document.length == 3
        assert s.document.history.modified is True
        assert s.host.edited == 1
        assert row_texts(s.view)[0].startswith("00000000  62 63 64")


@pytest.mark.asyncio
async def test_single_byte_deletes_in_a_row_undo_together(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcdef") as s:
        await s.press("delete", "delete", "delete")
        assert s.data() == b"def"
        assert s.document.history.undo(s.document) is not None
        assert s.data() == b"abcdef"


# -- CRLF -------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [1, 2])
async def test_typing_over_half_of_a_crlf_is_recorded_exactly(tmp_path: Path, offset: int) -> None:
    original = b"a\r\nb"
    async with session(tmp_path, original) as s:
        await s.press("tab", *["right"] * offset)
        await s.press("X")
        assert s.data() != original
        assert s.document.history.undo(s.document) is not None
        assert s.data() == original
        assert s.document.history.redo(s.document) is not None
        assert s.document.history.undo(s.document) is not None
        assert s.data() == original


# -- read-only --------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_read_only_view_ignores_typing_and_deleting_silently(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd", read_only=True) as s:
        await s.press("1", "a", "delete", "backspace", "right", "backspace")
        await s.press("tab", "X", "ä", "enter", "delete")
        assert s.data() == b"abcd"
        assert s.bell.call_count == 0
        assert s.notify.call_count == 0
        assert s.document.history.modified is False
        assert s.host.edited == 0


@pytest.mark.asyncio
async def test_a_read_only_view_still_navigates_selects_and_toggles_insert(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd", read_only=True) as s:
        assert await s.press("right", "right") == HexCursor(1, 0, "hex")
        await s.press("shift+right")
        assert s.view.selection is not None
        await s.press("ctrl+a")
        assert s.view.selection is not None
        await s.press("insert")
        assert s.view.insert_mode is True
        assert s.host.modes == [True]


@pytest.mark.asyncio
async def test_read_only_is_looked_up_at_every_key(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        state = {"read_only": True}
        s.view.is_read_only = lambda: state["read_only"]
        await s.press("1")
        assert s.data() == b"abcd"
        state["read_only"] = False
        await s.press("1")
        assert s.data() == bytes([0x11]) + b"bcd"


# -- the edit lock ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("keys", [("1",), ("delete",), ("backspace",), ("tab", "X")])
async def test_a_locked_document_refuses_edits_with_bell_and_warning(tmp_path: Path, keys: tuple[str, ...]) -> None:
    async with session(tmp_path, b"abcd") as s:
        await s.press("right", "right", "right")  # byte 1, low digit: backspace and delete have something to remove
        s.document.lock_edits("saving")
        await s.press(*keys)
        assert s.data() == b"abcd"
        assert s.document.history.modified is False
        assert s.bell.call_count == 1
        assert s.notify.call_count == 1
        assert "saving" in str(s.notify.call_args)
        assert s.notify.call_args.kwargs["severity"] == "warning"
        assert s.host.edited == 0


@pytest.mark.asyncio
async def test_edits_work_again_after_the_lock_is_lifted(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        s.document.lock_edits("saving")
        await s.press("1")
        s.document.unlock_edits("saving")
        await s.press("1")
        assert s.data() == bytes([0x11]) + b"bcd"
        assert s.bell.call_count == 1


# -- beyond the scanned part ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_works_before_the_scan_has_reached_the_position(tmp_path: Path) -> None:
    data = bytes(range(256)) * 400
    with opened(write_file(tmp_path, data), autostart=False) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+end", "left", "left", "a", "b")
            await pilot.pause()
            assert document.read_bytes(len(data) - 1, 1) == bytes([0xAB])
            assert document.length == len(data)
