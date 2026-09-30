"""Wrapped view of a `LazyDocument` with on-demand wrapping and an estimated vertical space (ACT3 design 4.4).

Nothing is computed for the whole document: short rows use the stock word wrap, medium and long rows use a grid wrap
(section k holds the characters whose display start lies in [k*W, (k+1)*W); a wide character or tab crossing a boundary
belongs to the section where it starts). The vertical space is a sparse table of measured 64-row blocks with a running mean
for the unmeasured ones, so no call walks the file from row 0.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections import OrderedDict
from collections.abc import Iterator
from typing import NamedTuple, SupportsIndex, overload

from rich.cells import cell_len
from rich.text import Text
from textual._wrap import compute_wrap_offsets
from textual.expand_tabs import expand_tabs_inline, get_tab_widths
from textual.geometry import Offset, clamp

from nova_editor.document._document import Location
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable, WholeLineAccess
from nova_editor.document._wrapped_document import WrappedDocument

BLOCK_ROWS = 64
"""Rows per measured block of the vertical estimate."""
ANCHOR_WINDOW = 8
"""Blocks around the anchor that are measured contiguously (a few screens); farther targets re-anchor."""
MEASURE_MAX_ROWS = 128
"""The most medium or long rows one call (`height`, `y_of_row`, `row_of_y`) measures exactly; the others get a provisional estimate.

