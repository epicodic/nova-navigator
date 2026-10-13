"""The hex status line: pure formatting of `format_hex_status` and the live line of the screen in hex mode (S0003 ACT7 commit 3, REQ-19, REQ-21, DEC-27)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_editor.status_line import HEX_DROP_ORDER, HexStatusState, StatusLine, StatusState, format_hex_status, format_size, format_status
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.widget.hex_helpers import forbid, logged_reads, make_sparse

LENGTH = 66_000
"""64.5 KiB; offset 7936 is 12 % of it."""


def hex_state(**changes: object) -> HexStatusState:
    """The REQ-19 example with consistent numbers: byte 0x41 at offset 7936 of 66 000 bytes, overwrite, modified."""
    base = HexStatusState(file_name="data.bin", offset=7936, length=LENGTH, byte=0x41, insert=False, modified=True)
    return dataclasses.replace(base, **changes)


FULL = "data.bin  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  Modified  64.5 KiB  Hex"


def test_the_req19_format_with_consistent_numbers() -> None:
    assert format_hex_status(hex_state(), 200) == FULL
    assert len(FULL) == 78


def test_offset_digits_are_eight_and_grow_with_the_length() -> None:
    assert "Off 0x00000000 (0) 0%" in format_hex_status(hex_state(offset=0), 200)
    big = 0x1_0000_0000
    text = format_hex_status(hex_state(offset=big, length=big * 4), 300)
    assert "Off 0x100000000 (4294967296) 25%" in text
    text = format_hex_status(hex_state(offset=0x12, length=0xF_FFFF_FFFF), 300)
    assert "Off 0x000000012 (18) 0%" in text


def test_decimal_offset_has_no_separators() -> None:
    assert "(1234567)" in format_hex_status(hex_state(offset=1_234_567, length=2_000_000), 200)


def test_percent_is_left_out_for_an_empty_document() -> None:
    text = format_hex_status(hex_state(offset=0, length=0, byte=None, modified=False), 200)
    assert text == "data.bin  Off 0x00000000 (0)  EOF  OVR  0 B  Hex"


def test_percent_is_100_at_the_end() -> None:
    text = format_hex_status(hex_state(offset=LENGTH, byte=None), 200)
    assert "Off 0x000101D0 (66000) 100%  EOF  " in text


@pytest.mark.parametrize(
    ("byte", "shown"),
    [
        (0x00, "0x00 0 <NUL>"),
        (0x07, "0x07 7 <BEL>"),
        (0x0A, "0x0A 10 <LF>"),
        (0x0D, "0x0D 13 <CR>"),
        (0x1B, "0x1B 27 <ESC>"),
        (0x1F, "0x1F 31 <US>"),
        (0x20, "0x20 32 ' '"),
        (0x41, "0x41 65 'A'"),
        (0x7E, "0x7E 126 '~'"),
        (0x7F, "0x7F 127 <DEL>"),
        (0x80, "0x80 128 '.'"),
        (0x9B, "0x9B 155 '.'"),
        (0xFF, "0xFF 255 '.'"),
    ],
)
def test_byte_value_parts(byte: int, shown: str) -> None:
    assert f"12%  {shown}  OVR" in format_hex_status(hex_state(byte=byte), 200)


def test_a_control_byte_drops_whole_two_columns_later() -> None:
    full = "data.bin  Off 0x00001F00 (7936) 12%  0x1B 27 <ESC>  OVR  Modified  64.5 KiB  Hex"
    without_byte = "data.bin  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"
    assert len(full) == 80
    assert len(without_byte) == 65
    state = hex_state(byte=0x1B)
    assert format_hex_status(state, 80) == full
    assert format_hex_status(state, 79) == without_byte
    assert format_hex_status(state, 65) == without_byte
    assert format_hex_status(state, 64) == "data.bin  OVR  Modified  64.5 KiB  Hex"


def test_eof_and_unreadable_byte_are_not_named() -> None:
    assert "EOF" in format_hex_status(hex_state(offset=LENGTH, byte=None), 200)
    assert "12%  ?  OVR" in format_hex_status(hex_state(byte=None), 200)


def test_eof_at_the_end_of_the_document() -> None:
    assert "100%  EOF  OVR" in format_hex_status(hex_state(offset=LENGTH, byte=None), 200)


def test_unreadable_byte_shows_a_question_mark() -> None:
    assert "12%  ?  OVR" in format_hex_status(hex_state(byte=None), 200)


def test_ovr_and_ins() -> None:
    assert "  OVR  " in format_hex_status(hex_state(insert=False), 200)
    text = format_hex_status(hex_state(insert=True), 200)
    assert "  INS  " in text
    assert "OVR" not in text


def test_modified_only_when_modified() -> None:
    assert "Modified" in format_hex_status(hex_state(modified=True), 200)
    text = format_hex_status(hex_state(modified=False), 200)
    assert text == "data.bin  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  64.5 KiB  Hex"


def test_unsaved_name() -> None:
    assert format_hex_status(hex_state(file_name=None), 200).startswith("(unsaved)  Off ")


def test_a_long_name_keeps_its_end() -> None:
    text = format_hex_status(hex_state(file_name="a_very_long_directory_free_name.bin"), 40)
    assert text == "…ctory_free_name.bin  OVR  Modified"


def test_note_follows_the_name() -> None:
    text = format_hex_status(hex_state(note="Saved  data.bin  64.5 KiB"), 200)
    assert text == "data.bin  Saved  data.bin  64.5 KiB  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  Modified  64.5 KiB  Hex"


# DEC-27: at a narrow width the position parts (byte value first, then Off) are dropped first; the name and the note keep their place, the rest is cut tail-first (Hex first).

WIDTH_TABLE = [
    (200, FULL),
    (78, FULL),
    (77, "data.bin  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"),
    (70, "data.bin  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"),
    (65, "data.bin  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"),
    (64, "data.bin  OVR  Modified  64.5 KiB  Hex"),
    (60, "data.bin  OVR  Modified  64.5 KiB  Hex"),
    (50, "data.bin  OVR  Modified  64.5 KiB  Hex"),
    (40, "data.bin  OVR  Modified  64.5 KiB  Hex"),
    (38, "data.bin  OVR  Modified  64.5 KiB  Hex"),
    (37, "data.bin  OVR  Modified  64.5 KiB"),
    (33, "data.bin  OVR  Modified  64.5 KiB"),
    (32, "data.bin  OVR  Modified"),
    (30, "data.bin  OVR  Modified"),
    (23, "data.bin  OVR  Modified"),
    (22, "data.bin  OVR"),
    (20, "data.bin  OVR"),
    (16, "data.bin  OVR"),
    (14, "…ta.bin  OVR"),
]


def test_the_drop_order_is_explicit() -> None:
    assert HEX_DROP_ORDER == ("byte", "offset", "hex", "size", "modified", "mode")


@pytest.mark.parametrize(("width", "expected"), WIDTH_TABLE)
def test_narrow_widths_drop_the_position_parts_first(width: int, expected: str) -> None:
    text = format_hex_status(hex_state(), width)
    assert text == expected
    assert len(text) <= width or text == "data.bin"


LONG_NOTE = "x" * 50

NOTE_TABLE = [
    (200, f"data.bin  {LONG_NOTE}  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  Modified  64.5 KiB  Hex"),
    (117, f"data.bin  {LONG_NOTE}  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"),
    (116, f"data.bin  {LONG_NOTE}  OVR  Modified  64.5 KiB  Hex"),
    (90, f"data.bin  {LONG_NOTE}  OVR  Modified  64.5 KiB  Hex"),
    (89, f"data.bin  {LONG_NOTE}  OVR  Modified  64.5 KiB"),
    (80, f"data.bin  {LONG_NOTE}  OVR  Modified"),
    (70, f"data.bin  {LONG_NOTE}  OVR"),
    (60, f"data.bin  {LONG_NOTE}"),
    (40, "data.bin  " + "x" * 29 + "…"),
    (30, "data.bin  " + "x" * 19 + "…"),
    (20, "data.bin  " + "x" * 9 + "…"),
]


@pytest.mark.parametrize(("width", "expected"), NOTE_TABLE)
def test_a_long_note_is_cut_not_dropped(width: int, expected: str) -> None:
    text = format_hex_status(hex_state(note=LONG_NOTE), width)
    assert text == expected
    assert len(text) <= width


def test_a_note_keeps_its_place_while_position_parts_go_first() -> None:
    note = "Saved  data.bin  64.5 KiB"
    head = "data.bin  Saved  data.bin  64.5 KiB"
    assert format_hex_status(hex_state(note=note), 92) == f"{head}  Off 0x00001F00 (7936) 12%  OVR  Modified  64.5 KiB  Hex"
    assert format_hex_status(hex_state(note=note), 91) == f"{head}  OVR  Modified  64.5 KiB  Hex"
    assert format_hex_status(hex_state(note=note), 65) == f"{head}  OVR  Modified  64.5 KiB  Hex"
    assert format_hex_status(hex_state(note=note), 64) == f"{head}  OVR  Modified  64.5 KiB"
    assert format_hex_status(hex_state(note=note), 50) == f"{head}  OVR  Modified"
    assert format_hex_status(hex_state(note=note), 40) == f"{head}  OVR"


def test_format_size_units() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(66_000) == "64.5 KiB"
    assert format_size(1024**2) == "1.0 MiB"
    assert format_size(3 * 1024**3 // 2) == "1.5 GiB"
    assert format_size(64 * 1024**3) == "64.0 GiB"
    assert format_size(2 * 1024**4) == "2.0 TiB"
    assert format_size(2048 * 1024**4) == "2048.0 TiB"


# The live line of the screen

SIZE = (100, 24)


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No poll during a test."""
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


