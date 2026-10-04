"""Headless app helpers of the view benchmark harness: a probe widget, key injection and timing.

Key injection: a `textual.events.Key` is handed to the app through `app._driver.send_message`, the path a terminal key takes.
The headless driver of `App.run_test` provides that private attribute; when it is missing the event is posted with `app.post_message`.
The private attributes are read through `getattr`, because the harness measures the app from outside.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import sys
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.strip import Strip

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea

SIZE = (120, 30)
"""Terminal size of every headless run (columns, rows)."""
SETTLE = 0.03
"""Seconds between two measured steps, like a person's gap between key presses."""
FAR = 100_000_000
SMALL_FILE_BYTES = 1 << 20
MIN_CONTENT_CHARS = 5
"""A first paint with fewer visible characters is a transient one, not the first content."""
LOWERED = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
"""Thresholds that give a small synthetic file short, medium and long rows (same values as `tests/nova_editor/helpers_view.LOWERED_OPTIONS`)."""


def emit(text: str) -> None:
    """Print a protocol line on the real stdout (Textual replaces `sys.stdout` while an app runs)."""
    stream = sys.__stdout__
    if stream is not None:
        print(text, file=stream, flush=True)


def versions() -> dict[str, str]:
    """Return the versions that identify the software under test."""
    return {"textual": version("textual"), "rich": version("rich")}


def build_config(path: Path, mode: str = "auto", *, yield_seconds: float | None = None, scan_block: int | None = None) -> LazyConfig:
    """Return the lazy configuration for `path`.

    Args:
        path: The file; with `mode="auto"` a file of at most 1 MiB gets the lowered thresholds so that the synthetic rows exercise every path.
        mode: `auto`, `default` (the shipped `LazyConfig`) or `lowered`.
        yield_seconds: Overrides `LazyConfig.yield_seconds`.
        scan_block: Overrides `LazyConfig.scan_block`.
    """
    lowered = mode == "lowered" or (mode == "auto" and path.stat().st_size <= SMALL_FILE_BYTES)
    config = LOWERED if lowered else LazyConfig()
    if yield_seconds is not None:
        config = dataclasses.replace(config, yield_seconds=yield_seconds)
    if scan_block is not None:
        config = dataclasses.replace(config, scan_block=scan_block)
    return config


class ProbeTextArea(NovaTextArea):
    """`NovaTextArea` that counts `render_line` calls and stamps the first one that shows content."""

    render_seq: int = 0
    first_content_ns: int | None = None

    def render_line(self, y: int) -> Strip:
        """Render a line like the base class and record that it happened."""
        strip = super().render_line(y)
        self.render_seq += 1
        if self.first_content_ns is None and len(strip.text.strip()) >= MIN_CONTENT_CHARS:
            self.first_content_ns = time.perf_counter_ns()
        return strip


SEGMENT_NAMES = ("reestimate", "reconcile", "measure")


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

    def since(self, before: tuple[dict[str, float], dict[str, int]]) -> dict[str, Any]:
        """The row fields `<name>_ms` and `<name>_calls` for the time accumulated since `before`."""
        seconds, calls = before
        fields: dict[str, Any] = {}
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

    def since(self, before: int) -> dict[str, Any]:
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


class ProbeEditor(ProbeTextArea, TimedNovaTextArea):
    """The editor class of the real `NovaEditApp` with the render counting of `ProbeTextArea` (the `app-latency` probe)."""


def open_probe(
    path: Path,
    *,
    wrap: bool,
    config: LazyConfig | None,
    language: str | None = None,
    highlight_limit: int | None = None,
) -> ProbeTextArea:
    """Open `path` lazily in a `ProbeTextArea` (not mounted yet)."""
    options: dict[str, Any] = {}
    if highlight_limit is not None:
        options["highlight_limit"] = highlight_limit
    area = ProbeTextArea.open(path, soft_wrap=wrap, config=config, language=language, **options)
    if not isinstance(area, ProbeTextArea):
        msg = "NovaTextArea.open did not return the subclass"
        raise TypeError(msg)
    return area


class ProbeApp(App[None]):
    """Minimal app hosting one widget."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


async def wait_until(condition: Callable[[], bool], limit: float, step: float = 0.01) -> bool:
    """Yield to the event loop until `condition()` holds; return False after `limit` seconds."""
    deadline = time.perf_counter() + limit
    while time.perf_counter() < deadline:
        if condition():
            return True
        await asyncio.sleep(step)
    return condition()


def lazy_document(area: NovaTextArea) -> LazyDocument | None:
    """Return the `LazyDocument` behind a lazily opened widget, otherwise None."""
    document = area.document
    return document if isinstance(document, LazyDocument) else None


def scan_busy(area: NovaTextArea) -> bool:
    """Whether the line scan or any long-row scan of the document is running; it creates nothing and changes no cache order."""
    document = lazy_document(area)
    return document is not None and document.is_growing()


def first_row_is_long(area: NovaTextArea) -> bool:
    """Whether row 0 of the lazy document is a long row (needs the first row's range, which is available at first content)."""
    document = lazy_document(area)
    try:
        return document is not None and document.is_long(0)
    except (RowUnavailable, IndexError):
        return False


def send_key(app: App[None], key: str) -> None:
    """Inject a key press: through the driver when the app has one, otherwise as a posted event."""
    event = events.Key(key, key if len(key) == 1 else None)
    event.set_sender(app)
    driver = getattr(app, "_driver", None)
    if driver is not None:
        driver.send_message(event)
    else:
        app.post_message(event)


def _view_state(area: NovaTextArea) -> tuple[object, ...]:
    """The observable position: cursor location, scroll offset, and the state and byte of the cursor machine (read without side effects).

    A step of a provisional cursor changes the byte and the drawn cursor cell, but not `cursor_location`.
    """
    return (area.cursor_location, tuple(area.scroll_offset), *area.peek_cursor_state())


@dataclasses.dataclass(frozen=True)
class Timing:
    """Milliseconds from an action until the visible state changed (`handler_ms`) and until the next `render_line` (`latency_ms`)."""

    handler_ms: float | None
    latency_ms: float | None

    @property
    def changed(self) -> bool:
        """Whether the cursor or the scroll offset changed within the limit."""
        return self.handler_ms is not None


async def timed(
    area: ProbeTextArea,
    action: Callable[[], None],
    *,
    limit: float = 0.5,
    require_change: bool = True,
    state: Callable[[ProbeTextArea], tuple[object, ...]] = _view_state,
) -> Timing:
    """Run `action` and time key-to-render.

    The state change is a new cursor location or scroll offset (or whatever `state` returns; edits add the document length). `latency_ms` is the time until, after the change, the widget's
    `render_line` ran at least once since before the action (the spike rule); with `require_change=False` any `render_line` counts.
    """
    seq0 = area.render_seq
    state0 = state(area)
    t0 = time.perf_counter()
    action()
    handler: float | None = None
    render: float | None = None
    while True:
        now = time.perf_counter()
        if now - t0 >= limit:
            break
        if handler is None and state(area) != state0:
            handler = now
        if (handler is not None or not require_change) and area.render_seq > seq0:
            render = now
            break
        await asyncio.sleep(0)
    return Timing(None if handler is None else (handler - t0) * 1000, None if render is None else (render - t0) * 1000)


async def settle(seconds: float = SETTLE) -> None:
    """Sleep between two measured steps."""
    await asyncio.sleep(seconds)
