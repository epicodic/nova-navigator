"""ACT7 final scenarios of the view benchmark harness: first and last row, the real app, wrap blocks and reload.

Every scenario returns raw JSON rows built by `base_row`; `spec` is the dictionary the parent put on the child's command line.
`first-end`, `wrap-blocks` and `reload` run the probe widget alone; `app-latency` runs the real `NovaEditApp` (status line, footer, bars) with a probe editor.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from nova_editor.app import NovaEditApp
from nova_editor.document._lazy_wrapped_document import BLOCK_ROWS
from nova_editor.status_line import StatusLine
from tools._view_app import SIZE, ProbeEditor, build_config, lazy_document, scan_busy, settle, timed, wait_until
from tools._view_procmem import KIB_PER_MIB, drop_cache, median, read_mem
from tools._view_scenarios import Row, Spec, _first_content, _open, base_row, latency_steps

_FIRST_END_LIMIT = 30.0
"""Seconds a first-end step may take before it is recorded without time."""
_DEFAULT_CALLS = 20


async def first_end_scenario(spec: Spec) -> list[Row]:
    """`first-end`: a fresh process; wait for the scan; then ctrl+end, ctrl+home, ctrl+end, goto the middle, goto the last line, each timed to the next render.

    The widget has no Ctrl+End or Ctrl+Home binding; as in `save-latency` the two jumps are `move_cursor` calls to the end and the start of the document.
    """
    app, area = _open(spec)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        await settle(0.5)
        document = lazy_document(area)
        if document is None:
            msg = "the file did not open lazily"
            raise RuntimeError(msg)
        steps: list[tuple[str, Callable[[], None]]] = [
            ("ctrl+end", lambda: area.move_cursor(document.end)),
            ("ctrl+home", lambda: area.move_cursor((0, 0))),
            ("ctrl+end", lambda: area.move_cursor(document.end)),
            ("goto_middle", lambda: area.goto_line(max(1, area.line_count // 2))),
            ("goto_last", lambda: area.goto_line(area.line_count)),
        ]
        for order, (name, action) in enumerate(steps):
            timing = await timed(area, action, limit=_FIRST_END_LIMIT, require_change=False)
            wrapped = getattr(area, "wrapped_document", None)
            blocks = len(getattr(wrapped, "_blocks", ())) if spec["wrap"] == "on" else None
            started = time.perf_counter()
            byte_offset = area.cursor_byte_offset  # the status line reads it on its timer; its cost is recorded beside the step (design 4.2: target under 2 ms)
            byte_offset_ms = (time.perf_counter() - started) * 1000
            rows.append(
                base_row(
                    spec,
                    case="first-end",
                    state="indexed",
                    op=name,
                    order=order,
                    latency_ms=timing.latency_ms,
                    handler_ms=timing.handler_ms,
                    measured_blocks=blocks,
                    byte_offset=byte_offset,
                    byte_offset_ms=byte_offset_ms,
                )
            )
            await settle(0.3)
    return rows


async def wrap_blocks_scenario(spec: Spec) -> list[Row]:
    """`wrap-blocks`: time of `wrap_range` and `RssAnon` with N measured blocks, wrap on."""
    app, area = _open({**spec, "wrap": "on"})
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        wrapped = area.wrapped_document
        blocks = getattr(wrapped, "_blocks", {})
        total_blocks = area.line_count // BLOCK_ROWS
        next_block = 0
        for target in spec["blocks"]:
            while len(blocks) < target and next_block < total_blocks:
                wrapped.y_of_row(next_block * BLOCK_ROWS)
                next_block += 1
            start = (min(area.line_count - 1, (total_blocks + 1) * BLOCK_ROWS), 0)  # past every measured block, so none is dropped
            times = []
            for _ in range(int(spec.get("calls", _DEFAULT_CALLS))):
                started = time.perf_counter()
                wrapped.wrap_range(start, start, start)
                times.append((time.perf_counter() - started) * 1000)
            rows.append(
                base_row(
                    spec,
                    case="wrap-blocks",
                    state=f"blocks={target}",
                    op="wrap_range",
                    blocks_target=target,
                    blocks=len(blocks),
                    wrap_range_ms=median(times),
                    wrap_range_max_ms=max(times),
                    rss_anon_mib=read_mem().rss_anon_kb / KIB_PER_MIB,
                )
            )
    return rows


async def reload_scenario(spec: Spec) -> list[Row]:
    """`reload`: time of `reload()` on the UI thread, warm and after dropping the page cache of the file."""
    app, area = _open(spec)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        await _first_content(area)
        await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
        for state in ("warm", "cold"):
            for repeat in range(int(spec.get("repeats", 3))):
                residency = drop_cache(Path(spec["file"])) if state == "cold" else None
                started = time.perf_counter()
                ok = area.reload()
                elapsed = (time.perf_counter() - started) * 1000
                rows.append(base_row(spec, case="reload", state=state, op="reload", repeat=repeat, reload_ms=elapsed, ok=ok, residency=residency))
                await _first_content(area)
                await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
                await settle(0.5)
    return rows


async def app_latency_scenario(spec: Spec) -> list[Row]:
    """`app-latency`: the real `NovaEditApp` (status line, footer, bars) with a probe editor; the same operations as `latency`, plus the status line flushes per step."""
    path = Path(spec["file"])
    config = build_config(path, spec.get("config", "auto"), yield_seconds=spec.get("yield_seconds"), scan_block=spec.get("scan_block"))
    app = NovaEditApp(path, soft_wrap=spec["wrap"] == "on", config=config, editor_class=ProbeEditor)
    async with app.run_test(size=SIZE) as pilot:
        area = app.editor
        if not isinstance(area, ProbeEditor):
            msg = "the app did not create the probe editor"
            raise TypeError(msg)
        status = app.query_one(StatusLine)
        last = [status.flushes]

        def flushes() -> Row:
            """The flush count at the end of a step and the flushes since the previous step."""
            now = status.flushes
            fields = {"status_flushes": now, "status_flushes_step": now - last[0]}
            last[0] = now
            return fields

        await _first_content(area)
        return await latency_steps(pilot, app, area, spec, case="app-latency", with_pilot=False, step_fields=flushes)
