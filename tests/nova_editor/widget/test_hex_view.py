"""Pilot tests of the hex view: rendering, scrolling, resizing and following the document (S0003 ACT4 task 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.style import Style
from textual.strip import Strip

from nova_editor.core import Content
from nova_editor.document import LazyDocument
from nova_editor.widget import HexView
from nova_editor.widget.hex_layout import format_row
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.widget.hex_helpers import HexHost, opened, row_texts, write_file

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
