"""REQ-4 oracle of the view benchmark harness: strips and cursor position of a long row against plain file I/O.

The oracle shares no code with `nova_editor`: it reads the file with `open`, splits rows with a regular expression, decodes with the
incremental UTF-8 decoder (`surrogateescape`) and measures characters with `rich.cells.cell_len` (the width table the editor uses too).
"""

from __future__ import annotations

import codecs
import re
from collections import deque
from pathlib import Path
from typing import Any

from rich.cells import cell_len

from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from tools._view_app import SIZE, ProbeApp, build_config, lazy_document, open_probe, wait_until
from tools._view_scenarios import Row, Spec, base_row

TAB = 4
CHUNK = 8 << 20
BEFORE_CHARS = 200
AFTER_CHARS = 400
WINDOW_STARTS = 70
_TERMINATOR = re.compile(rb"\r\n|\n|\r")


def longest_row(path: Path) -> tuple[int, int, int]:
    """Return `(row_index, start, content_end)` of the longest row of the file (rows end with LF, CRLF or CR; byte offsets)."""
    best = (0, 0)
    best_index = 0
    row_index = 0
    row_start = 0
    offset = 0
    carry = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            final = not chunk
            data = carry + chunk
            carry = b""
            if not final and data.endswith(b"\r"):
                carry = b"\r"
                data = data[:-1]
            for match in _TERMINATOR.finditer(data):
                content_end = offset + match.start()
                if content_end - row_start > best[1] - best[0]:
                    best = (row_start, content_end)
                    best_index = row_index
                row_index += 1
                row_start = offset + match.end()
            offset += len(data)
            if final:
                if offset - row_start > best[1] - best[0]:
                    best = (row_start, offset)
                    best_index = row_index
                return best_index, best[0], best[1]


def _advance(display: int, char: str) -> int:
    return display + (TAB - display % TAB if char == "\t" else cell_len(char))


def row_window(path: Path, start: int, end: int, column: int) -> tuple[int, int, int, list[tuple[str, int]]]:
    """Decode the row `[start, end)` character by character and return the window around `column`.

    Returns:
        `(column, first_column, first_display, chars)`: the column actually used (the last one when the row is shorter),
        the column and display start of the first window character, and `(character, display_start)` pairs of the window.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("surrogateescape")
    before: deque[tuple[str, int]] = deque(maxlen=BEFORE_CHARS)
    after: list[tuple[str, int]] = []
    index = 0
    display = 0
    hit = False
    remaining = end - start
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0 and len(after) < AFTER_CHARS:
            data = handle.read(min(CHUNK, remaining))
            remaining -= len(data)
            for char in decoder.decode(data, final=remaining <= 0):
                if hit:
                    after.append((char, display))
                else:
                    before.append((char, display))
                    hit = index == column
                display = _advance(display, char)
                index += 1
                if len(after) >= AFTER_CHARS:
                    break
    used = column if hit else index - 1
    chars = list(before) + after
    first_column = used - (len(before) - 1)
    return used, first_column, chars[0][1] if chars else 0, chars


def cells_of(chars: list[tuple[str, int]], first_display: int) -> list[str]:
    """Return the display cells of the characters: a wide character is followed by an empty cell, a zero-width one joins the previous cell."""
    cells: list[str] = []
    for char, _start in chars:
        if char == "\t":
            cells.extend(" " * (TAB - (first_display + len(cells)) % TAB))
            continue
        width = cell_len(char)
        if width == 0:
            if cells:
                previous = len(cells) - 1
                while previous > 0 and cells[previous] == "":
                    previous -= 1
                cells[previous] += char
            continue
        cells.append(char)
        cells.extend([""] * (width - 1))
    return cells


def cells_to_text(cells: list[str], start: int, width: int) -> str:
    """Join `width` cells from `start` like a cropped strip: a wide character cut by an edge becomes a space; padded to `width`."""
    picked = cells[start : start + width]
    if picked and picked[0] == "" and start > 0:
        picked[0] = " "
    if picked and start + len(picked) < len(cells) and cells[start + len(picked)] == "" and picked[-1] != "":
        picked[-1] = " "
    return "".join(picked) + " " * (width - len(picked))


async def oracle_scenario(spec: Spec) -> list[Row]:
    """Compare the rendered strip text and the cursor screen x at `column` with the oracle, for `WINDOW_STARTS` consecutive scroll offsets."""
    path = Path(spec["file"])
    row, start, end = longest_row(path)
    column, first_column, first_display, chars = row_window(path, start, end, int(spec["column"]))
    cells = cells_of(chars, first_display)
    cursor_display = chars[column - first_column][1] if chars else 0
    area = open_probe(path, wrap=False, config=build_config(path, spec.get("config", "auto")))
    app = ProbeApp(area)
    mismatches: list[dict[str, Any]] = []
    checks = 0
    skipped = 0
    async with app.run_test(size=SIZE) as pilot:
        if not await wait_until(lambda: area.first_content_ns is not None, 120):
            msg = "the widget showed no content"
            raise RuntimeError(msg)
        document = lazy_document(area)
        wrapped = area.wrapped_document
        if document is None or not isinstance(wrapped, LazyWrappedDocument):
            msg = "the file did not open lazily"
            raise RuntimeError(msg)
        if document.is_long(row):
            document.long_index(row).join(float(spec.get("timeout", 900)))
        area.scroll_to(y=wrapped.y_of_row(row), animate=False)
        area.move_cursor((row, column))
        await pilot.pause()
        state = area.cursor_state.name
        if state != "RESOLVED":
            mismatches.append({"what": "cursor_state", "expected": "RESOLVED", "actual": state})
        width = area.scrollable_content_region.width - area.gutter_width
        for k in range(WINDOW_STARTS):
            x = max(first_display, cursor_display - k)
            area.scroll_to(x=x, animate=False)
            await pilot.pause()
            actual_x = area.scroll_offset.x
            if actual_x != x:
                skipped += 1
                continue
            y = wrapped.y_of_row(row) - area.scroll_offset.y
            actual = area.render_line(y).crop(area.gutter_width, area.gutter_width + width).text
            expected = cells_to_text(cells, x - first_display, width)
            checks += 1
            if actual != expected:
                mismatches.append({"what": "strip", "scroll_x": x, "expected": expected, "actual": actual})
            if 0 <= cursor_display - x < width:
                checks += 1
                expected_x = area.content_region.x + cursor_display - x + area.gutter_width
                if area.cursor_screen_offset.x != expected_x:
                    mismatches.append({"what": "cursor_x", "scroll_x": x, "expected": expected_x, "actual": area.cursor_screen_offset.x})
    return [
        base_row(
            spec,
            case="oracle",
            state="indexed",
            op="strip_and_cursor",
            column=column,
            row=row,
            checks=checks,
            skipped=skipped,
            mismatches=len(mismatches),
            mismatch_samples=mismatches[:5],
        )
    ]
