"""Go to a byte offset in both views and the hex view's Go to popup (S0003 ACT6 commit 4, REQ-18, DEC-25)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.message import Message
from textual.pilot import Pilot

from nova_editor.goto_popup import HEX_HINT, HEX_INVALID, HINT, INVALID, GotoTarget, hex_goto, parse_goto
from nova_editor.screen import EditorScreen
from nova_editor.widget import HexView
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_editor_screen_hex import WIDE, app_for, note_of, screen_of, toggle

SIZE = 9000
LINE_MESSAGE = "Line numbers are not available in the hex view; use 0x…, …h or @offset"


def beyond(value: int, length: int) -> str:
    return f"byte offset {value} is beyond the end of the file ({length} bytes)"


def make_file(tmp_path: Path, size: int = SIZE, name: str = "f.bin") -> Path:
    path = tmp_path / name
    path.write_bytes(bytes(index % 255 + 1 for index in range(size)))  # no NUL byte: the file is not detected as binary and starts in text
    return path


@pytest.mark.parametrize("text", ["0x1F00", "0X1f00", "1F00h", "1f00H", "@7936", " 0x1F00 "])
def test_parse_goto_forms(text: str) -> None:
    assert parse_goto(text) == GotoTarget("byte", 7936)


@pytest.mark.parametrize("text", ["0x", "h", "@0x10", "-1h", "0xZZ", "1F00hh", "abc", "", "-1", "0x-1"])
def test_parse_goto_refuses(text: str) -> None:
    assert parse_goto(text) is None


HUGE_HEX = "f" * 5000


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"0x{HUGE_HEX}", id="0x-5000-f"),
        pytest.param(f"{HUGE_HEX}h", id="5000-f-h"),
        pytest.param("0x1" + "0" * 16, id="0x-2**64"),
        pytest.param("1" + "0" * 16 + "h", id="h-2**64"),
        pytest.param("@" + "1" * 5000, id="at-5000-digits"),
    ],
)
def test_parse_goto_refuses_values_beyond_64_bits(text: str) -> None:
    assert parse_goto(text) is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("0x" + "f" * 16, id="0x-16-f"),
        pytest.param("f" * 16 + "h", id="16-f-h"),
        pytest.param("0x" + "0" * 4000 + "1F00", id="0x-leading-zeros"),
        pytest.param("0" * 4000 + "1F00h", id="h-leading-zeros"),
    ],
)
def test_parse_goto_accepts_64_bit_values_and_leading_zeros(text: str) -> None:
    target = parse_goto(text)
    assert target is not None
    assert target.value < 2**64


def test_parse_goto_at_digit_limit_stays_none_or_safe() -> None:
    assert parse_goto("@" + "9" * 4301) is None
    target = parse_goto("@" + "9" * 4300)
    if target is not None:
        assert isinstance(hex_goto(target, 10), str)


def test_hex_goto_message_survives_any_int() -> None:
    message = hex_goto(GotoTarget("byte", 10**5000), 10)
    assert isinstance(message, str)
    assert message.startswith("byte offset ")


def test_parse_goto_plain_digits_stay_a_line_number() -> None:
    assert parse_goto("120") == GotoTarget("line", 120)
    assert parse_goto("100") == GotoTarget("line", 100)
    assert parse_goto("0x10") == GotoTarget("byte", 16)


def test_the_texts_name_the_new_forms() -> None:
    assert "0x1F00" in INVALID
    assert "1F00h" in INVALID
    assert "@4096" in INVALID
    assert HEX_HINT == "Byte offset: 0x1F00, 1F00h or @7936"
    assert HEX_INVALID == "Not a byte offset (for example 0x1F00, 1F00h or @7936)"
    assert HINT


def test_hex_goto_helper() -> None:
    assert hex_goto(GotoTarget("line", 3), 100) == LINE_MESSAGE
    assert hex_goto(GotoTarget("byte", 0), 100) == 0
    assert hex_goto(GotoTarget("byte", 99), 100) == 99


def test_hex_goto_helper_accepts_the_length_as_the_end_position() -> None:
    assert hex_goto(GotoTarget("byte", 100), 100) == 100


def test_hex_goto_helper_rejects_past_the_length() -> None:
    assert hex_goto(GotoTarget("byte", 101), 100) == beyond(101, 100)
    assert hex_goto(GotoTarget("byte", 10**12), 100) == beyond(10**12, 100)


def test_hex_goto_helper_on_an_empty_document() -> None:
    assert hex_goto(GotoTarget("byte", 0), 0) == 0
    assert hex_goto(GotoTarget("byte", 1), 0) == beyond(1, 0)
    assert hex_goto(GotoTarget("line", 1), 0) == LINE_MESSAGE


async def goto(pilot: Pilot[None], screen: EditorScreen, text: str) -> None:
    if not screen.goto_popup.display:
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: screen.goto_popup.display)
    screen.goto_popup.input.value = text
    await pilot.press("enter")


def visible(view: HexView, offset: int) -> bool:
    top = view.scroll_offset.y
    return top <= offset // view.hex_layout.bytes_per_row < top + view.scrollable_content_region.height


@pytest.mark.asyncio
async def test_goto_opens_in_hex_mode_with_the_hex_hint(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: screen.goto_popup.display)
        popup = screen.goto_popup
        assert popup.shown == HEX_HINT
        assert popup.input.placeholder == "Byte offset"
        assert screen.focused is popup.input
        assert note_of(app) is None
        await pilot.press("escape")
        await toggle(pilot, screen)
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: popup.display)
        assert popup.shown == HINT


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["0x1F00", "1f00h", "@7936"])
async def test_three_forms_move_the_hex_cursor_to_byte_7936(tmp_path: Path, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        await goto(pilot, screen, text)
        await wait_until(pilot, lambda: not screen.goto_popup.display)
        await pilot.pause()
        assert view.cursor == HexCursor(7936, 0, "hex")
        assert visible(view, 7936)
        assert screen.focused is view


@pytest.mark.asyncio
async def test_goto_to_the_last_byte(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        await goto(pilot, screen, f"@{SIZE - 1}")
        await wait_until(pilot, lambda: not screen.goto_popup.display)
        await pilot.pause()
        assert view.cursor == HexCursor(SIZE - 1, 0, "hex")
        assert visible(view, SIZE - 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [f"@{SIZE}", f"0x{SIZE:X}", f"{SIZE:X}h"])
async def test_goto_to_the_length_puts_the_cursor_at_the_end_of_the_document(tmp_path: Path, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        await goto(pilot, screen, text)
        await wait_until(pilot, lambda: not screen.goto_popup.display)
        await pilot.pause()
        assert view.cursor == HexCursor(SIZE, 0, "hex")
        assert visible(view, SIZE)
        assert screen.focused is view


@pytest.mark.asyncio
async def test_the_text_view_still_rejects_the_length(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("a" * 99 + "\n")
    app = app_for(tmp_path, path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await goto(pilot, screen, "@100")
        await wait_until(pilot, lambda: screen.goto_popup.shown.startswith(beyond(100, 100)))
        assert screen.goto_popup.display
        assert screen.document.editor.cursor_location == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["@99999", f"0x{SIZE + 1:X}", f"@{SIZE + 1}"])
async def test_out_of_range_names_the_length_and_keeps_the_popup_open(tmp_path: Path, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        popup = screen.goto_popup
        await goto(pilot, screen, text)
        await wait_until(pilot, lambda: popup.shown.endswith(f"beyond the end of the file ({SIZE} bytes)"))
        assert popup.display
        assert popup.input.has_class("-invalid")
        assert screen.focused is popup.input
        assert popup.input.selected_text == text
        assert view.cursor == HexCursor(0, 0, "hex")
        await pilot.press("@", "1", "6", "enter")  # the text was selected: typing replaces it
        await wait_until(pilot, lambda: not popup.display)
        assert view.cursor == HexCursor(16, 0, "hex")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["30", "1"])
async def test_a_plain_number_in_the_hex_view_is_rejected_with_the_line_message(tmp_path: Path, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        popup = screen.goto_popup
        await goto(pilot, screen, text)
        await wait_until(pilot, lambda: popup.shown == LINE_MESSAGE)
        assert popup.display
        assert screen.document.hex_view.cursor == HexCursor(0, 0, "hex")


@pytest.mark.asyncio
async def test_invalid_text_shows_the_hex_invalid_message(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        popup = screen.goto_popup
        await goto(pilot, screen, "xyz")
        await wait_until(pilot, lambda: popup.shown == HEX_INVALID)
        assert popup.display


@pytest.mark.asyncio
async def test_text_view_hex_forms_behave_as_at_n(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("".join(f"line {i}\n" for i in range(1, 51)))
    app = app_for(tmp_path, path)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        places = []
        for text in ("@16", "0x10", "10h"):
            await goto(pilot, screen, text)
            await wait_until(pilot, lambda: not screen.goto_popup.display)
            places.append(editor.cursor_location)
            await pilot.press("ctrl+home")
        assert places[0] != (0, 0)
        assert places == [places[0]] * 3
        await goto(pilot, screen, "@99999")
        await wait_until(pilot, lambda: screen.goto_popup.shown.startswith("byte offset 99999 "))
        at_n = screen.goto_popup.shown
        await goto(pilot, screen, "0x1869F")  # the same offset, 99999
        await pilot.pause(0.1)
        assert screen.goto_popup.shown == at_n


@pytest.mark.asyncio
async def test_a_goto_moves_the_cursor_but_keeps_the_selection_model(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        view = screen.document.hex_view
        view.select_range(10, 20)
        await pilot.pause()
        assert view.selection is not None
        moved: list[Message] = []
        real = view.post_message

        def spy(message: Message) -> bool:
            if isinstance(message, HexView.Moved):
                moved.append(message)
            return real(message)

        with patch.object(view, "post_message", spy):
            await goto(pilot, screen, "@4000")
            await wait_until(pilot, lambda: not screen.goto_popup.display)
            await pilot.pause()
        assert view.selection is None
        assert view.cursor == HexCursor(4000, 0, "hex")
        assert len(moved) == 1


@pytest.mark.asyncio
async def test_escape_closes_the_goto_popup_in_hex_mode(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await toggle(pilot, screen)
        await pilot.press("ctrl+g")
        await wait_until(pilot, lambda: screen.goto_popup.display)
        await pilot.press("escape")
        await wait_until(pilot, lambda: not screen.goto_popup.display)
        assert screen.focused is screen.document.hex_view


@pytest.mark.asyncio
@pytest.mark.parametrize("hex_view", [True, False])
@pytest.mark.parametrize("text", [pytest.param(f"0x{HUGE_HEX}", id="0x-5000-f"), pytest.param(f"{HUGE_HEX}h", id="5000-f-h")])
async def test_a_huge_hex_offset_shows_the_invalid_message_and_the_app_survives(tmp_path: Path, hex_view: bool, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        if hex_view:
            await toggle(pilot, screen)
        popup = screen.goto_popup
        await goto(pilot, screen, text)
        await wait_until(pilot, lambda: popup.shown == (HEX_INVALID if hex_view else INVALID))
        assert popup.display
        assert app.is_running
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)


@pytest.mark.asyncio
@pytest.mark.parametrize("hex_view", [True, False])
async def test_a_huge_decimal_offset_stays_invalid_and_the_app_survives(tmp_path: Path, hex_view: bool) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        if hex_view:
            await toggle(pilot, screen)
        popup = screen.goto_popup
        await goto(pilot, screen, "@" + "9" * 4301)
        await wait_until(pilot, lambda: popup.shown == (HEX_INVALID if hex_view else INVALID))
        assert app.is_running
