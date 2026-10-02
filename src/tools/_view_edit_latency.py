"""Edit latency scenarios of the view benchmark harness: typing, Backspace, Delete and Enter by direct injection, scattered earlier edits, segment times.

A key is injected like a terminal key (`tools._view_app.send_key`); `latency_ms` is the time until the widget's next `render_line` after the cursor, the scroll offset
or the document length changed. Rows carry `case`, `file`, `wrap`, `state`, `op`, `run`, `phase` (always `direct`), `step`, `latency_ms`, `handler_ms`, `changed`,
`scan_running`, `doc_delta` (bytes the step added to the document), `pieces`, `rss_anon_kb` (`RssAnon` of the process after the step), `gc_ms` and `gc_longest_ms`
(garbage collection time in the step, including its `gap` pause, in total and the longest single collection) and `place` (`end`, `far` or `start`).
The scatter rows carry `rss_anon_kb`, `gc_ms` and `gc_longest_ms` too.

`segments` wraps `NovaTextArea._reestimate`, `_reconcile_cursor` and `LazyWrappedDocument._measure` of the running widget with `perf_counter` accumulators.
The wrappers are installed from the harness on the instances; nothing in `nova_editor` changes.
`_measure` runs inside `_reestimate` when sizes are refreshed, so the three times overlap and are not to be added.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable
from typing import Any

from nova_editor.document._document import Location
from nova_editor.document._lazy_document import LazyDocument
from tools._view_app import FAR, SIZE, ProbeApp, ProbeTextArea, scan_busy, send_key, settle, timed, wait_until
from tools._view_edit import edit_state, is_longline, piece_count, place_at
from tools._view_procmem import read_mem
from tools._view_scenarios import Row, Spec, _first_content, _open, _wrap_on, base_row, lazy_document

EDIT_OPS = ("type", "backspace", "delete", "enter")
"""Operations that change the document, one key each."""
CURSOR_OPS = ("right", "left")
"""Cursor keys that are also measured over an edited row."""
OP_KEYS = {
    "type": "x",
    "backspace": "backspace",
    "delete": "delete",
    "enter": "enter",
    "right": "right",
    "left": "left",
    "home": "home",
    "end": "end",
    "ctrl+right": "ctrl+right",
    "down": "down",
    "up": "up",
}
ALL_OPS = tuple(OP_KEYS)
EDIT_LIMIT = 3.0
"""Seconds a step may take before it is recorded without time."""
MAX_IDLE = 5
"""Steps in a row that changed nothing (a refused edit, the end of the row) after which an op stops."""
SEGMENT_NAMES = ("reestimate", "reconcile", "measure")
_SEGMENT_GAP = 0.12
_RESOLVE_LIMIT = 120.0
_START_COLUMN = 10
_START_ROW = 20


class SegmentClock:
    """`perf_counter` accumulators of wrapped methods: seconds and calls per name."""

    def __init__(self) -> None:
        self.seconds = dict.fromkeys(SEGMENT_NAMES, 0.0)
        self.calls = dict.fromkeys(SEGMENT_NAMES, 0)

    def wrap[**P, R](self, name: str, function: Callable[P, R]) -> Callable[P, R]:
        """Return `function` with its time added to `name`."""

        def timed_call(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.seconds[name] += time.perf_counter() - started
                self.calls[name] += 1

        return timed_call

    def snapshot(self) -> tuple[dict[str, float], dict[str, int]]:
        """Copy of the accumulators."""
        return dict(self.seconds), dict(self.calls)

    def since(self, before: tuple[dict[str, float], dict[str, int]]) -> Row:
        """The row fields `<name>_ms` and `<name>_calls` for the time accumulated since `before`."""
        seconds, calls = before
        fields: Row = {}
        for name in SEGMENT_NAMES:
            fields[f"{name}_ms"] = (self.seconds[name] - seconds[name]) * 1000
            fields[f"{name}_calls"] = self.calls[name] - calls[name]
        return fields


class GcTimer:
    """Duration of every garbage collection between `install` and `uninstall`, to tell whether a slow step coincided with one (`gc.callbacks`)."""

    def __init__(self) -> None:
        self._durations: list[float] = []
        self._started = 0.0

    def install(self) -> None:
        """Start timing collections."""
        gc.callbacks.append(self._on_gc)

    def uninstall(self) -> None:
        """Stop timing and forget the recorded durations."""
        if self._on_gc in gc.callbacks:
            gc.callbacks.remove(self._on_gc)
        self._durations.clear()

    def _on_gc(self, phase: str, _info: dict[str, int]) -> None:
        if phase == "start":
            self._started = time.perf_counter()
        else:
            self._durations.append(time.perf_counter() - self._started)

    def snapshot(self) -> int:
        """The number of collections so far."""
        return len(self._durations)

    def since(self, before: int) -> Row:
        """`gc_ms` (total) and `gc_longest_ms` (the longest single collection) of the collections since `before`."""
        durations = self._durations[before:]
        return {"gc_ms": sum(durations) * 1000, "gc_longest_ms": max(durations, default=0.0) * 1000}


def install_clock(area: ProbeTextArea) -> SegmentClock:
    """Wrap `_reestimate`, `_reconcile_cursor` and the `_measure` of the wrapped document of `area` (instance attributes, no class is changed)."""
    clock = SegmentClock()
    vars(area)["_reestimate"] = clock.wrap("reestimate", area._reestimate)
    vars(area)["_reconcile_cursor"] = clock.wrap("reconcile", area._reconcile_cursor)
    wrapped = area.wrapped_document
    vars(wrapped)["_measure"] = clock.wrap("measure", wrapped._measure)
    return clock


async def place_cursor(area: ProbeTextArea, spec: Spec, *, longline: bool, indexed: bool) -> str:
    """Put the cursor where the steps run: the end of the document, the far column of the long row, or (while indexing) near the start where rows are scanned."""
    document = lazy_document(area)
    if document is None:
        msg = "the file did not open lazily"
        raise RuntimeError(msg)
    if not indexed:
        area.move_cursor((0, _START_COLUMN) if longline else (min(_START_ROW, document.line_count - 1), 0))
        place = "start"
    elif longline:
        area.goto_byte(min(int(spec.get("far", FAR)), document.length - 1))
        await wait_until(lambda: area.pending_progress is None and area.cursor_state.name == "RESOLVED", _RESOLVE_LIMIT)
        place = "far"
    else:
        area.move_cursor(document.end)
        place = "end"
    await settle(0.5)
    return place


async def prepare_op(area: ProbeTextArea, op: str, steps: int) -> None:
    """Untimed set-up: Backspace and Delete need typed text to remove (inserted at the cursor, so the cursor ends after it; Delete goes back before it)."""
    if op not in ("backspace", "delete"):
        return
    start = area.cursor_location
    area.insert("x" * (steps + MAX_IDLE))
    await settle(0.2)
    if op == "delete":
        area.move_cursor(start)
        await settle(0.2)


async def edit_steps(
    app: ProbeApp,
    area: ProbeTextArea,
    spec: Spec,
    op: str,
    steps: int,
    *,
    case: str,
    clock: SegmentClock | None = None,
    gap: float = 0.03,
    **extra: object,
) -> list[Row]:
    """Press the key of `op` up to `steps` times, one direct-injection step after the other, and return one row per step.

    With a `clock` each row also carries the time of the three wrapped methods between the key press and the end of the `gap` pause after its render.
    An op stops after `MAX_IDLE` steps in a row that changed nothing; the last row then has `aborted` set.
    """
    document = lazy_document(area)
    rows: list[Row] = []
    idle = 0
    gc_timer = GcTimer()
    gc_timer.install()
    try:
        for i in range(steps):
            row = await _edit_step(app, area, spec, op, i, document, gc_timer, case=case, clock=clock, gap=gap, **extra)
            rows.append(row)
            idle = 0 if row["changed"] else idle + 1
            if idle >= MAX_IDLE:
                row["aborted"] = True
                break
    finally:
        gc_timer.uninstall()
    return rows


async def _edit_step(
    app: ProbeApp,
    area: ProbeTextArea,
    spec: Spec,
    op: str,
    i: int,
    document: LazyDocument | None,
    gc_timer: GcTimer,
    *,
    case: str,
    clock: SegmentClock | None,
    gap: float,
    **extra: object,
) -> Row:
    """One key press of `edit_steps` and its row."""
    busy = scan_busy(area)
    length_before = 0 if document is None else document.length
    before = None if clock is None else clock.snapshot()
    gc_before = gc_timer.snapshot()
    timing = await timed(area, lambda: send_key(app, OP_KEYS[op]), limit=EDIT_LIMIT, state=edit_state)
    await settle(gap)
    segments = {} if clock is None or before is None else clock.since(before)
    collections = gc_timer.since(gc_before)
    row = base_row(
        spec,
        case=case,
        state=spec.get("state", "indexed"),
        op=op,
        phase="direct",
        step=i,
        latency_ms=timing.latency_ms,
        handler_ms=timing.handler_ms,
        changed=timing.changed,
        scan_running=busy,
        doc_delta=0 if document is None else document.length - length_before,
        pieces=piece_count(area),
        rss_anon_kb=read_mem().rss_anon_kb,
        **collections,
        **segments,
        **extra,
    )
    return row


async def scatter_edits(area: ProbeTextArea, spec: Spec, count: int, *, longline: bool) -> list[Row]:
    """Insert one character at `count` places spread over the document (from the last place to the first, so earlier columns stay valid).

    The document must be indexed. One row per edit (`action_ms`: the synchronous cost of the edit including the re-wrap).
    """
    document = lazy_document(area)
    if document is None:
        msg = "the file did not open lazily"
        raise RuntimeError(msg)
    length = document.length
    spots: set[Location] = set()
    for k in range(count):
        location, _byte = place_at(document, (k + 1) * length // (count + 1))
        spots.add(location)
    rows: list[Row] = []
    gc_timer = GcTimer()
    gc_timer.install()
    for k, location in enumerate(sorted(spots, reverse=True)):
        gc_before = gc_timer.snapshot()
        started = time.perf_counter()
        area.insert("e", location)
        elapsed = time.perf_counter() - started
        rows.append(
            base_row(
                spec,
                case="edit-scatter",
                state="build",
                op="scatter",
                variant=f"scatter-{count}",
                step=k,
                action_ms=elapsed * 1000,
                pieces=piece_count(area),
                rss_anon_kb=read_mem().rss_anon_kb,
                **gc_timer.since(gc_before),
                longline=longline,
            )
        )
    gc_timer.uninstall()
    await settle(0.5)
    return rows


async def _run_ops(
    app: ProbeApp,
    area: ProbeTextArea,
    spec: Spec,
    ops: list[str],
    *,
    longline: bool,
    indexed: bool,
    case: str,
    clock: SegmentClock | None = None,
    gap: float = 0.03,
    **extra: object,
) -> list[Row]:
    rows: list[Row] = []
    steps = int(spec["steps"])
    for op in ops:
        place = await place_cursor(area, spec, longline=longline, indexed=indexed)
        await prepare_op(area, op, steps)
        rows += await edit_steps(app, area, spec, op, steps, case=case, clock=clock, gap=gap, place=place, **extra)
    return rows


async def edit_latency_scenario(spec: Spec) -> list[Row]:
    """`edit-latency` and `edit-scatter` child: the ops of `spec["ops"]` in one session, after indexing or while the scan runs.

    With `scatter` above 0 the document is indexed first and `scatter` single character edits are made before the steps (case `edit-scatter`).
    """
    app, area = _open(spec)
    scatter = int(spec.get("scatter", 0))
    indexed = spec.get("state", "indexed") == "indexed" or scatter > 0
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        if indexed:
            await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
            await settle(0.2)
        longline = is_longline(spec, area)
        extra: dict[str, Any] = {}
        if scatter:
            rows += await scatter_edits(area, spec, scatter, longline=longline)
            extra["variant"] = f"scatter-{scatter}"
        rows += await _run_ops(app, area, spec, list(spec["ops"]), longline=longline, indexed=indexed, case="edit-scatter" if scatter else "edit-latency", **extra)
    return rows


async def segments_scenario(spec: Spec) -> list[Row]:
    """`segments` child: per-step times of `_reestimate`, `_reconcile_cursor` and `_measure` without edits, after typing and after `scatter` earlier edits.

    Variant `clean`: the cursor ops, `typed`: the edit ops, `scattered`: both after the scattered edits. Wrap mode is meant to be on (the DEC-22 matrix).
    """
    app, area = _open(spec)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        await settle(0.2)
        longline = is_longline(spec, area)
        clock = install_clock(area)
        cursor_ops = list(spec["cursor_ops"])
        edit_ops = list(spec["ops"])
        common: dict[str, Any] = {"long_row": longline, "wrap_on": _wrap_on(spec)}
        rows += await _run_ops(app, area, spec, cursor_ops, longline=longline, indexed=True, case="segments", clock=clock, gap=_SEGMENT_GAP, variant="clean", **common)
        rows += await _run_ops(app, area, spec, edit_ops, longline=longline, indexed=True, case="segments", clock=clock, gap=_SEGMENT_GAP, variant="typed", **common)
        scatter = int(spec.get("scatter", 0))
        if scatter:
            rows += await scatter_edits(area, spec, scatter, longline=longline)
            rows += await _run_ops(app, area, spec, [*cursor_ops, *edit_ops], longline=longline, indexed=True, case="segments", clock=clock, gap=_SEGMENT_GAP, variant=f"scattered-{scatter}", **common)
    return rows
