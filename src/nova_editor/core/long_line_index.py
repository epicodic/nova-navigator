"""Lazy checkpoint index over one very long row (ADR-5, REQ-4, DEC-10).

Checkpoints map character column, display column and byte offset (relative to the row start). They are appended by a
background thread and never change. Queries that need data beyond the scanned frontier return `None`; blocking is opt-in
through `wait_until_known`.
"""

from __future__ import annotations

import threading
import time
from array import array
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from nova_editor.core.byte_source import ByteSource, SourceChanged
from nova_editor.core.line_index import DEFAULT_SCAN_BLOCK, call_subscriber
from nova_editor.core.text_width import (
    MAX_SEQUENCE,
    SURROGATE_ESCAPE,
    TAB_WIDTH,
    advance_disp,
    locate_cover,
    resync,
    safe_cut,
    utf8_len,
)

CHECKPOINT_CHARS = 65_536
_WAIT_SLICE = 0.05


@dataclass(frozen=True)
class Frontier:
    """How far the scan has got: characters, display columns and bytes (relative to the row start) covered."""

    chars: int
    disp: int
    byte_rel: int
    complete: bool


class LongLineIndex:
    """Checkpoints of one row `[start, end)` of the source; the row is never held as `str`.

    Every query that decodes text (`try_char_to_byte`, `try_char_to_disp`, `try_disp_to_char`, `try_get_slice`,
    `decode_from_byte`, `byte_to_char_approx`) reads the source itself and can raise `SourceChanged` when the file changed;
    a caller on the UI path must be ready to handle it.
    Before closing the source, call `cancel()` and then `join()`, so that no scan read is still in flight.
    """

    def __init__(
        self,
        source: ByteSource,
        start: int,
        end: int,
        *,
        tab_width: int = TAB_WIDTH,
        checkpoint_chars: int = CHECKPOINT_CHARS,
        scan_block: int = DEFAULT_SCAN_BLOCK,
        yield_seconds: float = 0.0,
        autostart: bool = True,
    ) -> None:
        """Create the index; the background scan starts immediately unless `autostart` is false."""
        if checkpoint_chars <= 0 or scan_block <= 0 or tab_width <= 0:
            msg = "checkpoint_chars, scan_block and tab_width must be positive"
            raise ValueError(msg)
        self.start_offset = start
        self.end_offset = end
        self._source = source
        self._tab = tab_width
        self._step = checkpoint_chars
        self._scan_block = scan_block
        self._yield_seconds = yield_seconds
        self._cond = threading.Condition()  # guards everything below it
        self._cp_chars = array("Q", [0])
        self._cp_disp = array("Q", [0])
        self._cp_byte = array("Q", [0])
        self._complete = end <= start
        self._error: BaseException | None = None
        self._cancelled = threading.Event()
        self._subscribers: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None
        if autostart:
            self.start()

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        """Start the background scan; raise `RuntimeError` when it already started."""
        if self._thread is not None:
            msg = "scan already started"
            raise RuntimeError(msg)
        self._thread = threading.Thread(target=self._run, name="long-line-scan", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop the scan at the next block boundary and wake every waiter."""
        self._cancelled.set()
        with self._cond:
            self._cond.notify_all()

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the scan thread to finish (tests and shutdown); return whether the scan completed.

        Call `cancel()` first, then `join()`, before closing the source: a cancelled scan stops at the next block boundary,
        after the read in flight returns.
        """
        if self._thread is not None:
            self._thread.join(timeout)
        return self.frontier().complete

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Call `callback` on the scan thread on each frontier advance and on completion.

        When the scan already completed or failed, the callback is also run once, immediately, on the calling thread (outside
        the lock), so a late subscriber sees the terminal state.
        An exception raised by a callback is contained and logged at debug level.
        """
        with self._cond:
            self._subscribers.append(callback)
            terminal = self._complete or self._error is not None
        if terminal:
            call_subscriber(callback)

    @property
    def running(self) -> bool:
        """Whether the scan can still make progress: not complete, not failed, not cancelled (also true just before `start`)."""
        with self._cond:
            if self._complete or self._error is not None:
                return False
        if self._cancelled.is_set():
            return False
        thread = self._thread
        return thread is None or thread.is_alive()

    def frontier(self) -> Frontier:
        """Return a consistent snapshot of the scan progress."""
        with self._cond:
            return Frontier(self._cp_chars[-1], self._cp_disp[-1], self._cp_byte[-1], self._complete)

    @property
    def checkpoint_count(self) -> int:
        """Number of checkpoints stored so far, including the one at the row start."""
        with self._cond:
            return len(self._cp_chars)

    @property
    def total_chars(self) -> int | None:
        """Exact character count once the scan completed, otherwise `None`."""
        with self._cond:
            return int(self._cp_chars[-1]) if self._complete else None

    @property
    def total_disp(self) -> int | None:
        """Exact display width once the scan completed, otherwise `None`."""
        with self._cond:
            return int(self._cp_disp[-1]) if self._complete else None

    # -- scan thread --------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._scan()
        except SourceChanged as error:
            self._fail(error)
        except Exception as error:
            self._fail(error)
            raise

    def _fail(self, error: BaseException) -> None:
        with self._cond:
            self._error = error
            self._cond.notify_all()
        self._notify()

    def _scan(self) -> None:
        pos = self.start_offset
        end = self.end_offset
        chars = disp = rel = 0
        pending = b""
        while pos < end and not self._cancelled.is_set():
            fresh = self._source.read(pos, min(self._scan_block, end - pos), cache=False)
            if not fresh:
                msg = "unexpected empty read during the long-line scan"
                raise SourceChanged(msg)
            pos += len(fresh)
            data = pending + fresh
            cut = len(data) if pos >= end else safe_cut(data)
            text = data[:cut].decode("utf-8", SURROGATE_ESCAPE)
            pending = data[cut:]
            chars, disp, rel = self._publish(text, chars, disp, rel)
            self._notify()
            time.sleep(self._yield_seconds)
        if pos >= end and not self._cancelled.is_set():
            with self._cond:
                self._complete = True
                self._cond.notify_all()
            self._notify()

    def _publish(self, text: str, chars: int, disp: int, rel: int) -> tuple[int, int, int]:
        """Append one checkpoint per `checkpoint_chars` of `text`; return the new running totals.

        The widths and byte lengths are computed before the lock is taken, so queries never wait for them.
        """
        new_chars: list[int] = []
        new_disp: list[int] = []
        new_byte: list[int] = []
        for k in range(0, len(text), self._step):
            piece = text[k : k + self._step]
            chars += len(piece)
            disp = advance_disp(piece, disp, self._tab)
            rel += utf8_len(piece)
            new_chars.append(chars)
            new_disp.append(disp)
            new_byte.append(rel)
        with self._cond:
            self._cp_chars.extend(new_chars)
            self._cp_disp.extend(new_disp)
            self._cp_byte.extend(new_byte)
            self._cond.notify_all()
        return chars, disp, rel

    def _notify(self) -> None:
        with self._cond:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            call_subscriber(callback)

    # -- waiting (tests, worker threads; never the UI thread) ----------------------------------
    def _known(self, char_col: int | None, disp_col: int | None) -> bool:
        if self._complete:
            return True
        if char_col is not None and char_col > self._cp_chars[-1]:
            return False
        return not (disp_col is not None and disp_col >= self._cp_disp[-1])

    def wait_until_known(
        self,
        char_col: int | None = None,
        disp_col: int | None = None,
        timeout: float | None = None,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Block until the scan covers a column, completes, `timeout` elapses or `cancel` is set; raise a recorded scan error.

        Returns `True` when the column is known, `False` on timeout or cancellation.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                self._raise_error()
                if self._known(char_col, disp_col):
                    return True
                if (cancel is not None and cancel.is_set()) or self._cancelled.is_set():
                    return False
                remaining = _WAIT_SLICE if deadline is None else min(_WAIT_SLICE, deadline - time.monotonic())
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)

    def _raise_error(self) -> None:
        if self._error is not None:
            raise self._error

    # -- reads --------------------------------------------------------------------------------
    def _decode_from(self, byte_rel: int, nchars: int) -> str:
        """Decode up to `nchars` characters from a known character boundary."""
        pos = self.start_offset + byte_rel
        size = min(nchars * MAX_SEQUENCE, self.end_offset - pos)
        if size <= 0:
            return ""
        return self._source.read(pos, size).decode("utf-8", SURROGATE_ESCAPE)[:nchars]

    def decode_from_byte(self, byte_rel: int, nchars: int) -> tuple[str, int]:
        """Decode from an arbitrary byte offset, skipping up to three continuation bytes first.

        Returns `(text, skipped)`. Needs no prefix scan.
        Raises `SourceChanged` when the file changed.
        """
        pos = self.start_offset + byte_rel
        size = min(nchars * MAX_SEQUENCE + MAX_SEQUENCE, self.end_offset - pos)
        if size <= 0:
            return "", 0
        data = self._source.read(pos, size)
        skip = resync(data)
        return data[skip:].decode("utf-8", SURROGATE_ESCAPE)[:nchars], skip

    def _char_checkpoint(self, col: int) -> tuple[int, int, int, int] | None:
        """Return `(chars, disp, byte, col)` of the checkpoint at or before `col`; `col` is clamped once complete."""
        with self._cond:
            self._raise_error()
            last = self._cp_chars[-1]
            if col > last:
                if not self._complete:
                    return None
                col = last
            i = bisect_right(self._cp_chars, col) - 1
            return self._cp_chars[i], self._cp_disp[i], self._cp_byte[i], col

    def _disp_checkpoint(self, target: int) -> tuple[int, int, int] | None:
        with self._cond:
            self._raise_error()
            if target >= self._cp_disp[-1] and not self._complete:
                return None
            i = bisect_right(self._cp_disp, target) - 1
            return self._cp_chars[i], self._cp_disp[i], self._cp_byte[i]

    # -- non-blocking queries: `None` means "not scanned that far yet" ------------------------
    def try_char_to_byte(self, col: int) -> int | None:
        """Byte offset (relative to the row start) of character column `col`, clamped to the row end.

        Raises `SourceChanged` when the file changed.
        """
        found = self._char_checkpoint(max(0, col))
        if found is None:
            return None
        ch0, _, b0, col = found
        return b0 if col == ch0 else b0 + utf8_len(self._decode_from(b0, col - ch0))

    def try_char_to_disp(self, col: int) -> int | None:
        """Display column of character column `col`, clamped to the row end.

        Raises `SourceChanged` when the file changed.
        """
        found = self._char_checkpoint(max(0, col))
        if found is None:
            return None
        ch0, d0, b0, col = found
        return d0 if col == ch0 else advance_disp(self._decode_from(b0, col - ch0), d0, self._tab)

    def try_disp_to_char(self, target: int, *, ceil: bool = False) -> int | None:
        """Column of the character covering display column `target` (the row length beyond the end).

        `ceil` picks the first column starting at or after `target`.
        Raises `SourceChanged` when the file changed.
        """
        if ceil:
            if target <= 0:
                return 0
            target -= 1
        elif target < 0:
            return 0
        found = self._disp_checkpoint(target)
        if found is None:
            return None
        ch, disp, byte = found
        while True:
            text = self._decode_from(byte, self._step)
            if not text:
                return ch
            index, hit = locate_cover(text, disp, target, self._tab)
            if hit:
                return ch + index + (1 if ceil else 0)
            disp = advance_disp(text, disp, self._tab)
            ch += len(text)
            byte += utf8_len(text)
            if len(text) < self._step:
                return ch

    def try_get_slice(self, a: int, b: int) -> str | None:
        """Text of columns `[a, b)`, or `None` when `a` is beyond the scanned frontier.

        One call is bounded to one checkpoint interval: the request is clamped to at most `checkpoint_chars` characters
        (`b = min(b, a + checkpoint_chars)`), so a caller that wants more asks again from the end of the returned text.
        While the scan is incomplete the request is also clamped to the scanned frontier, so the text never extends beyond it.
        An empty range returns `""` only when `a` is within the frontier (or the scan is complete).
        Raises `SourceChanged` when the file changed.
        """
        found = self._char_checkpoint(max(0, a))
        if found is None:
            return None
        ch0, _, b0, a = found
        b = min(b, a + self._step)
        with self._cond:
            if not self._complete:
                b = min(b, int(self._cp_chars[-1]))
        if b <= a:
            return ""
        return self._decode_from(b0, b - ch0)[a - ch0 :]

    # -- immediate estimates ------------------------------------------------------------------
    def _scaled(self, values: array[int]) -> int:
        with self._cond:
            value, byte = int(values[-1]), int(self._cp_byte[-1])
            complete = self._complete
        total = self.end_offset - self.start_offset
        if complete or byte <= 0 or total <= 0:
            return value
        return max(value, int(value * total / byte))

    def estimate_length(self) -> int:
        """Character count, available at any time: the scanned prefix scaled by bytes, exact once the scan completed."""
        return self._scaled(self._cp_chars)

    def estimate_display_width(self) -> int:
        """Display width, available at any time: the scanned prefix scaled by bytes, exact once the scan completed."""
        return self._scaled(self._cp_disp)

    def byte_to_char_approx(self, byte_rel: int) -> int:
        """Approximate character column of a byte offset: exact inside the frontier, scaled beyond it.

        A negative offset counts as 0.
        Raises `SourceChanged` when the file changed.
        """
        byte_rel = max(0, byte_rel)
        with self._cond:
            i = bisect_right(self._cp_byte, byte_rel) - 1
            ch0, b0 = int(self._cp_chars[i]), int(self._cp_byte[i])
            last_byte, last_chars = int(self._cp_byte[-1]), int(self._cp_chars[-1])
            complete = self._complete
        if byte_rel < last_byte or complete:
            size = max(0, min(byte_rel, self.end_offset - self.start_offset) - b0)
            return ch0 + len(self._source.read(self.start_offset + b0, size).decode("utf-8", SURROGATE_ESCAPE))
        return last_chars + int((byte_rel - last_byte) * (last_chars / last_byte)) if last_byte > 0 else byte_rel
