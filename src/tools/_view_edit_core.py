"""Core-level edit measurements of the view benchmark harness (ACT4 design 15): pieces, undo records and the system clipboard.

`pieces_scenario`: memory per piece (`tracemalloc` and `RssAnon`, one process each) and the cost of one splice and one `row_range` at several piece counts.
`undo_record_scenario`: bytes per undo record for typing, Backspace, paste and delete.
`clipboard_scenario`: time of `app.copy_to_clipboard`, with the terminal write going to a pty that a thread drains.
"""

from __future__ import annotations

import gc
import os
import pty
import sys
import termios
import threading
import time
import tracemalloc
import tty
import types
from collections import deque
from typing import Any

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, Piece, PieceTable
from nova_editor.document._edit import Edit
from tools._view_app import SIZE, ProbeTextArea, settle
from tools._view_edit import piece_count
from tools._view_procmem import read_mem
from tools._view_scenarios import Row, Spec, _first_content, _open, base_row, lazy_document

_ROW = b"abcd\n"
"""One row of the synthetic original; the pieces `bcd\\n` leave a gap of one byte so that no two pieces merge."""
_LCG_MUL = 6364136223846793005
_LCG_ADD = 1442695040888963407
_MASK64 = (1 << 64) - 1
_CHUNK = 1000
_PASTE_CHARS = 20
_CLIPBOARD_LINE = "clipboard text with an accent é and a wide character 日 \n"
_ATOMIC_TYPES = (str, bytes, int, float, bool, type(None))


