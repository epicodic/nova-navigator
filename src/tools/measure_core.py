"""Measure the `nova_editor.core` indexes: memory, scan time and worst-case call cost.

Usage:
    uv run python -m tools.measure_core index PATH [--stride N] [--cache-blocks N] [--cold]
    uv run python -m tools.measure_core longline PATH
    uv run python -m tools.measure_core calls PATH [--samples N]

Each mode prints exactly one JSON object on stdout.
Memory is `RssAnon` from `/proc/self/status`, sampled after all imports so import cost is not charged to the index.
A guard thread exits the process with status 3 when `RssAnon` exceeds 8 GiB.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from nova_editor.core import LineIndex, LongLineIndex, PreadSource
from nova_editor.core.byte_source import ByteSource

GUARD_LIMIT_KB = 8 * 1024 * 1024
GUARD_INTERVAL = 0.2
WARM_CALLS = 1000
LINES_PER_CALL = 128

_LCG_MULTIPLIER = 6364136223846793005
_LCG_INCREMENT = 1442695040888963407
_MASK64 = (1 << 64) - 1


def _rss_anon_kb() -> int:
    """Return `RssAnon` of this process in KiB."""
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("RssAnon:"):
                return int(line.split()[1])
    msg = "RssAnon not found in /proc/self/status"
    raise RuntimeError(msg)


class _Guard:
    """Background thread that tracks peak `RssAnon` and kills the process above the limit."""

    def __init__(self) -> None:
        self.baseline_kb = _rss_anon_kb()
        self.peak_kb = self.baseline_kb
        self._thread = threading.Thread(target=self._run, name="rss-guard", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            rss = _rss_anon_kb()
            self.peak_kb = max(self.peak_kb, rss)
            if rss > GUARD_LIMIT_KB:
                print("GUARD: RssAnon > 8 GiB, exiting", file=sys.stderr, flush=True)
                os._exit(3)
            time.sleep(GUARD_INTERVAL)


class _Lcg:
    """Deterministic pseudo-random numbers (64-bit LCG); the same sequence on every run."""

    def __init__(self, seed: int = 1) -> None:
        self._state = seed & _MASK64

    def below(self, bound: int) -> int:
        self._state = (self._state * _LCG_MULTIPLIER + _LCG_INCREMENT) & _MASK64
        return (self._state >> 11) % bound


class _CountingSource:
    """Wraps a `ByteSource` and counts the bytes requested from it."""

    def __init__(self, inner: ByteSource) -> None:
        self._inner = inner
        self.requested = 0

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        data = self._inner.read(offset, size, cache=cache)
        self.requested += len(data)
        return data

    def close(self) -> None:
        self._inner.close()


def _cold_start(path: Path) -> None:
    """Drop the page cache for `path` (best effort; needs no privileges)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        with contextlib.suppress(OSError):  # read-only files cannot always be synced; nothing is dirty there
            os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def _measure_index(args: argparse.Namespace, guard: _Guard) -> dict[str, object]:
    path = Path(args.path)
    if args.cold:
        _cold_start(path)
    source = PreadSource(path, cache_blocks=args.cache_blocks)
    index = LineIndex(source, stride=args.stride)
    before = _rss_anon_kb()
    started = time.perf_counter()
    index.start()
    index.join()
    scan_seconds = time.perf_counter() - started
    after = _rss_anon_kb()
    snapshot = index.snapshot()
    if snapshot.error is not None:
        msg = f"scan failed: {snapshot.error!r}"
        raise RuntimeError(msg)
    rows = snapshot.count
    rng = _Lcg(1)
    for _ in range(WARM_CALLS):
        index.row_range(rng.below(rows))
    after_cache = _rss_anon_kb()
    source.close()
    return {
        "kind": "index",
        "path": str(path),
        "stride": args.stride,
        "cache_blocks": args.cache_blocks,
        "cold": args.cold,
        "rows": rows,
        "stored_entries": index.stored_entries(),
        "long_row_count": index.long_row_count,
        "long_overflow": index.long_overflow,
        "scan_seconds": round(scan_seconds, 3),
        "rss_anon_kb_baseline_after_imports": guard.baseline_kb,
        "rss_anon_kb_before": before,
        "rss_anon_kb_after": after,
        "rss_anon_kb_after_cache": after_cache,
        "rss_anon_kb_peak": max(guard.peak_kb, after, after_cache),
    }


