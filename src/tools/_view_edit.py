"""Edit memory scenario of the view benchmark harness (ACT4 design 15): a scripted session of delete, undo, redo, copy, paste and undo.

The document is checked after every step against a model of the original file (`FileModel`): hashes of windows of the document at fixed
offsets (and around the edit points) must equal the hashes of the windows computed from the file with the offset arithmetic of the scenario.
The helpers of this module are shared with the latency scenarios (`_view_edit_latency`).
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path

from nova_editor.document._document import Location, Selection
from nova_editor.document._lazy_document import LazyDocument
from tools._view_app import SIZE, ProbeTextArea, _view_state, emit, first_row_is_long, lazy_document, scan_busy, settle, timed, wait_until
from tools._view_scenarios import Row, Spec, _first_content, _open, base_row

WINDOW = 4096
"""Bytes of one verified window."""
FIXED_WINDOWS = 8
"""Windows at fixed fractions of the document, besides the ones around the edit points."""
LINES_SELECT_BYTES = 1_000_000_000
LONGLINE_SELECT_BYTES = 100_000_000
STEP_LIMIT = 60.0
"""Seconds one scripted step may take before its time is recorded as missing."""
MEMORY_PHASES = ("open", "indexing", "select", "delete", "undo", "redo", "select_copy", "copy", "goto_paste", "paste", "undo_paste")
"""Phase marks (`PHASE <name> <ns>`) of the edit memory child, in order."""

Seg = tuple[int, int]
"""A run of the original file: `(file offset, length)`."""


def edit_state(area: ProbeTextArea) -> tuple[object, ...]:
    """The observable state of an edit step: the view state plus the document length (a forward delete moves neither cursor nor scroll)."""
    document = lazy_document(area)
    return (*_view_state(area), 0 if document is None else document.length)


def piece_count(area: ProbeTextArea) -> int | None:
    """Number of pieces of the document (read from outside through `getattr`, so a change of the core does not break the harness)."""
    table = getattr(lazy_document(area), "_table", None)
    tree = getattr(table, "tree", None)
    count = getattr(tree, "piece_count", None)
    return count if isinstance(count, int) else None


def _cut(segs: list[Seg], start: int, end: int) -> tuple[list[Seg], list[Seg], list[Seg]]:
    before: list[Seg] = []
    middle: list[Seg] = []
    after: list[Seg] = []
    position = 0
    for offset, length in segs:
        low, high = position, position + length
        if low < start:
            before.append((offset, min(high, start) - low))
        first, last = max(low, start), min(high, end)
        if first < last:
            middle.append((offset + first - low, last - first))
        if high > end:
            keep = max(end, low)
            after.append((offset + keep - low, high - keep))
        position = high
    return before, middle, after


class FileModel:
    """The expected document: a list of ranges of the original file, changed with the same offset arithmetic as the scenario.

    `delete`, `insert` and `slice` mirror delete, undo and paste; `read` returns bytes computed from the file, never from the editor.
    """

    def __init__(self, path: Path) -> None:
        self._fd = os.open(path, os.O_RDONLY)
        size = os.fstat(self._fd).st_size
        self._segs: list[Seg] = [(0, size)] if size else []

    def close(self) -> None:
        """Close the file."""
        os.close(self._fd)

    @property
    def length(self) -> int:
        """Length of the expected document."""
        return sum(length for _, length in self._segs)

    def slice(self, start: int, size: int) -> list[Seg]:
        """The ranges of the expected bytes `[start, start + size)`."""
        return _cut(self._segs, start, start + size)[1]

    def delete(self, start: int, size: int) -> list[Seg]:
        """Remove `[start, start + size)` and return the removed ranges."""
        before, middle, after = _cut(self._segs, start, start + size)
        self._segs = [*before, *after]
        return middle

    def insert(self, at: int, segs: list[Seg]) -> None:
        """Insert ranges at document offset `at`."""
        before, _middle, after = _cut(self._segs, at, at)
        self._segs = [*before, *segs, *after]

    def read(self, offset: int, size: int) -> bytes:
        """Return up to `size` expected bytes from `offset`."""
        parts = [os.pread(self._fd, length, file_offset) for file_offset, length in self.slice(offset, size)]
        return b"".join(parts)


def window_offsets(length: int, extra: list[int]) -> list[int]:
    """Offsets of the verified windows: fixed fractions of the document and a window that starts half a window before each `extra` offset."""
    last = max(0, length - WINDOW)
    chosen = {last * i // (FIXED_WINDOWS - 1) for i in range(FIXED_WINDOWS)}
    chosen.update(min(max(0, offset - WINDOW // 2), last) for offset in extra)
    return sorted(chosen)


def verify_row(spec: Spec, step: str, document: LazyDocument, model: FileModel, extra: list[int]) -> Row:
    """Compare window hashes of the document with those of the model; `mismatches` counts differing windows plus a differing length."""
    offsets = window_offsets(document.length, extra)
    bad = [offset for offset in offsets if hashlib.sha256(model.read(offset, WINDOW)).digest() != hashlib.sha256(document.read_bytes(offset, WINDOW, cache=False)).digest()]
    length_ok = document.length == model.length
    return base_row(
        spec,
        case="verify",
        state=step,
        op="verify",
        windows=len(offsets),
        mismatches=len(bad) + (0 if length_ok else 1),
        mismatch_offsets=bad[:10],
        doc_length=document.length,
        model_length=model.length,
    )


def place_at(document: LazyDocument, offset: int, *, up: bool = False) -> tuple[Location, int]:
    """Return the location near byte `offset` and the exact byte of that location.

    A short row snaps to its start (`up`: to the start of the next row, so a range always ends after it started); a position in a long row is the column of the byte.
    The document must be indexed (the long row scan complete).
    """
    found = document.row_at_offset(offset)
    if found is None:
        msg = f"byte {offset} is not resolved"
        raise RuntimeError(msg)
    row, span = found
    if document.is_long(row):
        column = document.long_index(row).byte_to_char(offset - span.start)
        exact = None if column is None else document.byte_offset(row, column)
        if column is None or exact is None:
            msg = f"column of byte {offset} in long row {row} is not resolved"
            raise RuntimeError(msg)
        return (row, column), exact
    if up and row + 1 < document.line_count:
        return (row + 1, 0), span.end
    if up:
        return (row, document.line_length(row) or 0), span.content_end
    return (row, 0), span.start


def is_longline(spec: Spec, area: ProbeTextArea) -> bool:
    """Whether the scenario runs the long row variant: asked for with `variant` `longline`, or `auto` and row 0 is a long row."""
    variant = spec.get("variant", "auto")
    return variant == "longline" or (variant == "auto" and first_row_is_long(area))


def _document(area: ProbeTextArea) -> LazyDocument:
    document = lazy_document(area)
    if document is None:
        msg = "the file did not open lazily"
        raise RuntimeError(msg)
    return document


class _Session:
    """The running edit memory session: one timed step after the other, each followed by the window verification."""

    def __init__(self, spec: Spec, area: ProbeTextArea, model: FileModel, *, longline: bool) -> None:
        self.spec = spec
        self.area = area
        self.document = _document(area)
        self.model = model
        self.variant = "longline" if longline else "lines"
        self.timeout = float(spec.get("timeout", 900))
        self.rows: list[Row] = []
        self.extra: list[int] = []
        """Offsets of the edit points; a verified window starts before each."""

    async def step(self, name: str, action: Callable[[], None], update: Callable[[], None] | None = None) -> None:
        """Run `action` timed, apply `update` to the model, record the step and its verification, print the phase mark."""
        area = self.area
        timing = await timed(area, action, limit=min(self.timeout, STEP_LIMIT), require_change=False, state=edit_state)
        await wait_until(lambda: area.pending_progress is None, 5.0)
        started = time.perf_counter()
        await wait_until(lambda: not scan_busy(area), self.timeout)
        reindexed_ms = (time.perf_counter() - started) * 1000
        await settle(0.2)
        if update is not None:
            update()
        self.rows.append(
            base_row(
                self.spec,
                case="edit-memory",
                state=name,
                op=name,
                variant=self.variant,
                action_ms=timing.latency_ms,
                reindexed_ms=reindexed_ms,
                doc_length=self.document.length,
                pieces=piece_count(area),
            )
        )
        self.rows.append(verify_row(self.spec, name, self.document, self.model, self.extra))
        emit(f"PHASE {name} {time.perf_counter_ns()}")

    def selector(self, start: Location, end: Location) -> Callable[[], None]:
        """An action that selects `start` to `end`."""

        def select() -> None:
            self.area.selection = Selection(start, end)

        return select

    async def delete_undo_redo(self, size: int) -> None:
        """Select `size` bytes from a quarter of the document, delete them, undo and redo."""
        document, model = self.document, self.model
        first_loc, first_byte = place_at(document, document.length // 4)
        last_loc, last_byte = place_at(document, document.length // 4 + size, up=True)
        self.extra += [first_byte, last_byte]
        removed: list[Seg] = []
        span = last_byte - first_byte

        def after_delete() -> None:
            removed[:] = model.delete(first_byte, span)

        def after_undo() -> None:
            model.insert(first_byte, removed)

        await self.step("select", self.selector(first_loc, last_loc))
        await self.step("delete", self.area.action_delete_left, after_delete)
        await self.step("undo", self.area.undo, after_undo)
        await self.step("redo", self.area.redo, after_delete)

    async def copy_paste_undo(self, size: int) -> None:
        """Select `size` bytes from an eighth of the document, copy them, paste them at three quarters and undo the paste."""
        document, model = self.document, self.model
        size = min(size, document.length // 2)
        copy_start, copy_start_byte = place_at(document, document.length // 8)
        copy_end, copy_end_byte = place_at(document, document.length // 8 + size, up=True)
        clip = model.slice(copy_start_byte, copy_end_byte - copy_start_byte)
        await self.step("select_copy", self.selector(copy_start, copy_end))
        await self.step("copy", self.area.action_copy)
        paste_loc, paste_byte = place_at(document, document.length * 3 // 4)
        self.extra += [copy_start_byte, paste_byte]

        def move() -> None:
            self.area.move_cursor(paste_loc)

        def after_paste() -> None:
            model.insert(paste_byte, clip)

        def after_undo() -> None:
            model.delete(paste_byte, len_of(clip))

        await self.step("goto_paste", move)
        await self.step("paste", self.area.action_paste, after_paste)
        await self.step("undo_paste", self.area.undo, after_undo)


def len_of(segs: list[Seg]) -> int:
    """Total length of ranges."""
    return sum(length for _, length in segs)


async def edit_memory_scenario(spec: Spec) -> list[Row]:
    """`edit-memory` and `verify` child: open, index, select, delete, undo, redo, copy, paste elsewhere, undo.

    Prints `PHASE <name> <ns>` after each step (the parent samples `RssAnon` and attributes the samples to the phases) and `DONE` at the end.
    Rows: one `edit-memory` row per step (`action_ms`, document length, pieces) and one `verify` row per step.
    The long row variant selects 100 MB of the line, the other 1 GB; both are clamped to half of the document.
    """
    app, area = _open(spec)
    timeout = float(spec.get("timeout", 900))
    model = FileModel(Path(spec["file"]))
    rows: list[Row] = []
    try:
        async with app.run_test(size=SIZE):
            await _first_content(area)
            emit(f"PHASE open {time.perf_counter_ns()}")
            await wait_until(lambda: not scan_busy(area), timeout)
            await settle(0.2)
            longline = is_longline(spec, area)
            session = _Session(spec, area, model, longline=longline)
            rows = session.rows
            session.rows.append(verify_row(spec, "indexing", session.document, model, session.extra))
            emit(f"PHASE indexing {time.perf_counter_ns()}")
            request = int(spec.get("select_bytes") or (LONGLINE_SELECT_BYTES if longline else LINES_SELECT_BYTES))
            size = max(1, min(request, session.document.length // 2))
            await session.delete_undo_redo(size)
            await session.copy_paste_undo(size)
    finally:
        model.close()
    emit("DONE")
    return rows
