"""Process helpers of the view benchmark harness: /proc sampling, page cache control, supervised children, statistics.

Nothing here imports Textual or `nova_editor`.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import math
import mmap
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

GUARD_KB = 8 * 1024 * 1024
"""A supervised child whose `RssAnon` exceeds this (8 GiB) is killed."""
SAMPLE_INTERVAL = 0.05
COLD_RESIDENCY_LIMIT = 0.05
"""Largest page-cache residency (fraction of the file) that still counts as a cold cache."""
KIB_PER_MIB = 1024
_PAGE = os.sysconf("SC_PAGE_SIZE")
_LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_LIBC.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
_LIBC.mincore.restype = ctypes.c_int


@dataclass(frozen=True)
class MemSample:
    """One reading of `/proc/<pid>/status` (KiB) with the `perf_counter_ns` time it was taken."""

    t_ns: int
    vm_rss_kb: int
    rss_anon_kb: int
    rss_file_kb: int


def read_mem(pid: int | str = "self") -> MemSample:
    """Read `VmRSS`, `RssAnon` and `RssFile` of a process; raise `OSError` or `KeyError` when it is gone."""
    fields: dict[str, int] = {}
    with Path(f"/proc/{pid}/status").open(encoding="ascii") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            if key in ("VmRSS", "RssAnon", "RssFile"):
                fields[key] = int(rest.split()[0])
    return MemSample(time.perf_counter_ns(), fields["VmRSS"], fields["RssAnon"], fields["RssFile"])


def read_rchar(pid: int) -> int | None:
    """Return the bytes a process has read through read-like system calls so far (`/proc/<pid>/io`), or None when unreadable."""
    try:
        with Path(f"/proc/{pid}/io").open(encoding="ascii") as handle:
            for line in handle:
                if line.startswith("rchar:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def resident_fraction(path: Path) -> float:
    """Return the fraction of the file's pages that are resident in the page cache (`mincore` through ctypes)."""
    size = path.stat().st_size
    if size == 0:
        return 0.0
    pages = (size + _PAGE - 1) // _PAGE
    vec = (ctypes.c_ubyte * pages)()
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_COPY) as mapped:
        anchor = ctypes.c_char.from_buffer(mapped)
        try:
            if _LIBC.mincore(ctypes.c_void_p(ctypes.addressof(anchor)), ctypes.c_size_t(size), vec) != 0:
                raise OSError(ctypes.get_errno(), "mincore failed")
        finally:
            del anchor
    return sum(byte & 1 for byte in vec) / pages


def drop_cache(path: Path) -> float:
    """`fsync` and `posix_fadvise(DONTNEED)` the file (up to 3 tries) and return the residency left afterwards."""
    fraction = 1.0
    fd = os.open(path, os.O_RDONLY)
    try:
        for _ in range(3):
            with contextlib.suppress(OSError):  # a read-only file cannot always be synced; nothing is dirty there
                os.fsync(fd)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            fraction = resident_fraction(path)
            if fraction <= COLD_RESIDENCY_LIMIT:
                break
    finally:
        os.close(fd)
    return fraction


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the nearest-rank percentile: the value at 1-based rank `ceil(fraction * n)` of the sorted values.

    Args:
        values: Non-empty sample.
        fraction: In (0, 1], for example 0.95 for p95.

    Returns:
        A value of the sample (never interpolated).
    """
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def median(values: Sequence[float]) -> float:
    """Return the nearest-rank median (the 50th percentile) of a non-empty sample."""
    return percentile(values, 0.5)


@dataclass
class Supervised:
    """Outcome of a supervised child process."""

    samples: list[MemSample] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    status: str = "exit"
    returncode: int | None = None
    stderr_tail: str = ""
    t0_ns: int = 0

    def maxima(self, since_ns: int = 0, until_ns: int | None = None) -> dict[str, int]:
        """Return the largest `RssAnon`, `VmRSS` and `RssFile` (KiB) of the samples in `[since_ns, until_ns]` (empty dict without samples)."""
        chosen = [s for s in self.samples if s.t_ns >= since_ns and (until_ns is None or s.t_ns <= until_ns)]
        if not chosen:
            return {}
        return {
            "rss_anon_kb_max": max(s.rss_anon_kb for s in chosen),
            "vm_rss_kb_max": max(s.vm_rss_kb for s in chosen),
            "rss_file_kb_max": max(s.rss_file_kb for s in chosen),
            "n_samples": len(chosen),
        }


def supervise(
    cmd: Sequence[str],
    timeout: float,
    *,
    env: Mapping[str, str] | None = None,
    stop_when: Callable[[Supervised, int], bool] | None = None,
) -> Supervised:
    """Run `cmd`, sample its memory every 50 ms, collect its stdout lines, kill it on timeout or above the 8 GiB `RssAnon` guard.

    `t0_ns` is taken immediately before `Popen`. `stop_when(result, pid)` is polled with every sample and stops the child when it returns True.
    """
    result = Supervised()
    with tempfile.TemporaryFile("w+", encoding="utf-8") as stderr_file:
        lock = threading.Lock()
        result.t0_ns = time.perf_counter_ns()
        proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=stderr_file, text=True, bufsize=1, env=None if env is None else dict(env))
        stdout = proc.stdout

        def reader() -> None:
            if stdout is None:
                return
            for line in stdout:
                with lock:
                    result.lines.append(line.rstrip("\n"))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.perf_counter() + timeout
        try:
            while proc.poll() is None:
                with contextlib.suppress(OSError, KeyError):
                    sample = read_mem(proc.pid)
                    result.samples.append(sample)
                    if sample.rss_anon_kb > GUARD_KB:
                        result.status = "guard_rss_anon_over_8GiB"
                        break
                if stop_when is not None and stop_when(result, proc.pid):
                    result.status = "stopped"
                    break
                if time.perf_counter() > deadline:
                    result.status = "timeout"
                    break
                time.sleep(SAMPLE_INTERVAL)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            thread.join(timeout=5)
            result.returncode = proc.returncode
            stderr_file.seek(0)
            result.stderr_tail = stderr_file.read()[-4000:]
        return result


def kill_group(proc: subprocess.Popen[bytes], sig: int = signal.SIGKILL) -> None:
    """Signal the whole process group of a child started with `start_new_session=True`; ignore a group that is already gone."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)
