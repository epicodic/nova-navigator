"""The pure hex layout (REQ-5, REQ-6): bytes per row, offset digits, geometry, hit testing, row text."""

from __future__ import annotations

import pytest

from nova_editor.widget.hex_layout import CHAR_TABLE, HexLayout, choose_layout, format_row, total_rows

REQ5_SAMPLE = "00000000  7F 45 4C 46 02 01 01 00  00 00 00 00 00 00 00 00  |.ELF............|"


@pytest.mark.parametrize(
    ("width", "bpr"),
    [(80, 16), (144, 32), (150, 32), (78, 16), (77, 8), (40, 8), (10, 8), (0, 8)],
)
def test_bytes_per_row_follows_the_width(width: int, bpr: int) -> None:
    assert choose_layout(width, 0).bytes_per_row == bpr


def test_offset_digits_are_eight_up_to_4_gib_then_nine() -> None:
    assert choose_layout(80, 0).offset_digits == 8
    assert choose_layout(80, 2**32 - 1).offset_digits == 8
    assert choose_layout(80, 2**32).offset_digits == 9


@pytest.mark.parametrize("length", [0, 15, 16, 17, 2**32 - 1, 2**32, 2**36 + 5])
def test_offset_digits_equal_the_digits_of_the_last_row_start(length: int) -> None:
    layout = choose_layout(80, length)
    bpr = layout.bytes_per_row
    assert layout.offset_digits == max(8, len(f"{(length // bpr) * bpr:X}"))


def test_total_rows() -> None:
    assert total_rows(0, 16) == 1
    assert total_rows(15, 16) == 1
    assert total_rows(16, 16) == 2
    assert total_rows(17, 16) == 2
    assert total_rows(8 * 1024**3, 16) == 536870913


def test_widths_of_the_geometry() -> None:
    sixteen = HexLayout(bytes_per_row=16, offset_digits=8)
    assert sixteen.hex_x == 10
    assert sixteen.hex_width == 48
    assert sixteen.char_x == 61
    assert sixteen.total_width == 78
    thirty_two = HexLayout(bytes_per_row=32, offset_digits=8)
    assert thirty_two.hex_width == 98
    assert thirty_two.total_width == 144
    eight = HexLayout(bytes_per_row=8, offset_digits=8)
    assert eight.hex_width == 23
    assert eight.total_width == 8 + 2 + 23 + 2 + 10


@pytest.mark.parametrize("bpr", [8, 16, 32])
def test_locate_round_trips_every_digit_and_character(bpr: int) -> None:
    layout = HexLayout(bytes_per_row=bpr, offset_digits=8)
    for index in range(bpr):
        for nibble in (0, 1):
            assert layout.locate(layout.digit_x(index, nibble)) == ("hex", index, nibble)
        assert layout.locate(layout.char_cell_x(index)) == ("char", index, 0)


def test_locate_outside_the_panes() -> None:
    layout = HexLayout(bytes_per_row=16, offset_digits=8)
    assert layout.locate(0) is None
    left_gap = layout.digit_x(7, 1) + 1  # right after byte 7's low digit (the last byte of a group has no space)
    assert left_gap + 1 == layout.digit_x(8, 0) - 1
    assert layout.locate(left_gap) == ("hex", 7, 1)
    assert layout.locate(left_gap + 1) == ("hex", 8, 0)
    assert layout.locate(layout.total_width + 5) is None
    assert layout.locate(layout.char_x - 1) is None  # opening "|"
    assert layout.locate(layout.char_x + layout.bytes_per_row) is None  # closing "|"


def test_the_req6_character_table() -> None:
    assert len(CHAR_TABLE) == 256
    for byte in (0x00, 0x1F, 0x7F, 0x80, 0xFF):
        assert CHAR_TABLE[byte] == "."
    assert CHAR_TABLE[0x20] == " "
    assert CHAR_TABLE[0x7E] == "~"
    assert CHAR_TABLE[0x41] == "A"


def test_format_row_builds_the_req5_sample_byte_for_byte() -> None:
    data = bytes.fromhex("7f454c4602010100") + bytes(8)
    row = format_row(choose_layout(80, 52), 0, data)
    assert row == REQ5_SAMPLE
    assert len(row) == 78


def test_format_row_of_a_short_and_of_the_end_row() -> None:
    layout = choose_layout(80, 0x15)
    short = format_row(layout, 0x10, b"ABC\x00\xff")
    prefix = "00000010  41 42 43 00 FF"
    expected = prefix + " " * (layout.char_x - 1 - len(prefix)) + "|ABC..|"
    assert short == expected.ljust(layout.total_width)
    assert len(short) == layout.total_width
    end = format_row(layout, 0x10, b"")
    assert end == "00000010".ljust(layout.total_width)
