"""`AnchorIndex` adapter over one `LongLineIndex` (ACT3 design 7): byte anchored cursor arithmetic for a long row.

Every method is non-blocking: values beyond the scanned frontier are reported as `None` or estimated, never waited for.
Byte offsets are relative to the start of the row and are expected at character boundaries (results of `step` always are).
"""

from __future__ import annotations

from bisect import bisect_right

from nova_editor.core import LongLineIndex
from nova_editor.core.text_width import MAX_SEQUENCE, utf8_len

_CHUNK_CHARS = 8192
"""The most characters one `step` read decodes."""


class LongRowAnchorIndex:
    """Adapts a `LongLineIndex` to the `AnchorIndex` protocol (and `EstimateLengthIndex`) of the cursor machine."""

    def __init__(self, index: LongLineIndex) -> None:
        """Wrap `index`; the adapter holds no state of its own."""
        self._index = index

    @property
    def index(self) -> LongLineIndex:
        """The adapted index."""
        return self._index

    @property
    def row_end_rel(self) -> int:
        """Byte length of the row content (the byte offset of its end, relative to its start)."""
        return self._index.end_offset - self._index.start_offset

    def frontier_byte(self) -> int:
        """Return the bytes (relative to the row start) scanned so far."""
        return self._index.frontier().byte_rel

    def complete(self) -> bool:
        """Return whether the whole row has been scanned."""
        return self._index.frontier().complete

    def _floor(self, byte_rel: int) -> int:
        """Return the start of the character that holds `byte_rel` (`byte_rel` itself on a boundary), clamped to the row."""
        byte_rel = min(max(0, byte_rel), self.row_end_rel)
        start = max(0, byte_rel - MAX_SEQUENCE + 1)
        text, skipped = self._index.decode_from_byte(start, MAX_SEQUENCE)
        boundary = start + skipped
        if boundary > byte_rel:
            return byte_rel
        for char in text:
            following = boundary + utf8_len(char)
            if following > byte_rel:
                break
            boundary = following
        return boundary

    def exact_column(self, byte_rel: int) -> int | None:
        """Return the exact column of a byte offset (of its character when it is not on a boundary), or `None` beyond the frontier."""
        frontier = self._index.frontier()
        byte_rel = self._floor(byte_rel)
        if byte_rel > frontier.byte_rel and not frontier.complete:
            return None
        return self._index.byte_to_char_approx(byte_rel)

    def approx_column(self, byte_rel: int) -> int:
        """Return the column of a byte offset: exact inside the frontier, scaled beyond it (monotone in `byte_rel`)."""
        return self._index.byte_to_char_approx(self._floor(byte_rel))

    def estimate_length(self) -> int:
        """Return the estimated length of the row in characters (exact once the scan completed)."""
        return self._index.estimate_length()

    def step(self, byte_rel: int, chars: int) -> int:
        """Return the byte offset `chars` characters away (negative is left), at a character boundary, clamped to the row.

        Reads at most a few kilobytes per 8192 characters; a step that reaches the end of the row for sure
        (`chars` not less than the remaining bytes) reads nothing.
        """
        end = self.row_end_rel
        byte_rel = min(max(0, byte_rel), end)
        if chars >= 0:
            return self._step_right(byte_rel, chars, end)
        if -chars >= byte_rel:
            return 0
        return self._step_left(byte_rel, -chars)

    def _step_right(self, byte_rel: int, chars: int, end: int) -> int:
        if chars >= end - byte_rel:
            return end
        pos = byte_rel
        remaining = chars
        while remaining > 0 and pos < end:
            count = min(remaining, _CHUNK_CHARS)
            text, skipped = self._index.decode_from_byte(pos, count)
            if not text:
                break
            pos = min(end, pos + skipped + utf8_len(text))
            remaining -= len(text)
        return pos

    def _step_left(self, byte_rel: int, chars: int) -> int:
        pos = byte_rel
        remaining = chars
        while remaining > 0 and pos > 0:
            count = min(remaining, _CHUNK_CHARS)
            pos, moved = self._left_once(pos, count)
            if moved == 0:
                break
            remaining -= moved
        return pos

    def _left_once(self, byte_rel: int, chars: int) -> tuple[int, int]:
        """Move up to `chars` characters left of `byte_rel`; return the new offset and the characters moved."""
        start = max(0, byte_rel - chars * MAX_SEQUENCE - MAX_SEQUENCE)
        span = byte_rel - start
        text, skipped = self._index.decode_from_byte(start, span)
        boundaries = [start + skipped]
        for char in text:
            boundaries.append(boundaries[-1] + utf8_len(char))
        last = bisect_right(boundaries, byte_rel) - 1
        on_boundary = boundaries[last] == byte_rel
        target = last - chars if on_boundary else last - (chars - 1)
        if target < 0:  # only reachable at the row start: fewer than `chars` characters are left
            return 0, chars
        return boundaries[target], chars
