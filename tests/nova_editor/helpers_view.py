"""Synthetic file builder and oracle functions for lazy view tests.

This module provides:
- make_mixed: creates synthetic test files with various text features
- oracle functions: independently verify row content, display widths, and ranges
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from rich.cells import cell_len

# Lowered options for testing lazy document with small synthetic files
LOWERED_OPTIONS = {
    "stride": 4,
    "index_long_line_threshold": 64,
    "long_row_threshold": 512,
    "word_wrap_limit": 128,
    "checkpoint_chars": 64,
}


class RowRange(NamedTuple):
    """Byte range and content boundaries for a single row."""

    start: int
    content_end: int
    end: int


def make_mixed(path: Path, *, terminator: bytes = b"\n", long_chars: int = 2000) -> Path:
    r"""Write synthetic test file with various text features.

    Args:
        path: file path to write to
        terminator: line terminator (b"\n", b"\r\n", or b"\r")
        long_chars: length of the long row

    Returns:
        The path argument, for chaining.

    Writes:
    - 10 short ASCII rows
    - a row with tabs, CJK (日本語), combining marks (é), and emoji (😀)
    - a medium row of 300 characters
    - a long row cycling "abc\tä" with one \xff byte in the middle
    - an empty row
    - a final row without terminator
    """
    rows: list[bytes] = [f"short line {i}".encode() for i in range(10)]

    # Row with tabs, CJK, combining marks, emoji
    mixed_text = "\t\ttabbed\t\tline with 日本語 and é and 😀"
    rows.append(mixed_text.encode())

    # Medium row of 300 characters
    medium_row = ("word " * 60)[:300]  # 300 chars exactly
    rows.append(medium_row.encode())

    # Long row cycling through pattern with invalid byte
    pattern = "abc\t日é😀"
    long_row_bytes = bytearray()
    char_count = 0
    midpoint_marked = False

    while len(long_row_bytes) < long_chars:
        # Mark the midpoint for 0xFF insertion
        if not midpoint_marked and len(long_row_bytes) >= long_chars // 2:
            if len(long_row_bytes) + 1 < long_chars:  # Ensure room for 0xFF and more
                long_row_bytes.append(0xFF)
                midpoint_marked = True
            else:
                # Reached end before inserting 0xFF; insert it anyway
                long_row_bytes.append(0xFF)
                break

        # Add one character from pattern (as bytes)
        char = pattern[char_count % len(pattern)]
        char_bytes = char.encode("utf-8")

        # Check if adding this character would exceed long_chars
        if len(long_row_bytes) + len(char_bytes) > long_chars:
            break

        long_row_bytes.extend(char_bytes)
        char_count += 1

    rows.append(bytes(long_row_bytes))

    # Empty row
    rows.append(b"")

    # Final row without terminator (added separately below)
    final_row = b"final line"

    # Write file with terminators between rows but not after the final row
    file_bytes = terminator.join(rows) + (terminator if rows else b"") + final_row

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)
    return path


def oracle_row_ranges(data: bytes) -> list[RowRange]:
    r"""Parse row boundaries in data with any terminator variant.

    Returns:
        List of (start, content_end, end) tuples for each row, where:
        - start: byte offset of row start
        - content_end: byte offset before the terminator
        - end: byte offset after the terminator (or end of file)

    Detects and handles terminators: LF (\n), CRLF (\r\n), CR (\r).
    """
    if not data:
        return []

    ranges: list[RowRange] = []
    pos = 0

    while pos < len(data):
        start = pos
        content_end = pos

        # Find next terminator
        while content_end < len(data):
            if content_end + 1 < len(data) and data[content_end : content_end + 2] == b"\r\n":
                # CRLF terminator
                end = content_end + 2
                break
            if data[content_end] in (ord(b"\n"), ord(b"\r")):
                # LF or CR terminator
                end = content_end + 1
                break
            content_end += 1
        else:
            # No terminator found; rest is final row
            end = len(data)

        ranges.append(RowRange(start, content_end, end))
        pos = end

    return ranges


def oracle_row_text(data: bytes, row_index: int, _terminator_regex: bytes = rb"\r\n|\n|\r") -> str:
    """Return text of specified row, decoded with surrogateescape.

    Args:
        data: file bytes
        row_index: which row to extract (0-indexed)
        _terminator_regex: unused parameter for compatibility; detection is automatic

    Returns:
        Row text decoded as UTF-8 with surrogateescape error handling.

    Raises:
        IndexError: if row_index is out of range.
    """
    ranges = oracle_row_ranges(data)
    if row_index >= len(ranges):
        raise IndexError(f"row index {row_index} out of range (file has {len(ranges)} rows)")

    row_range = ranges[row_index]
    row_bytes = data[row_range.start : row_range.content_end]
    return row_bytes.decode("utf-8", errors="surrogateescape")


def oracle_display_column(text: str, column: int, tab_width: int = 4) -> int:
    """Return display column width accounting for tabs and wide characters.

    Args:
        text: row text
        column: byte column (0-indexed) within the row
        tab_width: tab stop width in cells

    Returns:
        Display column width in cells from the start of the row to the given column.

    The calculation:
    - Iterates character by character
    - For tabs: advance to next multiple of tab_width
    - For other chars: use rich.cells.cell_len for display width
    """
    if column < 0 or not text:
        return 0

    display_col = 0
    byte_pos = 0

    for char in text:
        if byte_pos >= column:
            break

        if char == "\t":
            # Tab: advance to next tab stop
            display_col += tab_width - (display_col % tab_width)
        else:
            # Regular character: use rich.cells.cell_len for width
            display_col += cell_len(char)

        byte_pos += len(char.encode("utf-8"))

    return display_col