Short rows are bounded by the byte budget only: they are small, and the contiguous window around the anchor stays exact.
"""
MEASURE_MAX_BYTES = 4 * 1024 * 1024
"""The most row bytes one call decodes to measure rows (the core read budget); the other rows get a provisional estimate."""
_MAX_PASSES = 8
_SHORT_CACHE_ROWS = 1024
_DISP_CACHE_ROWS = 4096


class _ShortRow(NamedTuple):
    """Cached stock wrapping of a short row."""

    offsets: list[int]
    tab_widths: list[int]


class _Block:
    """Measured heights of the rows of one block."""

    __slots__ = ("cum", "extra", "limited", "provisional", "rows", "total")

    def __init__(self, heights: list[int], *, provisional: bool, limited: bool = False) -> None:
        self.cum: list[int] = []
        total = 0
        for height in heights:
            self.cum.append(total)
            total += height
        self.total = total
        self.rows = len(heights)
        self.extra = total - len(heights)
        self.provisional = provisional
        self.limited = limited
        """True when some row got an estimate because the per-call budget was spent (a later tick finishes it)."""


class GridOffsets(list[int]):
    """Lazy wrap offsets of a grid-wrapped row: item k is the first column of section k + 1.

    Implements the `LazyOffsets` protocol of the navigator; iterating a long row walks its sections up to the scanned frontier.
    """

    def __init__(self, owner: LazyWrappedDocument, row: int) -> None:
        super().__init__()
        self._owner = owner
        self._row = row

    def __len__(self) -> int:
        return self._owner.row_sections(self._row) - 1

    def __bool__(self) -> bool:
        return len(self) > 0

    def __iter__(self) -> Iterator[int]:
        section = 1
        while (column := self._owner.section_start(self._row, section)) is not None:
            yield column
            section += 1

    @overload
    def __getitem__(self, index: SupportsIndex) -> int: ...

    @overload
    def __getitem__(self, index: slice) -> list[int]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> int | list[int]:
        if isinstance(index, slice):
            return list(self)[index]
        item = index.__index__()
        if item < 0:
            item += len(self)
        column = self._owner.section_start(self._row, item + 1) if item >= 0 else None
        if column is None:
            raise IndexError(item)
        return column

    def bisect_right(self, column: int) -> int:
        """Return the number of section starts that are at or before `column`."""
        return self._owner.section_index(self._row, column)

    def is_last_section(self, column: int) -> bool:
        """Return whether `column` lies in the last section (never true while more of a long row may follow)."""
        return self._owner.is_last_section(self._row, column)

    def index_of(self, column: int) -> int:
        """Return the index of the offset equal to `column`, or -1."""
        if column <= 0:
            return -1
        section = self._owner.section_index(self._row, column)
        if section >= 1 and self._owner.section_start(self._row, section) == column:
            return section - 1
        return -1


class _LineInfo(list[tuple[int, int]]):
    """Stand-in for `WrappedDocument._offset_to_line_info`: `y -> (row, section)` computed on demand."""

    def __init__(self, owner: LazyWrappedDocument) -> None:
        super().__init__()
        self._owner = owner

    def __len__(self) -> int:
        return self._owner.height

    @overload
    def __getitem__(self, index: SupportsIndex) -> tuple[int, int]: ...

    @overload
    def __getitem__(self, index: slice) -> list[tuple[int, int]]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> tuple[int, int] | list[tuple[int, int]]:
        if isinstance(index, slice):
            msg = "slices of the lazy line info are not supported"
            raise NotImplementedError(msg)
        y = index.__index__()
        height = self._owner.height
        if y < 0:
            y += height
        if not 0 <= y < height:
            raise IndexError(y)
        return self._owner.row_of_y(y)


class LazyWrappedDocument(WrappedDocument):
    """`WrappedDocument` over a `LazyDocument`: wraps rows on demand and estimates the vertical space.

    Short rows (byte length up to `word_wrap_limit`) are wrapped exactly like the stock class. Medium and long rows use the
    grid wrap on `display_column` / `column_at_display`, never decoding a whole long row. Tab widths of long rows are the
    document's `LazyConfig.tab_width`.
    """

    def __init__(self, document: LazyDocument, width: int = 0, tab_width: int = 4) -> None:
        self._lazy = document
        self._short_rows: OrderedDict[int, _ShortRow] = OrderedDict()
        self._disp_cache: OrderedDict[int, int] = OrderedDict()
        self._blocks: dict[int, _Block] = {}
        self._keys: list[int] = []
        self._extra_prefix: list[int] = [0]
        self._rows_prefix: list[int] = [0]
        self._total_extra = 0
        self._total_rows = 0
        self._dirty = False
        self._count_seen = -1
        self._anchor = 0
        self._min_height = 0
        self._rows_left = MEASURE_MAX_ROWS
        self._bytes_left = MEASURE_MAX_BYTES
        super().__init__(document, width, tab_width)
        self._offset_to_line_info = _LineInfo(self)

    # -- configuration ------------------------------------------------------------------------
    def wrap(self, width: int, tab_width: int | None = None) -> None:
        """Store the width (0 for no wrapping) and forget every cache; nothing is computed."""
        self._width = max(0, width)
        if tab_width:
            self._tab_width = tab_width
        self._short_rows.clear()
        self._disp_cache.clear()
        self._blocks.clear()
        self._keys.clear()
        self._extra_prefix = [0]
        self._rows_prefix = [0]
        self._total_extra = 0
        self._total_rows = 0
        self._dirty = False
        self._count_seen = -1
        self._anchor = 0
        self._min_height = 0

    @property
    def wrap_width(self) -> int:
        """The wrap width in cells; 0 when wrapping is off."""
        return self._width

    @property
    def wrapped(self) -> bool:
        """True when wrapping is enabled and at least one row is estimated to occupy several lines."""
        return self._width > 0 and self.height > self._lazy.line_count

    @property
    def lines(self) -> list[list[str]]:
        """Not available for lazy documents."""
        msg = "lazy wrapped documents have no whole-document lines"
        raise NotImplementedError(msg)

    def wrap_range(self, start: Location, old_end: Location, new_end: Location) -> None:
        """Not available: lazy documents are read-only."""
        msg = "read-only until ACT4"
        raise NotImplementedError(msg)

    # -- per-row wrapping ---------------------------------------------------------------------
    def _short_row(self, row: int) -> _ShortRow:
        cached = self._short_rows.get(row)
        if cached is not None:
            self._short_rows.move_to_end(row)
            return cached
        line = self._lazy.get_line(row)
        tab_sections = get_tab_widths(line, self._tab_width)
        offsets = compute_wrap_offsets(line, self._width, tab_size=self._tab_width, precomputed_tab_sections=tab_sections) if self._width else []
        result = _ShortRow(offsets, [width for _, width in tab_sections])
        self._short_rows[row] = result
        while len(self._short_rows) > _SHORT_CACHE_ROWS:
            self._short_rows.popitem(last=False)
        return result

    def _is_short(self, row: int) -> bool:
        return self._lazy.row_class(row) == "short"

    def _check_row(self, row: int) -> None:
        count = self._lazy.line_count
        if row < 0 or row >= count:
            msg = f"The document line index {row!r} is out of bounds. The document contains {count!r} lines."
            raise ValueError(msg)

    def _total_disp(self, row: int) -> tuple[int, bool]:
        """Return `(display width, exact)` of a medium or long row."""
        doc = self._lazy
        if doc.is_long(row):
            index = doc.long_index(row)
            total = index.total_disp
            if total is None:
                return index.estimate_display_width(), False
            return total, True
        cached = self._disp_cache.get(row)
        if cached is None:
            cached = doc.row_display_width(row, self._tab_width)
            self._disp_cache[row] = cached
            while len(self._disp_cache) > _DISP_CACHE_ROWS:
                self._disp_cache.popitem(last=False)
        return cached, True

    def _grid_sections(self, row: int) -> tuple[int, bool]:
        """Return `(section count, exact)` of a grid-wrapped row."""
        total, exact = self._total_disp(row)
        width = self._width
        return max(1, -(-total // width)), exact

    def _display_estimate(self, row: int, column: int) -> int:
        """Display column of `column` when it lies beyond the scanned frontier of a long row: one cell per further character."""
        frontier = self._lazy.long_index(row).frontier()
        return frontier.disp + max(0, column - frontier.chars)

    def _column_estimate(self, row: int) -> int:
        """Column to use when a display column lies beyond the frontier: the frontier itself."""
        return self._lazy.long_index(row).frontier().chars

    def _display(self, row: int, column: int) -> int:
        shown = self._lazy.display_column(row, column, self._tab_width)
        return self._display_estimate(row, column) if shown is None else shown

    def _column(self, row: int, x: int) -> int:
        column = self._lazy.column_at_display(row, x, self._tab_width)
        return self._column_estimate(row) if column is None else column

    def row_sections(self, row: int) -> int:
        """Return the number of wrapped sections of `row` (an estimate for an unresolved long row)."""
        if not self._width:
            return 1
        if self._is_short(row):
            return len(self._short_row(row).offsets) + 1
        return self._grid_sections(row)[0]

    def section_of(self, row: int, column: int) -> int | None:
        """Return the section holding `column`, or `None` when it lies beyond the scanned frontier of a long row."""
        width = self._width
        if not width:
            return 0
        if self._is_short(row):
            return bisect_right(self._short_row(row).offsets, column)
        shown = self._lazy.display_column(row, column, self._tab_width)
        if shown is None:
            return None
        section = shown // width
        count, exact = self._grid_sections(row)
        return min(section, count - 1) if exact else section

    def section_index(self, row: int, column: int) -> int:
        """Return the section of `column`, estimated from the scanned frontier when it is not scanned yet."""
        section = self.section_of(row, column)
        if section is None:
            return self._display_estimate(row, column) // self._width
        return section

    def section_start(self, row: int, section: int) -> int | None:
        """Return the character column where `section` starts, or `None` beyond the last section or the scanned frontier."""
        if section <= 0:
            return 0
        width = self._width
        if not width:
            return None
        if self._is_short(row):
            offsets = self._short_row(row).offsets
            return offsets[section - 1] if section <= len(offsets) else None
        target = section * width
        total, exact = self._total_disp(row)
        if exact and target >= total:
            return None
        covering = self._lazy.column_at_display(row, target - 1, self._tab_width)
        return None if covering is None else covering + 1

    def section_x(self, row: int, column: int) -> tuple[int, int]:
        """Return `(section, x)` of `column`: its section and its display column relative to the section start.

        Beyond the scanned frontier of a long row both values are estimates (one cell per unscanned character); never waits.
        """
        if not self._width:
            return 0, self._display(row, column)
        section = self.section_index(row, column)
        start = self.section_start(row, section) if section else 0
        return section, self._display(row, column) - (self._display(row, start) if start else 0)

    def is_last_section(self, row: int, column: int) -> bool:
        """Return whether `column` lies in the last section; false while a long row may still continue."""
        section = self.section_index(row, column)
        if self.section_start(row, section + 1) is not None:
            return False
        return self._is_short(row) or self._grid_sections(row)[1]

    def _medium_offsets(self, row: int) -> list[int]:
        return list(GridOffsets(self, row))

    def get_offsets(self, line_index: int) -> list[int]:
        """Return the wrap offsets of a row: a plain list for short rows, lazy `GridOffsets` for medium and long rows."""
        self._check_row(line_index)
        if not self._width:
            return []
        if self._is_short(line_index):
            return self._short_row(line_index).offsets
        return GridOffsets(self, line_index)

    def get_sections(self, line_index: int) -> list[str]:
        """Return the sections of a short or medium row; a long row raises `WholeLineAccess`."""
        self._check_row(line_index)
        doc = self._lazy
        if doc.is_long(line_index):
            doc.call_log.refuse("get_sections", line_index)
            msg = f"get_sections on long row {line_index}"
            raise WholeLineAccess(msg)
        offsets = self.get_offsets(line_index)
        if not self._is_short(line_index):
            offsets = self._medium_offsets(line_index)
        return [section.plain for section in Text(doc.get_line(line_index), end="").divide(offsets)]

    def get_tab_widths(self, line_index: int) -> list[int]:
        """Return the tab widths of a short or medium row; a long row raises `WholeLineAccess`."""
        self._check_row(line_index)
        doc = self._lazy
        if doc.is_long(line_index):
            doc.call_log.refuse("get_tab_widths", line_index)
            msg = f"get_tab_widths on long row {line_index}"
            raise WholeLineAccess(msg)
        if self._is_short(line_index):
            return self._short_row(line_index).tab_widths
        return [width for _, width in get_tab_widths(doc.get_line(line_index), self._tab_width)]

    # -- location <-> offset ------------------------------------------------------------------
    def location_to_offset(self, location: Location) -> Offset:
        """Convert a location to a position in the wrapped display (x relative to the start of its section)."""
        doc = self._lazy
        row, column = location
        row = clamp(row, 0, max(0, doc.line_count - 1))
        width = self._width
        if self._is_short(row):
            offsets = self._short_row(row).offsets
            section = bisect_right(offsets, column)
            start = ([0, *offsets])[section]
            text = self.get_sections(row)[section]
            x = cell_len(expand_tabs_inline(text[: column - start], self._tab_width))
            y = self.y_of_row(row) + section if width else row
            self.note_y(y)
            return Offset(x, y)
        shown = self._display(row, column)
        if not width:
            doc.note_x(shown + 1)
            return Offset(shown, row)
        section = self.section_index(row, column)
        start = self.section_start(row, section) or 0
        y = self.y_of_row(row) + section
        self.note_y(y)
        return Offset(shown - (self._display(row, start) if section else 0), y)

    def offset_to_location(self, offset: Offset) -> Location:
        """Convert a position in the wrapped display to a location."""
        x, y = offset
        x = max(0, x)
        y = max(0, y)
        doc = self._lazy
        count = doc.line_count
        if not self._width:
            row = min(y, count - 1)
            return row, self._column(row, x)
        row, section = self.row_of_y(y)
        return row, self.get_target_document_column(row, x, section)

    def get_target_document_column(self, line_index: int, x_offset: int, y_offset: int) -> int:
        """Return the column of the row at cell `x_offset` of section `y_offset` (-1 for the last section)."""
        if self._is_short(line_index):
            return super().get_target_document_column(line_index, x_offset, y_offset)
        if not self._width:
            return self._column(line_index, x_offset)
        section = self.row_sections(line_index) - 1 if y_offset == -1 else y_offset
        start = self.section_start(line_index, section) or 0
        base = self._display(line_index, start) if section else 0
        column = self._column(line_index, base + x_offset)
        following = self.section_start(line_index, section + 1)
        if following is not None:
            column = min(column, following - 1)
        return max(column, start)

    # -- vertical estimate --------------------------------------------------------------------
    def _begin_call(self) -> None:
        """Start the measuring budget of one public call."""
        self._rows_left = MEASURE_MAX_ROWS
        self._bytes_left = MEASURE_MAX_BYTES

    def _afford(self, size: int, *, heavy: bool) -> bool:
        """Charge one row of `size` bytes against the budget of the current call (a `heavy` row also against the row cap); false when it does not fit."""
        if (heavy and self._rows_left <= 0) or size > self._bytes_left:
            return False
        if heavy:
            self._rows_left -= 1
        self._bytes_left -= size
        return True

    def _mean_height(self) -> int:
        """Height of an unmeasured short row: the running mean of the measured rows."""
        return 1 + round(self._total_extra / self._total_rows) if self._total_rows else 1

    @property
    def pending_refinement(self) -> bool:
        """True while a measured block holds estimates only because a call ran out of budget (ticks finish them)."""
        return any(blk.limited for blk in self._blocks.values())

    def _row_height(self, row: int) -> tuple[int, bool, bool]:
        """Return `(sections, provisional, limited)` of a row for the block measurement; an uncached row costs budget."""
        doc = self._lazy
        try:
            kind = doc.row_class(row)
            size = doc.row_byte_length(row)
            if kind == "short":
                if row in self._short_rows or self._afford(size, heavy=False):
                    return len(self._short_row(row).offsets) + 1, False, False
                return self._mean_height(), True, True
            if row in self._disp_cache or self._afford(size if kind == "medium" else 0, heavy=True):
                count, exact = self._grid_sections(row)
                return count, not exact, False
        except RowUnavailable:
            return 1, True, False
        return max(1, -(-size // self._width)), True, True

    def _measure(self, block: int) -> _Block:
        count = self._lazy.line_count
        first = block * BLOCK_ROWS
        heights: list[int] = []
        provisional = False
        limited = False
        for row in range(first, min(first + BLOCK_ROWS, count)):
            height, unsure, spent = self._row_height(row)
            heights.append(height)
            provisional = provisional or unsure
            limited = limited or spent
        measured = _Block(heights, provisional=provisional, limited=limited)
        if block not in self._blocks:
            insort(self._keys, block)
        self._blocks[block] = measured
        self._dirty = True
        return measured

    def _sync(self) -> None:
        """Bring the prefix sums up to date and drop measured blocks that no longer match the row count."""
        count = self._lazy.line_count
        if count != self._count_seen:
            self._count_seen = count
            stale = [b for b, blk in self._blocks.items() if blk.rows != min(BLOCK_ROWS, count - b * BLOCK_ROWS)]
            for block in stale:
                del self._blocks[block]
                self._keys.remove(block)
                self._dirty = True
        if not self._dirty:
            return
        extra = 0
        rows = 0
        extra_prefix = [0]
        rows_prefix = [0]
        for block in self._keys:
            blk = self._blocks[block]
            extra += blk.extra
            rows += blk.rows
            extra_prefix.append(extra)
            rows_prefix.append(rows)
        self._extra_prefix = extra_prefix
        self._rows_prefix = rows_prefix
        self._total_extra = extra
        self._total_rows = rows
        self._dirty = False

    def _block_count(self) -> int:
        return -(-self._lazy.line_count // BLOCK_ROWS)

    def _y_of_block(self, block: int) -> int:
        """Estimated y of the first row of `block` (exact relative to measured neighbours)."""
        rows = min(block * BLOCK_ROWS, self._lazy.line_count)
        i = bisect_left(self._keys, block)
        mean = self._total_extra / self._total_rows if self._total_rows else 0.0
        return rows + self._extra_prefix[i] + round(mean * (rows - self._rows_prefix[i]))

    def _block_at(self, y: int) -> int:
        """Largest block whose estimated start is at or above `y` from below."""
        low, high = 0, max(0, self._block_count() - 1)
        while low < high:
            middle = (low + high + 1) // 2
            if self._y_of_block(middle) <= y:
                low = middle
            else:
                high = middle - 1
        return low

    def _valid_block(self, block: int) -> _Block | None:
        return self._blocks.get(block)

    def _touch(self, block: int) -> None:
        """Measure `block`, contiguously from the anchor when it is near, and make it the new anchor; re-anchor when it is far."""
        last = self._block_count() - 1
        block = clamp(block, 0, max(0, last))
        anchor = self._anchor
        if abs(block - anchor) > ANCHOR_WINDOW:
            wanted = range(max(0, block - 1), min(last, block + 1) + 1)
        else:
            wanted = range(min(anchor, block), max(anchor, block) + 1)
        for candidate in wanted:
            if self._valid_block(candidate) is None:
                self._measure(candidate)
        self._anchor = block
        self._sync()

    def _prepare(self) -> None:
        """Start the budget of a call, measure the first block once so the estimate has a mean; refresh the prefix sums."""
        self._begin_call()
        self._sync()
        if not self._blocks and self._lazy.line_count:
            self._measure(0)
            self._sync()

    @property
    def height(self) -> int:
        """Estimated height in wrapped lines: exact for measured blocks, the running mean for the others."""
        count = self._lazy.line_count
        if not self._width:
            return count
        self._prepare()
        estimated = self._y_of_block(self._block_count()) if count else 0
        return max(self._min_height, estimated)

    def y_of_row(self, row: int) -> int:
        """Return the y of the first section of `row`; measures the blocks between the anchor and the row (or re-anchors)."""
        count = self._lazy.line_count
        if not self._width or count == 0:
            return row
        row = clamp(row, 0, count - 1)
        block = row // BLOCK_ROWS
        self._prepare()
        self._touch(block)
        blk = self._blocks[block]
        return self._y_of_block(block) + blk.cum[row - block * BLOCK_ROWS]

    def row_of_y(self, y: int) -> tuple[int, int]:
        """Return `(row, section)` at wrapped line `y`; a far `y` re-anchors instead of walking from row 0."""
        count = self._lazy.line_count
        if count == 0:
            return 0, 0
        y = max(0, y)
        if not self._width:
            return min(y, count - 1), 0
        self._prepare()
        block = self._block_at(y)
        for _ in range(_MAX_PASSES):
            if self._valid_block(block) is not None:
                break
            self._touch(block)
            block = self._block_at(y)
        measured = self._valid_block(block)
        if measured is None:
            measured = self._measure(block)
            self._sync()
        offset = clamp(y - self._y_of_block(block), 0, measured.total - 1)
        index = bisect_right(measured.cum, offset) - 1
        return block * BLOCK_ROWS + index, offset - measured.cum[index]

    def note_y(self, y: int) -> None:
        """Record that something (the cursor) sits at wrapped line `y`: the estimated height must at least cover it."""
        self._min_height = max(self._min_height, y + 1)

    def refresh_estimates(self) -> None:
        """Forget the provisional blocks (unfinished long-row scans or a spent budget; call when a scan progressed or on a tick).

        The measured widths of medium rows stay cached (a row of the file never changes), so each tick finishes more rows.
        """
        stale = [b for b, blk in self._blocks.items() if blk.provisional]
        for block in stale:
            del self._blocks[block]
            self._keys.remove(block)
        if stale:
            self._dirty = True