def _measure_longline(args: argparse.Namespace, guard: _Guard) -> dict[str, object]:
    path = Path(args.path)
    source = PreadSource(path)
    size = source.length()
    before = _rss_anon_kb()
    index = LongLineIndex(source, 0, size, autostart=False)
    started = time.perf_counter()
    index.start()
    query_started = time.perf_counter()
    far = index.try_char_to_byte(size)
    far_query_ms = (time.perf_counter() - query_started) * 1000
    index.wait_until_known(char_col=size + 1)
    scan_seconds = time.perf_counter() - started
    after = _rss_anon_kb()
    source.close()
    return {
        "kind": "longline",
        "path": str(path),
        "size": size,
        "total_chars": index.total_chars,
        "total_disp": index.total_disp,
        "checkpoints": index.checkpoint_count,
        "scan_seconds": round(scan_seconds, 3),
        "far_query_ms": round(far_query_ms, 4),
        "far_query_was_none": far is None,
        "rss_anon_kb_baseline_after_imports": guard.baseline_kb,
        "rss_anon_kb_before": before,
        "rss_anon_kb_after": after,
        "rss_anon_kb_peak": max(guard.peak_kb, after),
    }


def _measure_calls(args: argparse.Namespace, guard: _Guard) -> dict[str, object]:
    path = Path(args.path)
    source = _CountingSource(PreadSource(path))
    index = LineIndex(source)
    index.start()
    index.join()
    snapshot = index.snapshot()
    if snapshot.error is not None:
        msg = f"scan failed: {snapshot.error!r}"
        raise RuntimeError(msg)
    rows = snapshot.count
    rng = _Lcg(2)
    times: list[float] = []
    worst_bytes = 0
    unresolved = 0
    for call in range(args.samples + args.samples // 4):
        row = rng.below(rows)
        before_bytes = source.requested
        started = time.perf_counter()
        if call < args.samples:
            unresolved += index.row_range(row) is None
        else:
            index.lines(row, LINES_PER_CALL)
        times.append((time.perf_counter() - started) * 1000)
        worst_bytes = max(worst_bytes, source.requested - before_bytes)
    source.close()
    return {
        "kind": "calls",
        "path": str(path),
        "rows": rows,
        "samples": args.samples,
        "lines_calls": args.samples // 4,
        "unresolved_row_range": unresolved,
        "worst_ms": round(max(times), 3),
        "median_ms": round(statistics.median(times), 4),
        "worst_bytes": worst_bytes,
        "rss_anon_kb_baseline_after_imports": guard.baseline_kb,
        "rss_anon_kb_peak": max(guard.peak_kb, _rss_anon_kb()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.measure_core", description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    index = modes.add_parser("index", help="build the line index and report memory and time")
    index.add_argument("path")
    index.add_argument("--stride", type=int, default=64)
    index.add_argument("--cache-blocks", type=int, default=128)
    index.add_argument("--cold", action="store_true", help="drop the page cache for the file first")
    longline = modes.add_parser("longline", help="scan the whole file as one long row")
    longline.add_argument("path")
    calls = modes.add_parser("calls", help="time random row_range and lines calls")
    calls.add_argument("path")
    calls.add_argument("--samples", type=int, default=2000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    guard = _Guard()
    guard.start()
    if args.mode == "index":
        result = _measure_index(args, guard)
    elif args.mode == "longline":
        result = _measure_longline(args, guard)
    else:
        result = _measure_calls(args, guard)
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
