"""Pure layout of the hex view (no Textual): bytes per row, column geometry, hit testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Pane = Literal["hex", "char"]

_GROUP = 8
"""Bytes per hex group."""
_GROUP_STRIDE = 25
"""Cells from the start of one group to the next: 23 for the group (8 * 3 - 1) plus the two-cell gap."""
_GROUP_CELLS = 23
"""Cells of one group of eight bytes: ``XX`` followed by a space, the last without the space."""
_GAP = 2
"""Cells between the offset column, the hex pane and the character pane."""
_MIN_BYTES_PER_ROW = 8
"""The smallest row; used also when not even it fits the width (the row is then cut at the right edge)."""
_MIN_OFFSET_DIGITS = 8
"""The smallest offset column width."""
_FIRST_PRINTABLE = 0x20
"""The first byte shown as its ASCII character (space)."""
_LAST_PRINTABLE = 0x7E
"""The last byte shown as its ASCII character (``~``)."""


@dataclass(frozen=True)
class HexLayout:
    """Geometry of one row for a given width and document length."""

    bytes_per_row: int
    """Bytes per row: a power of two, at least 8."""
    offset_digits: int
    """Digits of the offset column, at least 8."""

    @property
    def hex_x(self) -> int:
        """First cell of the hex pane."""
        return self.offset_digits + _GAP

    @property
    def hex_width(self) -> int:
        """Cells of the hex pane: the groups and the gaps between them."""
        return _GROUP_STRIDE * (self.bytes_per_row // _GROUP) - _GAP

    @property
    def char_x(self) -> int:
        """First character cell, right after the opening ``|``."""
        return self.hex_x + self.hex_width + _GAP + 1

    @property
    def total_width(self) -> int:
        """Cells of a full row, including both ``|``."""
        return self.offset_digits + _GAP + self.hex_width + _GAP + self.bytes_per_row + 2

    def digit_x(self, index: int, nibble: int) -> int:
        """Return the cell of the high (``nibble == 0``) or low digit of byte ``index`` in the row."""
        return self.hex_x + (index // _GROUP) * _GROUP_STRIDE + (index % _GROUP) * 3 + nibble

    def char_cell_x(self, index: int) -> int:
        """Return the cell of byte ``index`` in the character pane."""
        return self.char_x + index

    def locate(self, x: int) -> tuple[Pane, int, int] | None:
        """Map a cell to ``(pane, byte index, nibble)``, or ``None`` outside both panes.

        A cell between two digit pairs resolves to the nearest digit, ties going left; the offset column,
        the gaps around the panes and the ``|`` marks give ``None``.
        """
        relative = x - self.hex_x
        if 0 <= relative < self.hex_width:
            group, within = divmod(relative, _GROUP_STRIDE)
            first = group * _GROUP
            if within == _GROUP_CELLS:
                return ("hex", first + _GROUP - 1, 1)
            if within > _GROUP_CELLS:
                return ("hex", first + _GROUP, 0)
            byte, cell = divmod(within, 3)
            return ("hex", first + byte, min(cell, 1))
        if self.char_x <= x < self.char_x + self.bytes_per_row:
            return ("char", x - self.char_x, 0)
        return None


def _offset_digits(length: int, bytes_per_row: int) -> int:
    return max(_MIN_OFFSET_DIGITS, len(f"{(length // bytes_per_row) * bytes_per_row:X}"))


def _layout(bytes_per_row: int, length: int) -> HexLayout:
    return HexLayout(bytes_per_row=bytes_per_row, offset_digits=_offset_digits(length, bytes_per_row))


def choose_layout(width: int, length: int) -> HexLayout:
    """Return the layout with the largest power-of-two row (at least 8 bytes) that fits ``width`` cells."""
    layout = _layout(_MIN_BYTES_PER_ROW, length)
    while True:
        wider = _layout(layout.bytes_per_row * 2, length)
        if wider.total_width > width:
            return layout
        layout = wider


def total_rows(length: int, bytes_per_row: int) -> int:
    """Return the number of rows, including the end-of-document row when the last row is full or empty."""
    return length // bytes_per_row + 1


CHAR_TABLE: tuple[str, ...] = tuple(chr(byte) if _FIRST_PRINTABLE <= byte <= _LAST_PRINTABLE else "." for byte in range(256))
"""Character column cell per byte value (REQ-6): printable ASCII as itself, everything else ``.``."""


def char_text(data: bytes) -> str:
    """Return ``data`` as the character column shows it (REQ-6): printable ASCII as itself, else ``.``."""
    return "".join(CHAR_TABLE[byte] for byte in data)


def format_row(layout: HexLayout, offset: int, data: bytes) -> str:
    """Return the text of the row at ``offset`` holding ``data``, padded with spaces to ``total_width``.

    A short row shows only its bytes; the character pane is closed right after its last character. A row
    without bytes (the end-of-document row) shows only the offset.
    """
    text = f"{offset:0{layout.offset_digits}X}"
    if data:
        digits = data.hex().upper()
        groups = []
        for start in range(0, len(data), _GROUP):
            chunk = digits[start * 2 : (start + _GROUP) * 2]
            groups.append(" ".join(chunk[i : i + 2] for i in range(0, len(chunk), 2)))
        hex_text = (" " * _GAP).join(groups)
        chars = char_text(data)
        text += " " * _GAP + hex_text.ljust(layout.hex_width) + " " * _GAP + "|" + chars + "|"
    return text.ljust(layout.total_width)
