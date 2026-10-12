"""Pilot tests of the hex view: rendering, scrolling, resizing, following the document (S0003 ACT4 task 2) and the cursor,
selection, navigation keys and mouse (task 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.style import Style
from textual import events
from textual.pilot import Pilot
from textual.strip import Strip

from nova_editor.core import Content
from nova_editor.document import LazyDocument
from nova_editor.widget import HexView
from nova_editor.widget.hex_layout import format_row
from nova_editor.widget.hex_view import ByteSelection, HexCursor
from tests.nova_editor.widget.hex_helpers import HexHost, logged_reads, opened, row_texts, write_file

REQ5_SAMPLE = "00000000  7F 45 4C 46 02 01 01 00  00 00 00 00 00 00 00 00  |.ELF............|"
ELF_HEAD = bytes.fromhex("7f454c46020101000000000000000000")
EMPTY = Content.from_pieces((), 0)


def cell_style(strip: Strip, x: int) -> Style | None:
    """Return the style of cell `x` of `strip`."""
    start = 0
    for segment in strip:
        end = start + len(segment.text)
        if start <= x < end:
            return segment.style
        start = end
    raise IndexError(x)


@pytest.mark.asyncio
async def test_80_columns_show_the_req5_sample_row(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, ELF_HEAD + bytes(40))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert view.hex_layout.bytes_per_row == 16
            assert row_texts(view)[0] == REQ5_SAMPLE


@pytest.mark.asyncio
@pytest.mark.parametrize(("columns", "bytes_per_row"), [(146, 32), (80, 16), (30, 8)])
async def test_wider_and_narrower_terminals_change_the_byte_count(tmp_path: Path, columns: int, bytes_per_row: int) -> None:
    data = bytes(range(256))
    with opened(write_file(tmp_path, data)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(columns, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            width = view.scrollable_content_region.width
            assert layout.bytes_per_row == bytes_per_row
            first = row_texts(view)[0]
            assert len(first) == width
            assert first == format_row(layout, 0, data[:bytes_per_row]).ljust(width)[:width]


@pytest.mark.asyncio
async def test_empty_document_shows_one_row_with_only_the_offset(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"")) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            texts = row_texts(view)
            assert texts[0].rstrip() == "00000000"
            assert texts[1].strip() == ""
            assert view.virtual_size.height == 1


@pytest.mark.asyncio
async def test_a_full_last_row_is_followed_by_the_end_row(tmp_path: Path) -> None:
    data = bytes(range(32))
    with opened(write_file(tmp_path, data)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            texts = row_texts(view)
            assert view.virtual_size.height == 3
            assert texts[1] == format_row(view.hex_layout, 16, data[16:])
            assert texts[2].rstrip() == "00000020"
            assert texts[3].strip() == ""


@pytest.mark.asyncio
async def test_a_short_last_row_pads_the_missing_bytes(tmp_path: Path) -> None:
    data = bytes(range(0x41, 0x41 + 17))
    with opened(write_file(tmp_path, data)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            texts = row_texts(view)
            assert view.virtual_size.height == 2
            assert texts[1] == "00000010  51" + " " * 46 + "  |Q|" + " " * 15
            assert texts[2].strip() == ""


@pytest.mark.asyncio
async def test_character_column_follows_req6(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes.fromhex("001f20417e7f80ff"))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert row_texts(view)[0].endswith("|.. A~...|" + " " * 8)


@pytest.mark.asyncio
async def test_the_cursor_is_drawn_on_the_high_digit_and_marked_in_the_char_pane(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(32))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            strip = view.render_line(0)
            cursor = cell_style(strip, layout.digit_x(0, 0))
            mark = cell_style(strip, layout.char_cell_x(0))
            plain_digit = cell_style(strip, layout.digit_x(1, 0))
            plain_char = cell_style(strip, layout.char_cell_x(1))
            assert cursor != plain_digit
            assert mark != plain_char
            assert cursor != mark
            assert cell_style(strip, layout.digit_x(0, 1)) == mark  # the other digit of the cursor byte


@pytest.mark.asyncio
async def test_scroll_down_shows_the_next_rows(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.scroll_to(y=10, animate=False, immediate=True)
            await pilot.pause()
            assert row_texts(view)[0].startswith("000000A0  ")


@pytest.mark.asyncio
async def test_resize_keeps_the_top_offset(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.scroll_to(y=10, animate=False, immediate=True)
            await pilot.pause()
            await pilot.resize_terminal(146, 24)
            await pilot.pause()
            assert view.hex_layout.bytes_per_row == 32
            assert view.scroll_offset.y == 5
            assert row_texts(view)[0].startswith("000000A0  ")


@pytest.mark.asyncio
async def test_a_splice_from_outside_is_drawn(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(32))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert row_texts(view)[0].startswith("00000000  00 00")
            document.splice_bytes(0, 0, document._table.add(b"AB"))
            await pilot.pause()
            assert row_texts(view)[0].startswith("00000000  41 42 00")
            assert view.length == 34
            assert view.virtual_size.height == 3


@pytest.mark.asyncio
async def test_a_shrinking_splice_clamps_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.cursor = HexCursor(60, 1, "hex")
            document.splice_bytes(0, 32, EMPTY)
            view.follow_document()
            await pilot.pause()
            assert view.cursor.offset == 32
            assert view.virtual_size.height == 3


@pytest.mark.asyncio
async def test_a_replaced_document_is_followed(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"a" * 64, "a.bin")) as first, opened(write_file(tmp_path, b"b" * 8, "b.bin")) as second:
        current: list[LazyDocument] = [first]
        view = HexView(lambda: current[0])
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.cursor = HexCursor(40, 1, "char")
            assert row_texts(view)[0].startswith("00000000  61 61")
            current[0] = second
            view.follow_document()
            await pilot.pause()
            assert row_texts(view)[0] == format_row(view.hex_layout, 0, b"b" * 8)
            assert view.cursor == HexCursor(8, 0, "char")
            assert view.length == 8


@pytest.mark.asyncio
async def test_a_shortened_file_clamps_the_scroll_and_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600), "long.bin")) as first, opened(write_file(tmp_path, bytes(100), "short.bin")) as second:
        current: list[LazyDocument] = [first]
        view = HexView(lambda: current[0])
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.scroll_to(y=view.max_scroll_y, animate=False, immediate=True)
            view.cursor = HexCursor(1500, 0, "hex")
            await pilot.pause()
            assert view.scroll_offset.y > 0
            current[0] = second
            view.follow_document()
            await pilot.pause()
            assert view.scroll_offset.y == 0
            assert view.cursor.offset == 100
            texts = row_texts(view)
            assert texts[6] == format_row(view.hex_layout, 0x60, bytes(4))
            assert texts[7].strip() == ""


# -- task 3: cursor, selection, navigation keys, mouse ------------------------------------------------------------------


async def press_cursor(pilot: Pilot[None], view: HexView, *keys: str) -> HexCursor:
    """Press `keys` and return the cursor afterwards."""
    await pilot.press(*keys)
    await pilot.pause()
    return view.cursor


async def click_at(pilot: Pilot[None], view: HexView, x: int, y: int, *, shift: bool = False) -> None:
    """Hover over and click the cell `(x, y)` of `view`."""
    await pilot.hover(view, offset=(x, y))
    await pilot.click(view, offset=(x, y), shift=shift)
    await pilot.pause()


@pytest.mark.asyncio
async def test_the_cursor_starts_at_zero_in_the_hex_pane_high_digit(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert view.cursor == HexCursor(0, 0, "hex")
            assert view.selection is None


@pytest.mark.asyncio
async def test_rendering_marks_the_high_digit_and_the_same_byte_in_the_char_pane(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(5)
            await pilot.pause()
            layout = view.hex_layout
            strip = view.render_line(0)
            assert cell_style(strip, layout.digit_x(5, 0)) == view.get_component_rich_style("hex-view--cursor")
            assert cell_style(strip, layout.char_cell_x(5)) == view.get_component_rich_style("hex-view--mark")
            assert cell_style(strip, layout.digit_x(0, 0)) != view.get_component_rich_style("hex-view--cursor")


@pytest.mark.asyncio
async def test_the_low_digit_is_drawn_after_right(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, "right") == HexCursor(0, 1, "hex")
            layout = view.hex_layout
            strip = view.render_line(0)
            assert cell_style(strip, layout.digit_x(0, 1)) == view.get_component_rich_style("hex-view--cursor")
            assert cell_style(strip, layout.digit_x(0, 0)) == view.get_component_rich_style("hex-view--mark")


@pytest.mark.asyncio
async def test_tab_switches_the_pane_and_focus_stays(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert host.focused is view
            await press_cursor(pilot, view, "right")
            assert await press_cursor(pilot, view, "tab") == HexCursor(0, 0, "char")
            assert host.focused is view
            assert await press_cursor(pilot, view, "shift+tab") == HexCursor(0, 0, "hex")
            assert host.focused is view


@pytest.mark.asyncio
async def test_right_steps_digit_by_digit_across_bytes(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, "right") == HexCursor(0, 1, "hex")
            assert await press_cursor(pilot, view, "right") == HexCursor(1, 0, "hex")
            assert await press_cursor(pilot, view, "left") == HexCursor(0, 1, "hex")


@pytest.mark.asyncio
async def test_left_stops_at_zero(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await press_cursor(pilot, view, "right")
            assert await press_cursor(pilot, view, "left", "left", "left") == HexCursor(0, 0, "hex")
            assert len(host.moved) == 2


@pytest.mark.asyncio
async def test_right_stops_at_the_end_of_document_position(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"AB")) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, "right", "right", "right", "right") == HexCursor(2, 0, "hex")
            events = len(host.moved)
            assert await press_cursor(pilot, view, "right") == HexCursor(2, 0, "hex")
            assert len(host.moved) == events


@pytest.mark.asyncio
async def test_char_pane_steps_by_byte(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"ABC")) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await press_cursor(pilot, view, "tab")
            assert await press_cursor(pilot, view, "right") == HexCursor(1, 0, "char")
            assert await press_cursor(pilot, view, "right", "right", "right") == HexCursor(3, 0, "char")
            assert await press_cursor(pilot, view, "left") == HexCursor(2, 0, "char")


@pytest.mark.asyncio
async def test_down_and_up_keep_column_and_nibble(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(100))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, *["right"] * 7) == HexCursor(3, 1, "hex")
            assert await press_cursor(pilot, view, "down") == HexCursor(19, 1, "hex")
            assert await press_cursor(pilot, view, "up") == HexCursor(3, 1, "hex")


@pytest.mark.asyncio
async def test_down_into_a_short_last_row_lands_on_the_end_position(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(100))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(90, 1)
            assert await press_cursor(pilot, view, "down") == HexCursor(100, 0, "hex")


@pytest.mark.asyncio
async def test_up_on_row_0_and_down_on_last_row_do_not_move_and_post_no_event(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(100))) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(3)
            await pilot.pause()
            events = len(host.moved)
            assert await press_cursor(pilot, view, "up") == HexCursor(3, 0, "hex")
            assert len(host.moved) == events
            view.set_cursor(98)
            await pilot.pause()
            events = len(host.moved)
            assert await press_cursor(pilot, view, "down") == HexCursor(98, 0, "hex")
            assert len(host.moved) == events


@pytest.mark.asyncio
async def test_pageup_pagedown_move_by_the_visible_rows(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            height = view.scrollable_content_region.height
            await press_cursor(pilot, view, "right", "right", "right")
            assert await press_cursor(pilot, view, "pagedown") == HexCursor(height * 16 + 1, 1, "hex")
            assert view.scroll_offset.y <= height < view.scroll_offset.y + height
            assert await press_cursor(pilot, view, "pagedown", "pagedown") == HexCursor(3 * height * 16 + 1, 1, "hex")
            assert await press_cursor(pilot, view, "pageup") == HexCursor(2 * height * 16 + 1, 1, "hex")
            assert await press_cursor(pilot, view, *["pageup"] * 5) == HexCursor(1, 1, "hex")


@pytest.mark.asyncio
async def test_home_end_in_a_row(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(40))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(20, 1)
            assert await press_cursor(pilot, view, "home") == HexCursor(16, 0, "hex")
            assert await press_cursor(pilot, view, "end") == HexCursor(31, 1, "hex")
            view.set_cursor(33)
            assert await press_cursor(pilot, view, "end") == HexCursor(40, 0, "hex")
            assert await press_cursor(pilot, view, "home") == HexCursor(32, 0, "hex")
            await press_cursor(pilot, view, "tab")
            assert await press_cursor(pilot, view, "up", "end") == HexCursor(31, 0, "char")


@pytest.mark.asyncio
async def test_ctrl_home_ctrl_end(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            row_texts(view)
            with logged_reads(document) as log:
                assert await press_cursor(pilot, view, "right", "ctrl+end") == HexCursor(1600, 0, "hex")
                row_texts(view)
                assert len(log.calls) <= 1
            assert await press_cursor(pilot, view, "ctrl+home") == HexCursor(0, 0, "hex")


@pytest.mark.asyncio
async def test_shift_right_selects_whole_bytes(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, "shift+right") == HexCursor(1, 0, "hex")
            assert view.selection == ByteSelection(0, 1)
            assert await press_cursor(pilot, view, "shift+right") == HexCursor(2, 0, "hex")
            assert view.selection == ByteSelection(0, 2)
            await press_cursor(pilot, view, "right")  # plain move: low digit of byte 2, selection gone
            assert await press_cursor(pilot, view, "shift+right") == HexCursor(3, 0, "hex")
            assert view.selection == ByteSelection(2, 3)


@pytest.mark.asyncio
async def test_shift_left_selects_the_byte_before(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(3)
            assert await press_cursor(pilot, view, "shift+left") == HexCursor(2, 0, "hex")
            assert view.selection == ByteSelection(2, 3)
            assert await press_cursor(pilot, view, "shift+right", "shift+right") == HexCursor(4, 0, "hex")
            assert view.selection == ByteSelection(3, 4)


@pytest.mark.asyncio
async def test_shift_down_end_pagedown_and_ctrl_shift_end(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            height = view.scrollable_content_region.height
            assert await press_cursor(pilot, view, "shift+down") == HexCursor(16, 0, "hex")
            assert view.selection == ByteSelection(0, 16)
            assert await press_cursor(pilot, view, "shift+end") == HexCursor(31, 0, "hex")
            assert view.selection == ByteSelection(0, 31)
            assert await press_cursor(pilot, view, "shift+home") == HexCursor(16, 0, "hex")
            assert await press_cursor(pilot, view, "shift+pagedown") == HexCursor(16 + height * 16, 0, "hex")
            assert view.selection == ByteSelection(0, 16 + height * 16)
            assert await press_cursor(pilot, view, "ctrl+shift+end") == HexCursor(1600, 0, "hex")
            assert view.selection == ByteSelection(0, 1600)
            assert await press_cursor(pilot, view, "shift+up") == HexCursor(1584, 0, "hex")
            assert await press_cursor(pilot, view, "ctrl+shift+home") == HexCursor(0, 0, "hex")
            assert view.selection is None


@pytest.mark.asyncio
async def test_a_plain_move_clears_the_selection_and_moves_from_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await press_cursor(pilot, view, "shift+right", "shift+right")
            assert view.selection == ByteSelection(0, 2)
            assert await press_cursor(pilot, view, "right") == HexCursor(2, 1, "hex")
            assert view.selection is None
            await press_cursor(pilot, view, "shift+down")
            assert await press_cursor(pilot, view, "up") == HexCursor(2, 0, "hex")
            assert view.selection is None


@pytest.mark.asyncio
async def test_select_all_selects_zero_to_length_and_highlights_both_panes(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert await press_cursor(pilot, view, "ctrl+a") == HexCursor(64, 0, "hex")
            assert view.selection == ByteSelection(0, 64)
            layout = view.hex_layout
            strip = view.render_line(0)
            selected = view.get_component_rich_style("hex-view--selection")
            for index in (0, 7, 15):
                assert cell_style(strip, layout.digit_x(index, 0)) == selected
                assert cell_style(strip, layout.digit_x(index, 1)) == selected
                assert cell_style(strip, layout.char_cell_x(index)) == selected
            view.action_select_all()
            assert view.selection == ByteSelection(0, 64)


@pytest.mark.asyncio
async def test_click_on_high_and_low_digit(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await click_at(pilot, view, layout.digit_x(5, 0), 1)
            assert view.cursor == HexCursor(21, 0, "hex")
            await click_at(pilot, view, layout.digit_x(9, 1), 0)
            assert view.cursor == HexCursor(9, 1, "hex")


@pytest.mark.asyncio
async def test_click_on_a_character_sets_the_char_pane(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await click_at(pilot, view, view.hex_layout.char_cell_x(3), 2)
            assert view.cursor == HexCursor(35, 0, "char")


@pytest.mark.asyncio
async def test_click_on_the_gap_picks_the_nearest_digit_ties_left(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await click_at(pilot, view, layout.digit_x(2, 1) + 1, 0)  # the space between two bytes
            assert view.cursor == HexCursor(2, 1, "hex")
            await click_at(pilot, view, layout.digit_x(7, 1) + 1, 0)  # first cell of the gap between groups
            assert view.cursor == HexCursor(7, 1, "hex")
            await click_at(pilot, view, layout.digit_x(7, 1) + 2, 0)  # second cell of the gap
            assert view.cursor == HexCursor(8, 0, "hex")


@pytest.mark.asyncio
async def test_click_on_the_offset_column_keeps_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await click_at(pilot, view, layout.digit_x(5, 0), 0)
            assert view.cursor == HexCursor(5, 0, "hex")
            events = len(host.moved)
            await click_at(pilot, view, 3, 2)
            await click_at(pilot, view, layout.char_x - 1, 2)
            assert view.cursor == HexCursor(5, 0, "hex")
            assert len(host.moved) == events


@pytest.mark.asyncio
async def test_click_right_of_the_last_byte_or_below_lands_on_the_end_position(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(20))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await click_at(pilot, view, layout.digit_x(10, 1), 1)
            assert view.cursor == HexCursor(20, 0, "hex")
            await click_at(pilot, view, layout.digit_x(1, 0), 0)
            await click_at(pilot, view, layout.char_cell_x(10), 1)
            assert view.cursor == HexCursor(20, 0, "char")
            await click_at(pilot, view, layout.digit_x(1, 0), 0)
            await click_at(pilot, view, layout.digit_x(0, 0), 5)
            assert view.cursor == HexCursor(20, 0, "hex")


@pytest.mark.asyncio
@pytest.mark.parametrize(("pressed", "released"), [(2, 5), (5, 2)])
async def test_drag_forward_and_backward_selects_the_inclusive_range(tmp_path: Path, pressed: int, released: int) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await pilot.hover(view, offset=(layout.digit_x(pressed, 0), 0))
            await pilot.mouse_down(view, offset=(layout.digit_x(pressed, 0), 0))
            await pilot.hover(view, offset=(layout.digit_x(released, 1), 0))
            await pilot.mouse_up(view, offset=(layout.digit_x(released, 1), 0))
            await pilot.pause()
            assert view.selection == ByteSelection(2, 6)
            strip = view.render_line(0)
            selected = view.get_component_rich_style("hex-view--selection")
            for index in (3, 4):
                assert cell_style(strip, layout.digit_x(index, 0)) == selected
                assert cell_style(strip, layout.char_cell_x(index)) == selected


@pytest.mark.asyncio
async def test_click_without_drag_has_no_selection(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await click_at(pilot, view, view.hex_layout.digit_x(4, 0), 0)
            assert view.cursor == HexCursor(4, 0, "hex")
            assert view.selection is None


@pytest.mark.asyncio
async def test_shift_click_extends_from_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            layout = view.hex_layout
            await click_at(pilot, view, layout.digit_x(2, 0), 0)
            await click_at(pilot, view, layout.digit_x(6, 1), 0, shift=True)
            assert view.cursor == HexCursor(6, 0, "hex")
            assert view.selection == ByteSelection(2, 6)


@pytest.mark.asyncio
async def test_wheel_scrolls(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert view.scroll_offset.y == 0
            view.post_message(events.MouseScrollDown(widget=view, x=10, y=5, delta_x=0, delta_y=1, button=0, shift=False, meta=False, ctrl=False))
            await pilot.pause()
            assert view.scroll_offset.y > 0
            assert view.cursor == HexCursor(0, 0, "hex")


@pytest.mark.asyncio
async def test_moved_is_posted_per_change_and_not_for_a_no_op(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert host.moved == []
            await press_cursor(pilot, view, "right")
            await press_cursor(pilot, view, "shift+right")
            await press_cursor(pilot, view, "ctrl+home")
            await press_cursor(pilot, view, "left", "ctrl+home", "home")
            assert [(message.cursor, message.selection) for message in host.moved] == [
                (HexCursor(0, 1, "hex"), None),
                (HexCursor(1, 0, "hex"), ByteSelection(0, 1)),
                (HexCursor(0, 0, "hex"), None),
            ]


@pytest.mark.asyncio
async def test_the_cursor_scrolls_into_view_and_a_far_move_centres_the_row(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(1600))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            height = view.scrollable_content_region.height
            await press_cursor(pilot, view, "ctrl+end")
            assert view.scroll_offset.y <= 100 < view.scroll_offset.y + height
            await press_cursor(pilot, view, "ctrl+home")
            assert view.scroll_offset.y == 0
            await press_cursor(pilot, view, *["down"] * height)
            assert view.cursor.offset == height * 16
            assert view.scroll_offset.y == 1
            view.goto(800)
            await pilot.pause()
            assert view.cursor == HexCursor(800, 0, "hex")
            assert view.scroll_offset.y == 50 - height // 2
            assert row_texts(view)[height // 2].startswith("00000320  ")


@pytest.mark.asyncio
async def test_insert_mode_is_false_and_other_keys_do_nothing(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert view.insert_mode is False
            await press_cursor(pilot, view, "right")
            assert await press_cursor(pilot, view, "a", "1", "delete", "backspace", "ctrl+z") == HexCursor(0, 1, "hex")
            assert document.length == 64
            assert row_texts(view)[0].startswith("00000000  00 00")


@pytest.mark.asyncio
async def test_cursor_outside_the_cut_area_is_still_tracked(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(30, 24)) as pilot:
            await pilot.pause()
            assert view.hex_layout.bytes_per_row == 8
            assert await press_cursor(pilot, view, *["right"] * 20) == HexCursor(10, 0, "hex")
            assert await press_cursor(pilot, view, "tab", "end") == HexCursor(15, 0, "char")
            assert len(row_texts(view)[1]) == view.scrollable_content_region.width