def make_binary(tmp_path: Path, length: int = LENGTH) -> Path:
    """A binary file whose byte 7936 is `A` and whose byte 7937 is `B`."""
    data = bytearray(length)
    data[7936] = 0x41
    data[7937] = 0x42
    path = tmp_path / "data.bin"
    path.write_bytes(bytes(data))
    return path


def screen_of(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


def status_text(app: NovaEditApp) -> str:
    return app.query_one(StatusLine).text


@pytest.mark.asyncio
async def test_the_status_line_shows_the_hex_parts(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00000000 (0) 0%  0x00 0 <NUL>  OVR  64.5 KiB  Hex")
        screen.document.hex_view.set_cursor(7936)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  64.5 KiB  Hex")


@pytest.mark.asyncio
async def test_the_live_status_line_names_a_bel_byte(tmp_path: Path) -> None:
    path = make_binary(tmp_path)
    data = bytearray(path.read_bytes())
    data[7936] = 0x07
    path.write_bytes(bytes(data))
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen_of(app).document.hex_view.set_cursor(7936)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F00 (7936) 12%  0x07 7 <BEL>  OVR  64.5 KiB  Hex")


@pytest.mark.asyncio
async def test_status_follows_cursor_moves_and_the_end_position(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = screen_of(app).document.hex_view
        view.set_cursor(7937)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F01 (7937) 12%  0x42 66 'B'  OVR  64.5 KiB  Hex")
        await pilot.press("right", "right")  # a nibble each: the high digit, then the next byte
        await wait_until(pilot, lambda: "Off 0x00001F02 (7938) 12%  0x00 0 <NUL>" in status_text(app))
        view.set_cursor(LENGTH)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x000101D0 (66000) 100%  EOF  OVR  64.5 KiB  Hex")


@pytest.mark.asyncio
async def test_status_follows_an_overwrite_and_undo_clears_modified(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        view = screen.document.hex_view
        view.set_cursor(7936)
        await wait_until(pilot, lambda: "0x41 65 'A'" in status_text(app))
        await pilot.press("5", "0")
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F01 (7937) 12%  0x42 66 'B'  OVR  Modified  64.5 KiB  Hex")
        view.set_cursor(7936)
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F00 (7936) 12%  0x50 80 'P'  OVR  Modified  64.5 KiB  Hex")
        await pilot.press("ctrl+z")
        await wait_until(pilot, lambda: status_text(app) == "data.bin  Off 0x00001F00 (7936) 12%  0x41 65 'A'  OVR  64.5 KiB  Hex")


@pytest.mark.asyncio
async def test_insert_key_shows_ins(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("insert")
        await wait_until(pilot, lambda: "  INS  " in status_text(app))
        await pilot.press("insert")
        await wait_until(pilot, lambda: "  OVR  " in status_text(app))


@pytest.mark.asyncio
async def test_a_switch_updates_the_status_both_ways(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0"))
        await pilot.press("f4")
        await wait_until(pilot, lambda: status_text(app) == "f.txt  Off 0x00000000 (0) 0%  0x6F 111 'o'  OVR  8 B  Hex")
        await pilot.press("f4")
        await wait_until(pilot, lambda: status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0"))


@pytest.mark.asyncio
async def test_the_text_status_line_is_unchanged_back_in_text(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await pilot.press("f4")
        await wait_until(pilot, lambda: "Hex" in status_text(app))
        await pilot.press("f4")
        await wait_until(pilot, lambda: not screen.document.hex_mode and "Ln 1" in status_text(app))
        assert status_text(app).startswith("f.txt  Ln 1  Col 1  Byte 0")
        state = screen._status_state()
        assert isinstance(state, StatusState)
        assert status_text(app) == format_status(state, app.query_one(StatusLine).size.width)


@pytest.mark.asyncio
async def test_a_note_shows_after_the_name_in_hex(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: status_text(app).startswith("data.bin  No changes to save  Off 0x00000000 (0) 0%  "))
        screen_of(app).document.hex_view.set_cursor(7936)
        await pilot.press("4", "1")
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: status_text(app).startswith("data.bin  Saved  data.bin  "))
        assert "  Off 0x00001F01 (7937) 12%  0x42 66 'B'  OVR  64.5 KiB  Hex" in status_text(app)


@pytest.mark.asyncio
async def test_the_hex_status_fits_80_columns(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen_of(app).document.hex_view.set_cursor(7936)
        await wait_until(pilot, lambda: status_text(app) == FULL.replace("  Modified", ""))
        assert len(status_text(app)) <= 80
        await pilot.press("4", "1")
        await wait_until(pilot, lambda: "Modified" in status_text(app))
        assert len(status_text(app)) <= 80


@pytest.mark.asyncio
async def test_a_hex_status_refresh_calls_no_row_api(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_binary(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.text.endswith("  Hex"))
        document = editor.document
        forbid(document)
        with patch.object(editor, "peek_cursor_state", side_effect=AssertionError("peek_cursor_state")):
            flushes = status.flushes
            screen.document.hex_view.set_cursor(7936)
            await wait_until(pilot, lambda: status.flushes > flushes and "0x41 65 'A'" in status.text)


@pytest.mark.asyncio
async def test_a_hex_status_refresh_reads_at_most_one_window_on_a_sparse_8_gib_file(tmp_path: Path) -> None:
    path = make_sparse(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.text.endswith("  Hex"))
        document = screen.document.editor.document
        view = screen.document.hex_view
        with logged_reads(document) as log:
            flushes = status.flushes
            view.set_cursor(0x1_2345_6789)
            await wait_until(pilot, lambda: status.flushes > flushes and "Off 0x123456789 (4886718345)" in status.text)
            await pilot.pause(0.2)
        assert len(log.calls) <= 1, log.calls
        assert status.text.startswith("sparse.bin  Off 0x123456789 (4886718345) 56%  0x00 0 <NUL>  OVR  8.0 GiB  Hex")
