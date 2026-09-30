"""`thresholds` subcommand of the view benchmark harness: synthetic files and rows that size the highlight, word wrap, long row and eager limits.

The files are generated in a temporary directory. All measurements run in the calling process on a headless app (no pty).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from textual._wrap import compute_wrap_offsets
from textual.geometry import Region

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.widget import LanguageDoesNotExist
from tools._view_app import SIZE, ProbeApp, ProbeTextArea, build_config, open_probe, send_key, settle, timed, wait_until
from tools._view_procmem import median
from tools._view_scenarios import Row, Spec, base_row

HUGE = 1 << 60
KINDS = ("highlight", "wordwrap", "longrow", "eager")
_WRAP_REPEATS = 20
_RENDER_REPEATS = 20
_KEY_STEPS = 20
_FIRST_CONTENT_LIMIT = 120.0
_SETTLE_LIMIT = 10.0
_PYTHON_SNIPPET = (
    "def compute(values: list[int], scale: float = 1.0) -> dict[str, float]:\n"
    '    """Return simple statistics of the values."""\n'
    "    total = sum(values) * scale\n"
    "    if not values:\n"
    "        return {'total': 0.0, 'mean': 0.0}\n"
    "    return {'total': total, 'mean': total / len(values)}\n"
    "\n"
    "\n"
)
_WORDS = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
_TEXT_LINE = "the quick brown fox jumps over the lazy dog 0123456789 and once more\n"


def _repeat_to(unit: str, size: int) -> str:
    """Return `unit` repeated and cut to exactly `size` characters (ASCII)."""
    return (unit * (size // len(unit) + 1))[:size]


def write_python_source(path: Path, size: int) -> Path:
    """Write Python source of about `size` bytes (whole lines)."""
    path.write_text(_repeat_to(_PYTHON_SNIPPET, size).rsplit("\n", 1)[0] + "\n", encoding="utf-8")
    return path


def write_single_row(path: Path, size: int) -> Path:
    """Write one row of `size` bytes made of words, followed by a newline."""
    path.write_text(_repeat_to(_WORDS, size) + "\n", encoding="utf-8")
    return path


def write_plain_text(path: Path, size: int) -> Path:
    """Write a plain text file of about `size` bytes (whole lines of 66 bytes)."""
    path.write_text(_repeat_to(_TEXT_LINE, size).rsplit("\n", 1)[0] + "\n", encoding="utf-8")
    return path


def _spec(path: Path, run: int) -> Spec:
    return {"file": str(path), "wrap": "off", "run": run}


async def _highlight_one(path: Path, size: int, run: int) -> list[Row]:
    spec = _spec(path, run)
    started = time.perf_counter_ns()
    try:
        area = open_probe(path, wrap=False, config=build_config(path, "default"), language="python", highlight_limit=HUGE)
    except LanguageDoesNotExist as error:
        return [base_row(spec, case="threshold-highlight", state=str(size), op="first_screen", size=size, error=str(error))]
    app = ProbeApp(area)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        shown = await wait_until(lambda: area.first_content_ns is not None, _FIRST_CONTENT_LIMIT)
        first = None if area.first_content_ns is None else (area.first_content_ns - started) / 1e6
        rows.append(base_row(spec, case="threshold-highlight", state=str(size), op="first_screen", size=size, first_screen_ms=first, shown=shown, highlight_active=area.highlight_active))
        await settle(0.2)
        for step in range(_KEY_STEPS):
            timing = await timed(area, lambda: send_key(app, "down"), limit=10.0)
            rows.append(base_row(spec, case="threshold-highlight", state=str(size), op="down", size=size, step=step, latency_ms=timing.latency_ms, changed=timing.changed))
            await settle()
    return rows


def wrap_time_ms(row_bytes: int, width: int) -> float:
    """Return the median (of 20) time in milliseconds of `compute_wrap_offsets` on one row of `row_bytes` bytes of words."""
    text = _repeat_to(_WORDS, row_bytes)
    samples: list[float] = []
    for _ in range(_WRAP_REPEATS):
        started = time.perf_counter()
        compute_wrap_offsets(text, width, 4)
        samples.append((time.perf_counter() - started) * 1000)
    return median(samples)


async def _longrow_one(path: Path, size: int, variant: str, run: int) -> list[Row]:
    spec = _spec(path, run)
    config = LazyConfig(long_row_threshold=HUGE, word_wrap_limit=HUGE) if variant == "whole" else LazyConfig(long_row_threshold=1024, word_wrap_limit=1024, index_long_line_threshold=1024)
    started = time.perf_counter_ns()
    area = open_probe(path, wrap=False, config=config)
    app = ProbeApp(area)
    rows: list[Row] = []
    async with app.run_test(size=SIZE):
        shown = await wait_until(lambda: area.first_content_ns is not None, _FIRST_CONTENT_LIMIT)
        first = None if area.first_content_ns is None else (area.first_content_ns - started) / 1e6
        rows.append(base_row(spec, case="threshold-longrow", state=str(size), op=variant, size=size, first_screen_ms=first, shown=shown))
        await wait_until(lambda: not area.is_estimating, _SETTLE_LIMIT)
        for i in range(_RENDER_REPEATS):
            area._line_cache.clear()
            area.scroll_to(x=7 * i, animate=False)
            began = time.perf_counter()
            area.render_lines(Region(0, 0, area.size.width, area.size.height))
            rows.append(base_row(spec, case="threshold-longrow", state=str(size), op=variant, size=size, step=i, render_ms=(time.perf_counter() - began) * 1000))
    return rows


async def _eager_one(path: Path, size: int, variant: str, run: int) -> list[Row]:
    spec = _spec(path, run)
    started = time.perf_counter_ns()
    if variant == "stock":
        area = ProbeTextArea(text=path.read_text(encoding="utf-8"))
    else:
        area = open_probe(path, wrap=False, config=build_config(path, "default"))
    app = ProbeApp(area)
    async with app.run_test(size=SIZE):
        shown = await wait_until(lambda: area.first_content_ns is not None, _FIRST_CONTENT_LIMIT)
        first = None if area.first_content_ns is None else (area.first_content_ns - started) / 1e6
    return [base_row(spec, case="threshold-eager", state=str(size), op=variant, size=size, first_screen_ms=first, shown=shown)]


async def threshold_scenario(spec: Spec) -> list[Row]:
    """Child scenario: one measurement (`spec["kind"]`, `size`, `variant`) in a fresh process, like a real launch."""
    path, size, run = Path(spec["file"]), int(spec["size"]), int(spec.get("run", 1))
    kind, variant = spec["kind"], spec.get("variant", "")
    if kind == "highlight":
        return await _highlight_one(path, size, run)
    if kind == "longrow":
        return await _longrow_one(path, size, variant, run)
    if kind == "eager":
        return await _eager_one(path, size, variant, run)
    msg = f"unknown threshold kind {kind!r}"
    raise ValueError(msg)


def run_thresholds(kind: str, sizes: list[int], width: int, runs: int, directory: Path, runner: Callable[[Spec], list[Row]]) -> list[Row]:
    """Measure one threshold kind for each size (`sizes` are file sizes, or row sizes for `wordwrap` and `longrow`).

    `runner` runs one measurement spec in a fresh process (the apps of a process after the first one start slower, so each measurement gets its own).
    """
    rows: list[Row] = []
    for size in sizes:
        for run in range(1, runs + 1):
            if kind == "wordwrap":
                spec = _spec(directory / f"row_{size}", run)
                rows.append(base_row(spec, case="threshold-wordwrap", state=str(size), op="wrap", size=size, width=width, wrap_ms=wrap_time_ms(size, width)))
            elif kind == "highlight":
                path = write_python_source(directory / f"highlight_{size}.py", size)
                rows += runner({**_spec(path, run), "kind": kind, "size": size})
            elif kind == "longrow":
                path = write_single_row(directory / f"row_{size}.txt", size)
                for variant in ("whole", "window"):
                    rows += runner({**_spec(path, run), "kind": kind, "size": size, "variant": variant})
            elif kind == "eager":
                path = write_plain_text(directory / f"text_{size}.txt", size)
                for variant in ("stock", "lazy"):
                    rows += runner({**_spec(path, run), "kind": kind, "size": size, "variant": variant})
            else:
                msg = f"unknown threshold kind {kind!r}"
                raise ValueError(msg)
    return rows
