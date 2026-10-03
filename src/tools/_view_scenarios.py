"""Scenarios that run inside a headless app process of the view benchmark harness.

Every scenario returns raw JSON rows. `spec` is the dictionary the parent put on the child's command line.
A row carries `case`, `file`, `wrap`, `state`, `op`, `run`, the versions of the software and the metric fields.
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import pstats
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import App
from textual.pilot import Pilot

from tools._view_app import (
    FAR,
    SEGMENT_NAMES,
    SIZE,
    GcTimer,
    ProbeApp,
    ProbeTextArea,
    Timing,
    build_config,
    emit,
    first_row_is_long,
    install_clock,
    lazy_document,
    open_probe,
    scan_busy,
    send_key,
    settle,
    timed,
    versions,
    wait_until,
)

Spec = dict[str, Any]
Row = dict[str, Any]

KEY_OPS = ("down", "up", "pagedown", "pageup", "left", "right", "home", "end", "ctrl+right")
"""Operations that are one key press."""
LATENCY_OPS = (*KEY_OPS, "hscroll", "farjump")
_FAR_OPS = ("left", "right", "home", "end", "ctrl+right", "hscroll")
_REPOSITION_OPS = ("left", "home", "end")
_PAGE_ROWS = 30
_FLOOR_STEPS = 10
_FLOOR_KEY = "f24"
_KEY_TIMEOUT = 0.5
_JUMP_TIMEOUT = 120.0
_PROFILE_TOP = 25
_HSCROLL_STEP = 3
_DOWN_STEPS = 300
_UP_STEPS = 50
_MAX_IDLE = 2
_LEFT_COLUMN = 60
_HOME_COLUMN = 10


def base_row(spec: Spec, *, case: str, state: str, op: str, **extra: object) -> Row:
    """Return a row with the identifying fields of a measurement."""
    return {
        "case": case,
        "file": Path(spec["file"]).name,
        "wrap": spec["wrap"],
        "state": state,
        "op": op,
        "run": spec.get("run", 1),
        **versions(),
        **extra,
    }


def _wrap_on(spec: Spec) -> bool:
    return spec["wrap"] == "on"


def _open(spec: Spec) -> tuple[ProbeApp, ProbeTextArea]:
    path = Path(spec["file"])
    config = build_config(path, spec.get("config", "auto"), yield_seconds=spec.get("yield_seconds"), scan_block=spec.get("scan_block"))
    area = open_probe(path, wrap=_wrap_on(spec), config=config)
    return ProbeApp(area), area


async def _first_content(area: ProbeTextArea, limit: float = 120.0) -> None:
    if not await wait_until(lambda: area.first_content_ns is not None, limit):
        msg = "the widget showed no content"
        raise RuntimeError(msg)


def _far_byte(area: ProbeTextArea, far: int) -> int | None:
    """Return `far` when row 0 is a long row and the file reaches it (the 200 MB single-line file), otherwise None."""
    document = lazy_document(area)
    return far if document is not None and first_row_is_long(area) and document.length > far else None


def _byte_target(area: ProbeTextArea, byte: int) -> int:
    document = lazy_document(area)
    return byte if document is None else max(0, min(byte, document.length - 1))


def _place(area: ProbeTextArea, op: str, steps: int, far_byte: int | None) -> None:
    """Put the cursor where `op` has room to work: at the far column on a long row, otherwise at a fixed row and column."""
    if far_byte is not None and op in _FAR_OPS:
        area.goto_byte(far_byte)
        return
    row = area.cursor_location[0]
    targets = {
        "up": (2 * steps + 5, 0),
        "pageup": (2 * steps * _PAGE_ROWS + _PAGE_ROWS, 0),
        "left": (row, _LEFT_COLUMN),
        "home": (row, _HOME_COLUMN),
        "end": (row, 0),
    }
    area.move_cursor(targets.get(op, (0, 0)))


async def _place_settled(area: ProbeTextArea, op: str, steps: int, far_byte: int | None) -> None:
    """`_place`, then wait until a jump that stayed pending is over (wrap mode: the far column is reachable only once the scan got there).

    Without the wait a step of a far op that changes nothing would be timed until the scan reached the far column and the pending jump moved the cursor,
    which measures the scan and not the key.
    """
    _place(area, op, steps, far_byte)
    await wait_until(lambda: area.pending_progress is None, _JUMP_TIMEOUT)


async def _pilot_step(pilot: Pilot[None], area: ProbeTextArea, op: str, target_x: int) -> float | None:
    if op == "farjump":
        return None
    started = time.perf_counter()
    if op == "hscroll":
        area.scroll_to(x=target_x, animate=False)
    else:
        await pilot.press(op)
    await pilot.pause()
    return (time.perf_counter() - started) * 1000


async def _direct_step(app: App[None], area: ProbeTextArea, op: str, i: int, base_x: int, far_byte: int | None, steps: int) -> Timing:
    """Issue one direct-injection step of `op` and wait for its render."""
    if op in _REPOSITION_OPS:
        await _place_settled(area, op, steps, far_byte)
        await settle()
    if op == "farjump":
        area.goto_byte(0)
        await settle()
        target = _byte_target(area, far_byte if far_byte is not None else FAR)
        return await timed(area, lambda target=target: area.goto_byte(target), limit=_JUMP_TIMEOUT, require_change=False)
    if op == "hscroll":
        x = base_x + _HSCROLL_STEP * (2 * i + 1)
        return await timed(area, lambda x=x: area.scroll_to(x=x, animate=False), limit=_KEY_TIMEOUT)
    return await timed(area, lambda: send_key(app, op), limit=_KEY_TIMEOUT)


async def measure_ops(
    pilot: Pilot[None],
    app: App[None],
    area: ProbeTextArea,
    spec: Spec,
    op: str,
    steps: int,
    *,
    case: str,
    state: str,
    with_pilot: bool,
    place_first: bool = True,
    instrument: bool = False,
    step_fields: Callable[[], Row] | None = None,
    **extra: object,
) -> list[Row]:
    """Time `steps` presses of `op` in two phases.

    Phase `direct`: all steps back to back by direct injection (`latency_ms`, `handler_ms`, `scan_running` before the step, `scan_completed_during_step`), each waiting only for its own render, so
    that a running scan is not over before the burst is. Phase `pilot` (with `with_pilot`): the Pilot method (`pilot_ms`), slow while a scan runs.
    `step_fields`, when given, is called after every direct step and its fields go into that step's row.
    With `instrument` every direct row also carries `gc_ms`, `gc_longest_ms` and `segments` (`reestimate_ms`, `reconcile_ms`, `measure_ms` of the wrapped methods, as the edit rows do).
    """
    far_byte = _far_byte(area, int(spec.get("far", FAR)))
    rows: list[Row] = []
    started_busy = scan_busy(area)
    scan_done_at: int | None = None
    if place_first:
        await _place_settled(area, op, steps, far_byte)
        await settle(0.2)
    base_x = area.scroll_offset.x
    gc_timer = GcTimer()
    clock = install_clock(area) if instrument else None
    if instrument:
        gc_timer.install()
    for i in range(steps):
        busy = scan_busy(area)
        gc_before = gc_timer.snapshot()
        clock_before = None if clock is None else clock.snapshot()
        timing = await _direct_step(app, area, op, i, base_x, far_byte, steps)
        attribution: Row = {}
        if clock is not None and clock_before is not None:
            fields = clock.since(clock_before)
            attribution = {
                **gc_timer.since(gc_before),
                "segments": {f"{name}_ms": fields[f"{name}_ms"] for name in SEGMENT_NAMES},
            }
        completed = busy and not scan_busy(area)
        if started_busy and scan_done_at is None and not scan_busy(area):
            scan_done_at = i
        rows.append(
            base_row(
                spec,
                case=case,
                state=state,
                op=op,
                phase="direct",
                step=i,
                latency_ms=timing.latency_ms,
                handler_ms=timing.handler_ms,
                changed=timing.changed,
                busy_at_step=busy,
                scan_running=busy,
                scan_completed_during_step=completed,
                far_byte=far_byte,
                **attribution,
                **({} if step_fields is None else step_fields()),
                **extra,
            )
        )
    if instrument:
        gc_timer.uninstall()
    for row in rows:
        row["scan_done_at_step"] = scan_done_at
    if with_pilot and op != "farjump":
        await settle(0.2)
        if place_first:
            await _place_settled(area, op, steps, far_byte)
            await settle(0.2)
        base_x = area.scroll_offset.x
        for i in range(steps):
            busy = scan_busy(area)
            if op in _REPOSITION_OPS:
                await _place_settled(area, op, steps, far_byte)
                await settle()
            pilot_ms = await _pilot_step(pilot, area, op, base_x + _HSCROLL_STEP * (2 * i + 1))
            rows.append(base_row(spec, case=case, state=state, op=op, phase="pilot", step=i, pilot_ms=pilot_ms, busy_at_step=busy, far_byte=far_byte, **extra))
            await settle()
    return rows


async def repeat_key(app: App[None], area: ProbeTextArea, key: str, count: int, *, limit: float = 0.5) -> int:
    """Press `key` up to `count` times, stop after two presses in a row that changed nothing (the end of the file); return the presses made."""
    idle = 0
    for made in range(1, count + 1):
        timing = await timed(area, lambda: send_key(app, key), limit=limit)
        idle = 0 if timing.changed else idle + 1
        if idle >= _MAX_IDLE:
            return made
    return count


async def _idle_floor(pilot: Pilot[None], area: ProbeTextArea, spec: Spec, state: str) -> list[Row]:
    """Pilot idle floor: press an unbound key and wait for idle, which costs the same whatever the editor does."""
    rows: list[Row] = []
    for i in range(_FLOOR_STEPS):
        busy = scan_busy(area)
        started = time.perf_counter()
        await pilot.press(_FLOOR_KEY)
        await pilot.pause()
        rows.append(base_row(spec, case="latency", state=state, op=_FLOOR_KEY, phase="pilot", step=i, pilot_ms=(time.perf_counter() - started) * 1000, busy_at_step=busy))
        await settle()
    return rows


def _write_profile(path: Path, run: int, profile: cProfile.Profile, rows: list[Row]) -> None:
    """Append the top 25 functions by cumulative time and the per-step timings of one run."""
    buffer = io.StringIO()
    pstats.Stats(profile, stream=buffer).sort_stats("cumulative").print_stats(_PROFILE_TOP)
    lines = [f"== run {run}: cProfile top {_PROFILE_TOP} by cumulative time (event loop thread only) ==", buffer.getvalue(), "per-step timings (ms):"]
    lines.extend(
        f"phase={row.get('phase')} step={row['step']} op={row['op']} latency_ms={row.get('latency_ms')} handler_ms={row.get('handler_ms')} pilot_ms={row.get('pilot_ms')} busy={row['busy_at_step']}"
        for row in rows
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def latency_steps(pilot: Pilot[None], app: App[None], area: ProbeTextArea, spec: Spec, *, case: str, with_pilot: bool, step_fields: Callable[[], Row] | None = None) -> list[Row]:
    """The steps of `latency` and `app-latency` in an app that showed its first content: wait for the scan when the state is `indexed`, then `measure_ops`.

    `spec["instrument"]` adds the garbage collection and segment attribution to every direct step.
    """
    op, state, steps = spec["op"], spec["state"], int(spec["steps"])
    if state == "indexed":
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        await settle(0.2)
    if _wrap_on(spec) and op == "hscroll":
        return [base_row(spec, case=case, state=state, op=op, not_applicable="soft wrap has no horizontal scroll")]
    profile = cProfile.Profile() if spec.get("profile_out") else None
    if profile is not None:
        profile.enable()
    rows = await measure_ops(pilot, app, area, spec, op, steps, case=case, state=state, with_pilot=with_pilot, instrument=bool(spec.get("instrument", False)), step_fields=step_fields)
    if profile is not None:
        profile.disable()
        _write_profile(Path(spec["profile_out"]), int(spec.get("run", 1)), profile, rows)
    return rows


async def latency_scenario(spec: Spec) -> list[Row]:
    """`latency`: key-to-render per step for one op in the `indexing` or `indexed` state (`spec["pilot"]` false skips the Pilot phase)."""
    app, area = _open(spec)
    async with app.run_test(size=SIZE) as pilot:
        await _first_content(area)
        rows = await latency_steps(pilot, app, area, spec, case="latency", with_pilot=bool(spec.get("pilot", True)))
        if not (_wrap_on(spec) and spec["op"] == "hscroll"):
            rows.extend(await _idle_floor(pilot, area, spec, spec["state"]))
    return rows


async def sweep_scenario(spec: Spec) -> list[Row]:
    """`sweep-yield`: page down then page up while indexing for one (`yield_seconds`, `scan_block`) setting, plus the scan completion time."""
    steps = int(spec["steps"])
    started_ns = time.perf_counter_ns()
    app, area = _open(spec)
    document = lazy_document(area)
    done_ns: list[int] = []

    def note_done() -> None:
        if document is not None and not done_ns and document.snapshot().complete:
            done_ns.append(time.perf_counter_ns())

    if document is not None:
        document.subscribe(note_done)
    extra: dict[str, Any] = {"variant": f"yield={spec['yield_seconds']} block={spec['scan_block']}", "yield_seconds": spec["yield_seconds"], "scan_block": spec["scan_block"]}
    rows: list[Row] = []
    async with app.run_test(size=SIZE) as pilot:
        await _first_content(area)
        rows += await measure_ops(pilot, app, area, spec, "pagedown", steps, case="sweep-yield", state="indexing", with_pilot=False, **extra)
        rows += await measure_ops(pilot, app, area, spec, "pageup", steps, case="sweep-yield", state="indexing", with_pilot=False, place_first=False, **extra)
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        note_done()
        if not done_ns:
            done_ns.append(time.perf_counter_ns())
        rows.append(base_row(spec, case="sweep-yield", state="indexing", op="scan", scan_complete_s=(done_ns[0] - started_ns) / 1e9, **extra))
    return rows


async def memory_scenario(spec: Spec) -> list[Row]:
    """`memory` child: run the scripted scenario and print `PHASE <name> <ns>` after each phase; the parent samples /proc and builds the rows."""
    app, area = _open(spec)
    timeout = float(spec.get("timeout", 300))
    async with app.run_test(size=SIZE):
        await _first_content(area)
        emit(f"PHASE open {time.perf_counter_ns()}")
        await wait_until(lambda: not scan_busy(area), timeout)
        emit(f"PHASE indexing {time.perf_counter_ns()}")
        await repeat_key(app, area, "pagedown", _DOWN_STEPS)
        emit(f"PHASE pagedown {time.perf_counter_ns()}")
        target = _byte_target(area, 1 << 62)
        await timed(area, lambda: area.goto_byte(target), limit=60.0, require_change=False)
        await settle(0.5)
        emit(f"PHASE end {time.perf_counter_ns()}")
        await repeat_key(app, area, "pageup", _UP_STEPS)
        emit(f"PHASE pageup {time.perf_counter_ns()}")
    emit("DONE")
    return []


async def _observe(area: ProbeTextArea, started: float, limit: float) -> list[tuple[str, float]]:
    """Record the cursor state transitions after an action until the cursor is RESOLVED again (or `limit` seconds)."""
    seen: list[tuple[str, float]] = []
    while True:
        name = area.cursor_state.name
        if not seen or seen[-1][0] != name:
            seen.append((name, (time.perf_counter() - started) * 1000))
        if name == "RESOLVED" or time.perf_counter() - started > limit:
            return seen
        await asyncio.sleep(0.005)


async def _jump_once(app: ProbeApp, area: ProbeTextArea, spec: Spec, op: str, state: str, column: int) -> Row:
    area.goto_byte(0)
    await settle(0.2)
    scan_running = scan_busy(area)
    started = time.perf_counter()
    if op == "end":
        timing = await timed(area, lambda: send_key(app, "end"), limit=_JUMP_TIMEOUT, require_change=False)
    else:
        target = _byte_target(area, column)
        timing = await timed(area, lambda: area.goto_byte(target), limit=_JUMP_TIMEOUT, require_change=False)
    transitions = await _observe(area, started, float(spec.get("resolve_timeout", 120)))
    provisional = next((ms for name, ms in transitions if name == "PROVISIONAL"), None)
    resolved = next((ms for name, ms in transitions if name == "RESOLVED" and len(transitions) > 1), None)
    return base_row(
        spec,
        case="jump",
        state=state,
        op=op,
        repaint_ms=timing.latency_ms,
        provisional_ms=provisional,
        resolved_after_ms=resolved,
        transitions=[[name, round(ms, 3)] for name, ms in transitions],
        scan_running_at_op=scan_running,
        column=column,
    )


async def jump_scenario(spec: Spec) -> list[Row]:
    """`jump`: End and a far jump before and after the scan completed; records the repaint time and the cursor state transitions."""
    column = int(spec["column"])
    app, area = _open(spec)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        rows.extend([await _jump_once(app, area, spec, op, "before_scan", column) for op in ("end", "farjump")])
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        await settle(0.2)
        rows.extend([await _jump_once(app, area, spec, op, "after_scan", column) for op in ("end", "farjump")])
    return rows


async def calllog_scenario(spec: Spec) -> list[Row]:
    """`calllog`: open and scroll through a fixed script; `ok` is False when a call decoded more than 8192 characters or a whole-row access was refused."""
    app, area = _open(spec)
    document = lazy_document(area)
    if document is None:
        msg = "the file did not open lazily"
        raise RuntimeError(msg)
    script: list[tuple[str, int]] = [("pagedown", 30), ("end", 1), ("pageup", 10), ("home", 1), ("right", 5), ("ctrl+right", 3), ("down", 5), ("up", 5)]
    async with app.run_test(size=SIZE):
        await _first_content(area)
        for key, count in script:
            await repeat_key(app, area, key, count)
        target = _byte_target(area, 1 << 62)
        await timed(area, lambda: area.goto_byte(target), limit=60.0, require_change=False)
        await timed(area, lambda: area.goto_byte(target // 2), limit=60.0, require_change=False)
        for key in ("right", "end", "home", "ctrl+right"):
            await repeat_key(app, area, key, 1)
        area.toggle_wrap()
        await settle(0.2)
        for key, count in (("pagedown", 10), ("pageup", 5), ("down", 3)):
            await repeat_key(app, area, key, count)
        area.toggle_wrap()
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        await repeat_key(app, area, "pagedown", 10)
    log = document.call_log
    by_method: dict[str, int] = {}
    for record in list(log.events):
        by_method[record.method] = by_method.get(record.method, 0) + 1
    limit = int(spec.get("limit", 8192))
    ok = log.max_decoded_chars <= limit and not log.refusals
    return [
        base_row(
            spec,
            case="calllog",
            state="script",
            op="script",
            ok=ok,
            max_decoded_chars=log.max_decoded_chars,
            limit=limit,
            calls=len(log.events),
            refusals=len(log.refusals),
            refusal_samples=[list(item) for item in log.refusals[:5]],
            by_method=by_method,
        )
    ]