class Lcg:
    """Tiny deterministic generator for positions."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK64

    def below(self, bound: int) -> int:
        """Return an integer in `[0, bound)`."""
        self._state = (self._state * _LCG_MUL + _LCG_ADD) & _MASK64
        return (self._state >> 33) % bound


def empty_table(count: int) -> PieceTable:
    """Return a table over the original of `count` rows of four letters (scanned), still one piece."""
    source = BytesSource(_ROW * count)
    index = LineIndex(source, stride=64)
    index.scan_now()
    return PieceTable(source, index, AddStore())


def fill_table(table: PieceTable, count: int) -> None:
    """Replace the table by `count` pieces of three letters and a line feed, one per row, with a gap of one byte before each so that no two pieces merge (chunks of 1,000 pieces)."""
    width = len(_ROW)
    for first in range(0, count, _CHUNK):
        last = min(first + _CHUNK, count)
        pieces = [Piece(0, width * i + 1, width * (i + 1), 1, i, False, False) for i in range(first, last)]
        content = Content.from_pieces(pieces, 0)
        if first == 0:
            table.splice(0, table.length, content)
        else:
            table.splice(table.length, table.length, content)


def build_table(count: int) -> PieceTable:
    """Return a table of `count` pieces."""
    table = empty_table(count)
    fill_table(table, count)
    return table


def _memory_row(spec: Spec, method: str, count: int) -> Row:
    """Bytes per piece of a table of `count` pieces: the growth of traced Python memory (`tracemalloc`) or of `RssAnon` (`rss`) over filling the table."""
    table = empty_table(count)
    gc.collect()
    fields: Row
    if method == "tracemalloc":
        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        fill_table(table, count)
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        fields = {"bytes_per_piece_tracemalloc": (current - base) / count, "peak_bytes_per_piece_tracemalloc": (peak - base) / count}
    else:
        before = read_mem().rss_anon_kb
        fill_table(table, count)
        gc.collect()
        fields = {"bytes_per_piece_rss": (read_mem().rss_anon_kb - before) * 1024 / count}
    return base_row(spec, case="pieces", state=f"pieces={count}", op="memory", pieces=table.tree.piece_count, method=method, **fields)


def _cost_rows(spec: Spec, count: int, calls: int) -> list[Row]:
    """Time `calls` splices (insert and delete of one byte inside a piece) and `calls` row lookups on a table of `count` pieces."""
    table = build_table(count)
    rng = Lcg(count)
    length = table.length
    rows: list[Row] = []
    state = f"pieces={count}"
    nothing = Content.from_pieces((), 0)
    for k in range(calls):
        position = 4 * rng.below(count) + 2
        added = table.add(b"x")
        started = time.perf_counter_ns()
        table.splice(position, position, added)
        inserted_ms = (time.perf_counter_ns() - started) / 1e6
        started = time.perf_counter_ns()
        table.splice(position, position + 1, nothing)
        deleted_ms = (time.perf_counter_ns() - started) / 1e6
        rows.append(base_row(spec, case="pieces", state=state, op="splice_insert", step=k, splice_ms=inserted_ms, pieces=table.tree.piece_count))
        rows.append(base_row(spec, case="pieces", state=state, op="splice_delete", step=k, splice_ms=deleted_ms, pieces=table.tree.piece_count))
    rows_total = count
    for k in range(calls):
        row = rng.below(rows_total)
        started = time.perf_counter_ns()
        found = table.row_range(row)
        elapsed = (time.perf_counter_ns() - started) / 1e6
        rows.append(base_row(spec, case="pieces", state=state, op="row_range", step=k, row_range_ms=elapsed, resolved=found is not None))
    assert table.length == length
    return rows


async def pieces_scenario(spec: Spec) -> list[Row]:
    """`pieces` child: `method` `cost` (splice and row_range at every size of `sizes`), `tracemalloc` or `rss` (bytes per piece at `memory_size`)."""
    method = str(spec["method"])
    if method == "cost":
        rows: list[Row] = []
        for count in spec["sizes"]:
            rows += _cost_rows(spec, int(count), int(spec["calls"]))
        return rows
    return [_memory_row(spec, method, int(spec["memory_size"]))]


def deep_size(root: object) -> int:
    """Bytes of every object reachable from `root` (each object once, by identity), following containers, dicts, `__dict__` and `__slots__`.

    Shared objects, such as the small integers of the interpreter, are counted once for the whole graph.
    """
    seen: set[int] = set()
    stack: list[object] = [root]
    total = 0
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, type | types.FunctionType | types.ModuleType | types.MethodType):
            continue
        seen.add(id(obj))
        total += sys.getsizeof(obj)
        if isinstance(obj, _ATOMIC_TYPES):
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, list | tuple | set | frozenset | deque):
            stack.extend(obj)
        else:
            attributes = getattr(obj, "__dict__", None)
            if attributes is not None:
                stack.append(attributes)
            for cls in type(obj).__mro__:
                slots = getattr(cls, "__slots__", ())
                stack.extend(getattr(obj, name) for name in ((slots,) if isinstance(slots, str) else slots) if hasattr(obj, name))
    return total


def _records(area: ProbeTextArea) -> tuple[int, int, int]:
    """Return `(batches, records, deep bytes)` of the undo stack."""
    stack = list(area.history._undo_stack)
    return len(stack), sum(len(batch) for batch in stack), deep_size(stack)


def _undo_row(spec: Spec, area: ProbeTextArea, kind: str, operations: int, traced: int) -> Row:
    batches, records, deep = _records(area)
    return base_row(
        spec,
        case="undo-record",
        state=kind,
        op=kind,
        operations=operations,
        batches=batches,
        records=records,
        bytes_per_record=deep / max(1, records),
        bytes_per_record_tracemalloc=traced / max(1, records),
        bytes_per_operation=deep / max(1, operations),
        pieces=piece_count(area),
    )


async def undo_record_scenario(spec: Spec) -> list[Row]:
    """`undo-record` child: bytes per undo record after `count` typed characters, Backspace presses, pastes and deletes.

    `bytes_per_record` is the deep size of the undo stack divided by the records (the `Edit` objects kept after coalescing);
    `bytes_per_record_tracemalloc` is the retained growth of traced memory over the same operations, which also contains the add store bytes and any cache that grew.
    """
    app, area = _open(spec)
    count = int(spec["count"])
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        document = lazy_document(area)
        if document is None:
            msg = "the file did not open lazily"
            raise RuntimeError(msg)
        rows_available = max(1, document.line_count - 1)
        tracemalloc.start()

        async def measure(kind: str, operations: int, work: Any) -> None:
            area.history.clear()
            gc.collect()
            base = tracemalloc.get_traced_memory()[0]
            work()
            await settle(0.05)
            gc.collect()
            rows.append(_undo_row(spec, area, kind, operations, tracemalloc.get_traced_memory()[0] - base))

        def typing() -> None:
            area.move_cursor((0, 0))
            for _ in range(count):
                area.insert("x")

        def backspace() -> None:
            for _ in range(count):
                area.action_delete_left()

        def paste() -> None:
            start = (0, 0)
            end = (0, min(_PASTE_CHARS, document.line_length(0) or 0))
            content = document.selection_content(start, end)
            for k in range(count):
                row = (k * 2 + 1) % rows_available
                area.edit(Edit("", (row, 0), (row, 0), False, insert_content=content))

        def delete() -> None:
            for k in range(count):
                row = (k * 2) % rows_available
                if (document.line_length(row) or 0) > 0:
                    area.delete((row, 0), (row, 1))

        await measure("typing", count, typing)
        await measure("backspace", count, backspace)
        await measure("paste", count, paste)
        await measure("delete", count, delete)
        tracemalloc.stop()
    return rows


def _clipboard_text(size: int) -> str:
    """Text whose UTF-8 encoding is `size` bytes (padded with ASCII)."""
    line = _CLIPBOARD_LINE.encode()
    text = ((line * (size // len(line) + 1))[:size]).decode("utf-8", "ignore")
    return text + "x" * (size - len(text.encode("utf-8")))


class _Drain:
    """A pty whose master side is read by a thread; writing to the slave side is the "terminal write"."""

    def __init__(self) -> None:
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave, termios.TCSANOW)
        self.received = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                data = os.read(self.master, 1 << 20)
            except OSError:
                return
            if not data:
                return
            self.received += len(data)

    def write(self, text: str) -> None:
        """Write `text` to the slave side completely (blocks while the pty buffer is full)."""
        data = text.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(self.slave, view)
            view = view[written:]

    def close(self) -> None:
        """Close the slave (the reader ends) and the master."""
        os.close(self.slave)
        self._thread.join(timeout=5)
        os.close(self.master)


async def clipboard_scenario(spec: Spec) -> list[Row]:
    """`clipboard` child: `calls` calls of `app.copy_to_clipboard` per size, once with the terminal write going to a drained pty and once with a discarded write.

    Op `copy_write` is the whole call; op `copy_only` is the call with the driver write replaced by a no-op (the Textual side alone).
    """
    app, area = _open(spec)
    sizes = [int(size) for size in spec["sizes"]]
    calls = int(spec["calls"])
    rows: list[Row] = []
    drain = _Drain()
    try:
        async with app.run_test(size=SIZE):
            await _first_content(area)
            driver = getattr(app, "_driver", None)
            if driver is None:
                msg = "the app has no driver to take the terminal write"
                raise RuntimeError(msg)
            attributes = vars(driver)
            had_write = "write" in attributes
            original = attributes.get("write")
            for size in sizes:
                text = _clipboard_text(size)
                for op, writer in (("copy_only", lambda _data: None), ("copy_write", drain.write)):
                    attributes["write"] = writer
                    for k in range(calls):
                        started = time.perf_counter_ns()
                        app.copy_to_clipboard(text)
                        elapsed = (time.perf_counter_ns() - started) / 1e6
                        rows.append(base_row(spec, case="clipboard", state=f"{size}", op=op, step=k, clipboard_ms=elapsed, size_bytes=len(text.encode("utf-8"))))
            if had_write:
                attributes["write"] = original
            else:
                attributes.pop("write", None)
    finally:
        drain.close()
    return rows
