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
from typing import NamedTuple

from nova_editor.core.byte_source import ByteSource, SourceChanged
from nova_editor.core.foreground import Foreground
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
SPLICE_SYNC_BYTES = 1 << 20
_WAIT_SLICE = 0.05


@dataclass(frozen=True)
class Frontier:
    """How far the scan has got: characters, display columns and bytes (relative to the row start) covered."""

    chars: int
    disp: int
    byte_rel: int
    complete: bool


class Edit(NamedTuple):
    """One replacement inside a row, as `LongLineIndex.spliced` takes it.

    `x_rel` is the byte offset (relative to the row start) of the replaced range, `removed_*` measure the old text of the range and
    `added_*` the new text.
    The display measures are the widths of the text when it starts at the display column of `x_rel`.
    """

    x_rel: int
    removed_bytes: int
    removed_chars: int
    removed_disp: int
    added_bytes: int
    added_chars: int
    added_disp: int


@dataclass(frozen=True)
class _Gap:
    """Work the scan thread does first after a splice: scan the inserted text, then attach the shifted old tail.

    The gap starts at the last checkpoint (the exact one at the edit) and ends at byte `end_byte`, where the scan must reach
    `end_chars` and `end_disp`; `tail` holds the shifted checkpoints behind it and `complete` says whether the old scan had finished.
    """

    end_byte: int
    end_chars: int
    end_disp: int
    tail: tuple[list[int], list[int], list[int]]
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
        foreground: Foreground | None = None,
        autostart: bool = True,
        resume: tuple[int, int, int] | None = None,
    ) -> None:
        """Create the index; the background scan starts immediately unless `autostart` is false.

        `resume=(chars, disp, byte_rel)` makes the scan continue from a known state at a character boundary instead of the row start.
        The row start stays the first checkpoint, so queries before the resume point stay exact but decode from the row start.
        """
        if checkpoint_chars <= 0 or scan_block <= 0 or tab_width <= 0:
            msg = "checkpoint_chars, scan_block and tab_width must be positive"
            raise ValueError(msg)
        if resume is not None and not 0 <= resume[2] <= max(end - start, 0):
            msg = "resume point outside the row"
            raise ValueError(msg)
        self.start_offset = start
        self.end_offset = end
        self._source = source
        self._tab = tab_width
        self._step = checkpoint_chars
        self._scan_block = scan_block
        self._yield_seconds = yield_seconds
        self._foreground = foreground
        self._cond = threading.Condition()  # guards everything below it
        self._cp_chars = array("Q", [0])
        self._cp_disp = array("Q", [0])
        self._cp_byte = array("Q", [0])
        if resume is not None and resume[2] > 0:
            self._cp_chars.append(resume[0])
            self._cp_disp.append(resume[1])
            self._cp_byte.append(resume[2])
        self._gap: _Gap | None = None
        self._complete = end <= start
        self._error: BaseException | None = None
        self._cancelled = threading.Event()
        self._subscribers: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        if autostart:
            self.start()

    @classmethod
    def spliced(
        cls,
        old: LongLineIndex,
        new_source: ByteSource,
        edit: Edit | tuple[int, int, int, int, int, int, int],
        *,
        autostart: bool = True,
        scan_block: int | None = None,
        sync_bytes: int = SPLICE_SYNC_BYTES,
    ) -> LongLineIndex:
        """Build the index of the row after one replacement from the index of the row before it, without rescanning the row (design 6.2).

        `new_source` holds the edited row (start 0, its whole length); `edit` describes the replacement in the old row (see `Edit`) and the new bytes
        must be `old[:x_rel] + inserted + old[x_rel + removed_bytes:]` with a clean decode at both junctions.
        Checkpoints up to `x_rel` are kept, those inside the removed range are dropped, those behind it are kept shifted by the deltas, and exact
        checkpoints are added at `x_rel` and at the end of the inserted text.
        Inserted text longer than one checkpoint step gets interior checkpoints from a scan of its bytes: on the calling thread for at most
        `sync_bytes`, otherwise on the scan thread, which attaches the shifted tail when it reaches the end of the inserted text; until then the
        queries inside and behind the gap return `None`.
        When the old scan frontier lies before the edit only the prefix is kept and the scan resumes from the old frontier on `new_source`.
        Tab stops behind the edit keep their old alignment (design 6.4): exact when the display width change is a multiple of the tab width.
        The result is an ordinary index (same queries, `cancel`, `join`, `subscribe`) with the settings of `old`; `old` is left untouched.
        Raises `SourceChanged` when reading the old row fails.
        """
        edit = Edit(*edit)
        total = new_source.length()
        index = cls(
            new_source,
            0,
            total,
            tab_width=old._tab,
            checkpoint_chars=old._step,
            scan_block=old._scan_block if scan_block is None else scan_block,
            yield_seconds=old._yield_seconds,
            foreground=old._foreground,
            autostart=False,
        )
        if total > 0:
            index._adopt(old, edit, sync_bytes)
        if autostart:
            index.start()
        return index

    @classmethod
    def rebased(
        cls,
        old: LongLineIndex,
        new_source: ByteSource,
        *,
        autostart: bool = True,
        scan_block: int | None = None,
    ) -> LongLineIndex | None:
        """Move the index onto `new_source`, which holds the same row bytes as the source of `old` (design 7.3); `None` when the lengths differ.

        This is `spliced` with an identity edit at the end of the row: every checkpoint is kept, an incomplete scan resumes from the old frontier
        on `new_source`, and a complete index only scans the part behind its last checkpoint (nothing, as the last checkpoint is the row end).
        `old` is left untouched; the caller cancels and joins it.
        The caller guarantees that the bytes are identical; only the length is checked here.
        """
        length = old.end_offset - old.start_offset
        if new_source.length() != length:
            return None
        return cls.spliced(old, new_source, Edit(length, 0, 0, 0, 0, 0, 0), autostart=autostart, scan_block=scan_block)

    def _adopt(self, old: LongLineIndex, edit: Edit, sync_bytes: int) -> None:
        """Take over the checkpoints of `old` around `edit` (see `spliced`); runs before the scan thread starts."""
        with old._cond:
            chars, disp, byte = list(old._cp_chars), list(old._cp_disp), list(old._cp_byte)
            old_complete = old._complete
        x, e = edit.x_rel, edit.x_rel + edit.removed_bytes
        far = bisect_right(byte, e)  # first checkpoint behind the removed range
        tail_chars, tail_disp, tail_byte = chars[far:], disp[far:], byte[far:]
        frontier = byte[-1]
        if frontier < x or (frontier == x and not old_complete):
            self._set_checkpoints(chars, disp, byte)  # only the prefix is known: the scan resumes from the old frontier
            return
        keep = bisect_right(byte, x)
        chars, disp, byte = chars[:keep], disp[:keep], byte[:keep]
        if byte[-1] < x:  # exact checkpoint at the edit
            at = old.byte_to_char(x)
            if at is None:
                raise ValueError("edit position beyond the scanned part of the old index")
            chars.append(at)
            disp.append(old._char_disp(at))
            byte.append(x)
        d_chars = edit.added_chars - edit.removed_chars
        d_disp = edit.added_disp - edit.removed_disp
        d_byte = edit.added_bytes - edit.removed_bytes
        tail = ([v + d_chars for v in tail_chars], [v + d_disp for v in tail_disp], [v + d_byte for v in tail_byte])
        end = (chars[-1] + edit.added_chars, disp[-1] + edit.added_disp, x + edit.added_bytes)
        self._set_checkpoints(chars, disp, byte)
        # An old scan that stopped inside or at the end of the removed range has no tail; it continues behind the inserted text.
        gap = _Gap(end[2], end[0], end[1], tail, old_complete)
        if edit.added_chars <= self._step:
            self._extend_exact(end)
            self._attach(gap)
        elif edit.added_bytes <= sync_bytes:
            self._scan_span(x, end[2], chars[-1], disp[-1], polite=False)
            if not self._attach(gap):
                raise ValueError("edit does not describe the inserted bytes")
        else:
            self._gap = gap

    def _extend_exact(self, point: tuple[int, int, int]) -> None:
        if point[2] > self._cp_byte[-1]:
            self._cp_chars.append(point[0])
            self._cp_disp.append(point[1])
            self._cp_byte.append(point[2])

    def _set_checkpoints(self, chars: list[int], disp: list[int], byte: list[int]) -> None:
        with self._cond:
            self._cp_chars = array("Q", chars)
            self._cp_disp = array("Q", disp)
            self._cp_byte = array("Q", byte)
            self._complete = False

    def _char_disp(self, col: int) -> int:
        """Exact display column of a character column inside the scanned part."""
        found = self.try_char_to_disp(col)
        if found is None:
            raise ValueError("character column beyond the scanned part")
        return found

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        """Start the background scan; raise `RuntimeError` when it already started."""
        with self._start_lock:
            if self._thread is not None:
                msg = "scan already started"
                raise RuntimeError(msg)
            thread = threading.Thread(target=self._run, name="long-line-scan", daemon=True)
            thread.start()
            self._thread = thread  # published once alive: a thread that is not started yet would look finished to `quiescent`

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

    def quiescent(self) -> bool:
        """Whether the scan thread was started and has returned (completed, failed or cancelled); a started thread still in a read keeps it false.

        An index that is not started yet is not quiescent: a `start` may still follow.
        """
        thread = self._thread
        return thread is not None and not thread.is_alive()

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
        gap = self._gap
        if gap is not None:
            if not self._scan_gap(gap):
                self._notify()
                return
            self._gap = None
        with self._cond:
            if self._complete:
                done = True
            else:
                done = False
                chars, disp, rel = int(self._cp_chars[-1]), int(self._cp_disp[-1]), int(self._cp_byte[-1])
        if not done and self._scan_span(rel, self.end_offset - self.start_offset, chars, disp, polite=True) is None:
            return
        with self._cond:
            self._complete = True
            self._cond.notify_all()
        self._notify()

    def _scan_gap(self, gap: _Gap) -> bool:
        """Scan the inserted text, then attach the shifted tail; return false when cancelled or when the edit did not match the bytes."""
        with self._cond:
            chars, disp, rel = int(self._cp_chars[-1]), int(self._cp_disp[-1]), int(self._cp_byte[-1])
        if rel < gap.end_byte and self._scan_span(rel, gap.end_byte, chars, disp, polite=True) is None:
            return False
        if not self._attach(gap):
            return False
        self._notify()
        return True

    def _attach(self, gap: _Gap) -> bool:
        """Append the shifted tail checkpoints once the checkpoint at the end of the inserted text is known."""
        with self._cond:
            matches = (self._cp_chars[-1], self._cp_disp[-1], self._cp_byte[-1]) == (gap.end_chars, gap.end_disp, gap.end_byte)
            if not matches:
                self._error = ValueError("splice edit does not describe the inserted bytes")
                self._cond.notify_all()
                return False
            self._cp_chars.extend(gap.tail[0])
            self._cp_disp.extend(gap.tail[1])
            self._cp_byte.extend(gap.tail[2])
            self._complete = gap.complete
            self._cond.notify_all()
            return True

    def _scan_span(self, rel: int, limit: int, chars: int, disp: int, *, polite: bool) -> tuple[int, int, int] | None:
        """Scan the bytes `[rel, limit)` (relative to the row start) from a known state, publishing checkpoints.

        The limit is a hard end: bytes cut off there are decoded as they are.
        Returns the totals reached, or `None` when cancelled.
        `polite` notifies subscribers and yields between blocks (the scan thread); the synchronous splice scan passes false.
        """
        pos = self.start_offset + rel
        end = self.start_offset + limit
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
            chars, disp, rel = self._publish(text, chars, disp, rel, polite=polite)
            if polite:
                self._notify()
                time.sleep(self._yield_seconds)
        return (chars, disp, rel) if pos >= end and not self._cancelled.is_set() else None

    def _publish(self, text: str, chars: int, disp: int, rel: int, *, polite: bool = True) -> tuple[int, int, int]:
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
            if polite:
                self._give_way()
        with self._cond:
            self._cp_chars.extend(new_chars)
            self._cp_disp.extend(new_disp)
            self._cp_byte.extend(new_byte)
            self._cond.notify_all()
        return chars, disp, rel

    def _give_way(self) -> None:
        """Sleep briefly while the UI is busy, so that it gets the GIL (see `Foreground`)."""
        foreground = self._foreground
        pause = 0.0 if foreground is None else foreground.pause_seconds()
        if pause > 0:
            time.sleep(pause)

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

    def byte_to_char(self, byte_rel: int) -> int | None:
        """Exact character column of the byte offset `byte_rel` (relative to the row start): the characters decoded from the bytes before it.

        The checkpoint at or before the offset plus a bounded decode; a negative offset counts as 0 and an offset beyond the row end is clamped.
        A split multi-byte sequence counts as its separate escaped bytes.
        Returns `None` when the offset is beyond the scanned frontier and the scan is not complete.
        Raises `SourceChanged` when the file changed.
        """
        byte_rel = max(0, byte_rel)
        with self._cond:
            self._raise_error()
            last = int(self._cp_byte[-1])
            if byte_rel > last:
                if not self._complete:
                    return None
                byte_rel = last
            i = bisect_right(self._cp_byte, byte_rel) - 1
            ch0, b0 = int(self._cp_chars[i]), int(self._cp_byte[i])
        if byte_rel == b0:
            return ch0
        return ch0 + len(self._source.read(self.start_offset + b0, byte_rel - b0).decode("utf-8", SURROGATE_ESCAPE))

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
