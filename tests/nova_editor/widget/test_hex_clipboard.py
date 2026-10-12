"""Copy, cut and paste in the hex view (S0003 ACT5 commit 5, REQ-15, REQ-16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.widget import NovaTextArea
from tests.nova_editor.save_widget_helpers import SETTINGS
from tests.nova_editor.test_editor_screen_hex import app_for, make_file, screen_of, toggle
from tests.nova_editor.widget.test_hex_edit import Session, session

WIDE = (160, 24)


def select(s: Session, start: int, end: int, pane: str = "hex") -> None:
    """Select `[start, end)` in `pane` (the cursor ends at `end`)."""
    s.view.set_cursor(start, pane="hex" if pane == "hex" else "char")
    s.view.set_cursor(end, select=True)


# -- copy (REQ-15) ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_in_the_hex_pane_writes_upper_case_pairs(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01])) as s:
        select(s, 0, 4)
        await s.press("ctrl+c")
        assert s.host.clipboard == "DE AD BE EF"
        assert s.data() == bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01])
        assert s.document.history.modified is False


@pytest.mark.asyncio
async def test_copy_in_the_character_pane_writes_the_rendering(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x41, 0x00, 0x42, 0x43])) as s:
        select(s, 0, 3, "char")
        await s.press("ctrl+c")
        assert s.host.clipboard == "A.B"


@pytest.mark.asyncio
async def test_copy_without_a_selection_does_nothing(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        s.host.copy_to_clipboard("keep")
        await s.press("ctrl+c")
        assert s.host.clipboard == "keep"
        assert s.bell.call_count == 0
        assert s.notify.call_count == 0


@pytest.mark.asyncio
async def test_copy_works_when_read_only(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02\x03", read_only=True) as s:
        select(s, 0, 2)
        await s.press("ctrl+c")
        assert s.host.clipboard == "01 02"


@pytest.mark.asyncio
async def test_above_the_cap_the_system_clipboard_is_left_alone_and_the_internal_paste_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "clipboard_cap", 5)
    async with session(tmp_path, bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00])) as s:
        s.host.copy_to_clipboard("old")
        select(s, 0, 4)  # the hex text is 11 characters: above the cap
        await s.press("ctrl+c")
        assert s.host.clipboard == "old"
        assert s.notify.call_count == 1
        assert "too large for the system clipboard" in str(s.notify.call_args)
        assert s.notify.call_args.kwargs["severity"] == "warning"
        s.view.set_cursor(5)
        await s.press("ctrl+v")
        assert s.data() == bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0xDE, 0xAD, 0xBE, 0xEF])


@pytest.mark.asyncio
async def test_the_cap_is_measured_on_the_clipboard_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "clipboard_cap", 4)
    async with session(tmp_path, bytes([0x41, 0x42, 0x43, 0x44, 0x45])) as s:
        select(s, 0, 4, "char")  # four characters fit
        await s.press("ctrl+c")
        assert s.host.clipboard == "ABCD"
        assert s.notify.call_count == 0


# -- internal paste keeps the exact bytes ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_paste_in_the_character_pane_keeps_the_bytes_not_the_dots(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x41, 0x00, 0x42])) as s:
        select(s, 0, 3, "char")
        await s.press("ctrl+c")
        assert s.host.clipboard == "A.B"
        s.view.set_cursor(3, pane="char")
        await s.press("ctrl+v")
        assert s.data() == bytes([0x41, 0x00, 0x42, 0x41, 0x00, 0x42])


@pytest.mark.asyncio
async def test_internal_paste_in_the_hex_pane_keeps_the_bytes(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0xFF, 0xFE, 0x00])) as s:
        select(s, 0, 2)
        await s.press("ctrl+c")
        s.view.set_cursor(3)
        await s.press("ctrl+v")
        assert s.data() == bytes([0xFF, 0xFE, 0x00, 0xFF, 0xFE])


@pytest.mark.asyncio
async def test_a_changed_application_clipboard_ends_the_internal_paste(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0x01, 0x02])) as s:
        select(s, 0, 1)
        await s.press("ctrl+c")
        s.host.copy_to_clipboard("0A")
        s.view.set_cursor(2)
        await s.press("ctrl+v")
        assert s.data() == bytes([0x01, 0x02, 0x0A])


# -- paste parsing (REQ-16) -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("de ad be ef", b"\xde\xad\xbe\xef"),
        ("DEADBEEF", b"\xde\xad\xbe\xef"),
        ("0xDE 0xAD", b"\xde\xad"),
        ("0Xde\n\t01", b"\xde\x01"),
        ("d e", b"\xde"),
    ],
)
async def test_hex_pane_paste_parses_hex_text(tmp_path: Path, text: str, expected: bytes) -> None:
    async with session(tmp_path, b"\x00\x00\x00\x00\x00\x00") as s:
        s.host.copy_to_clipboard(text)
        await s.press("insert", "ctrl+v")
        assert s.data() == expected + bytes(6)
        assert s.view.cursor.offset == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Nothing to paste: the clipboard is empty."),
        (" \n\t", "Nothing to paste: the clipboard is empty."),
        ("de zz", 'Cannot paste into the hex pane: "z" at position 4 of the clipboard text is not a hex digit.'),
        ("de,ad", 'Cannot paste into the hex pane: "," at position 3 of the clipboard text is not a hex digit.'),
        ("dea", "Cannot paste into the hex pane: the clipboard text holds an odd number of hex digits (3)."),
        ("de 0x", 'Cannot paste into the hex pane: "0x" without digits at position 4.'),
    ],
)
async def test_hex_pane_paste_errors_name_the_problem_and_change_nothing(tmp_path: Path, text: str, message: str) -> None:
    async with session(tmp_path, b"abcd") as s:
        s.host.copy_to_clipboard(text)
        await s.press("ctrl+v")
        assert s.data() == b"abcd"
        assert s.document.history.modified is False
        assert not s.document.history.undo_stack
        assert s.bell.call_count == 1
        assert s.notify.call_count == 1
        assert message in str(s.notify.call_args)
        assert s.notify.call_args.kwargs["severity"] == "warning"
        assert s.host.edited == 0


@pytest.mark.asyncio
async def test_character_pane_paste_encodes_utf8_and_leaves_line_endings_alone(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x00\x00") as s:
        s.host.copy_to_clipboard("é\r\n")
        await s.press("tab", "insert", "ctrl+v")
        assert s.data() == "é\r\n".encode() + b"\x00\x00"
        assert s.view.cursor.offset == 4


@pytest.mark.asyncio
async def test_character_pane_paste_of_an_empty_clipboard_is_an_error(tmp_path: Path) -> None:
    async with session(tmp_path, b"ab") as s:
        s.host.copy_to_clipboard("")
        await s.press("tab", "ctrl+v")
        assert s.data() == b"ab"
        assert s.bell.call_count == 1
        assert "Nothing to paste: the clipboard is empty." in str(s.notify.call_args)


@pytest.mark.asyncio
async def test_character_pane_paste_of_an_unencodable_text_is_an_error(tmp_path: Path) -> None:
    async with session(tmp_path, b"ab") as s:
        s.host._clipboard = "a\ud800"  # the terminal path would refuse to encode it
        await s.press("tab", "ctrl+v")
        assert s.data() == b"ab"
        assert s.bell.call_count == 1
        assert "Cannot paste into the character pane: the clipboard text cannot be encoded as UTF-8." in str(s.notify.call_args)


# -- where the paste lands --------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overwrite_paste_replaces_and_extends_past_the_end(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02\x03\x04") as s:
        s.host.copy_to_clipboard("aa bb cc")
        s.view.set_cursor(3)
        await s.press("ctrl+v")
        assert s.data() == b"\x01\x02\x03\xaa\xbb\xcc"
        assert s.view.cursor.offset == 6


@pytest.mark.asyncio
async def test_overwrite_paste_replaces_only_what_it_covers(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02\x03\x04") as s:
        s.host.copy_to_clipboard("aa bb")
        s.view.set_cursor(1)
        await s.press("ctrl+v")
        assert s.data() == b"\x01\xaa\xbb\x04"


@pytest.mark.asyncio
async def test_insert_paste_inserts(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02") as s:
        s.host.copy_to_clipboard("aa bb")
        s.view.set_cursor(1)
        await s.press("insert", "ctrl+v")
        assert s.data() == b"\x01\xaa\xbb\x02"
        assert s.view.cursor.offset == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("keys", [(), ("insert",)], ids=["overwrite", "insert"])
async def test_paste_over_a_selection_replaces_the_selection_in_both_modes(tmp_path: Path, keys: tuple[str, ...]) -> None:
    async with session(tmp_path, b"\x01\x02\x03\x04\x05") as s:
        s.host.copy_to_clipboard("aa")
        select(s, 1, 4)
        await s.press(*keys, "ctrl+v")
        assert s.data() == b"\x01\xaa\x05"
        assert s.view.selection is None
        assert s.view.cursor.offset == 2


@pytest.mark.asyncio
async def test_a_paste_is_one_undo_step(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02\x03\x04") as s:
        s.host.copy_to_clipboard("aa bb cc dd ee")
        await s.press("ctrl+v")
        assert len(s.document.history.undo_stack) == 1
        await s.press("ctrl+z")
        assert s.data() == b"\x01\x02\x03\x04"
        await s.press("ctrl+y")
        assert s.data() == bytes.fromhex("aabbccddee")


@pytest.mark.asyncio
async def test_pasting_twice_makes_two_undo_steps(tmp_path: Path) -> None:
    async with session(tmp_path, b"\x01\x02") as s:
        s.host.copy_to_clipboard("aa")
        await s.press("insert", "ctrl+v", "ctrl+v")
        assert s.data() == b"\xaa\xaa\x01\x02"
        await s.press("ctrl+z")
        assert s.data() == b"\xaa\x01\x02"


# -- cut --------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cut_removes_the_selection_as_one_entry_and_copies_it(tmp_path: Path) -> None:
    async with session(tmp_path, bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01])) as s:
        select(s, 1, 4)
        await s.press("ctrl+x")
        assert s.data() == bytes([0xDE, 0x01])
        assert s.host.clipboard == "AD BE EF"
        assert len(s.document.history.undo_stack) == 1
        assert s.view.cursor.offset == 1
        s.view.set_cursor(2)
        await s.press("insert", "ctrl+v")
        assert s.data() == bytes([0xDE, 0x01, 0xAD, 0xBE, 0xEF])
        await s.press("ctrl+z", "ctrl+z")
        assert s.data() == bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01])


@pytest.mark.asyncio
async def test_cut_without_a_selection_does_nothing(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        s.host.copy_to_clipboard("keep")
        await s.press("ctrl+x")
        assert s.data() == b"abcd"
        assert s.host.clipboard == "keep"
        assert s.bell.call_count == 0


@pytest.mark.asyncio
async def test_cut_and_paste_do_nothing_when_read_only(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd", read_only=True) as s:
        s.host.copy_to_clipboard("0A")
        select(s, 0, 2)
        await s.press("ctrl+x", "ctrl+v")
        assert s.data() == b"abcd"
        assert s.host.clipboard == "0A"
        assert s.bell.call_count == 0
        assert s.notify.call_count == 0


@pytest.mark.asyncio
async def test_a_refused_cut_copies_nothing_and_a_refused_paste_changes_nothing(tmp_path: Path) -> None:
    async with session(tmp_path, b"abcd") as s:
        s.host.copy_to_clipboard("0A")
        select(s, 0, 2)
        s.document.lock_edits("saving")
        await s.press("ctrl+x")
        assert s.host.clipboard == "0A"
        assert s.data() == b"abcd"
        assert s.bell.call_count == 1
        assert "saving" in str(s.notify.call_args)
        await s.press("ctrl+v")
        assert s.data() == b"abcd"
        assert s.bell.call_count == 2


# -- the clipboard is shared with the text view ------------------------------------------------------------------------------


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


@pytest.mark.asyncio
@pytest.mark.usefixtures("quiet")
async def test_a_copy_in_the_text_view_pastes_into_the_hex_view_and_back(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, "abc\ndef\n"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await pilot.press("shift+right", "shift+right", "ctrl+c")  # "ab"
        await toggle(pilot, screen)
        view = screen.document.hex_view
        view.set_cursor(8)
        await pilot.press("ctrl+v")
        assert editor.document.read_bytes(0, editor.document.length) == b"abc\ndef\nab"
        view.set_cursor(0)
        view.set_cursor(3, select=True)
        await pilot.press("ctrl+c")
        assert app.clipboard == "61 62 63"
        await toggle(pilot, screen)
        editor.move_cursor((0, 0))
        await pilot.press("ctrl+v")
        assert editor.document.read_bytes(0, 3) == b"abc"  # the exact bytes, not the text "61 62 63"
        assert editor.document.length == len(b"abc\ndef\nab") + 3


@pytest.mark.asyncio
@pytest.mark.usefixtures("quiet")
async def test_reloading_the_text_clears_the_internal_record(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, "abcd"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await toggle(pilot, screen)
        view = screen.document.hex_view
        view.set_cursor(0)
        view.set_cursor(2, select=True)
        await pilot.press("ctrl+c")
        assert editor._clipboard_record is not None
        editor.load_text("zzzz")
        assert editor._clipboard_record is None
        view.set_cursor(4)
        await pilot.press("ctrl+v")  # falls back to the system text "61 62"
        assert editor.document.read_bytes(0, editor.document.length) == b"zzzzab"
