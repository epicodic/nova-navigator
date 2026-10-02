"""Helpers shared by the save failure, target and cancel tests."""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from nova_editor.core import PreadSource
from nova_editor.core.save import SaveIo, SaveJob, SaveProgress, SaveSettings

from .fake_planner import whole_file_planner

ORIGINAL = bytes(range(33, 93))  # 60 bytes
CHUNK = 7


def failing_io(op: str, at: int, errno_: int) -> SaveIo:
    """Return a `SaveIo` whose `op` raises `OSError(errno_)` at its `at`-th call (1-based)."""
    io = SaveIo()
    real: Callable[..., object] = getattr(io, op)
    calls = 0

    def wrapper(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == at:
            raise OSError(errno_, "injected failure")
        return real(*args, **kwargs)

    setattr(io, op, wrapper)
    return io


def counting_io(counter: Counter[str]) -> SaveIo:
    """Return a `SaveIo` that counts the calls of every listed operation in `counter`."""
    io = SaveIo()
    for op in ("mkstemp", "fallocate", "write", "fsync", "fchmod", "replace"):
        setattr(io, op, _counted(op, getattr(io, op), counter))
    return io


def _counted(op: str, real: Callable[..., object], counter: Counter[str]) -> Callable[..., object]:
    def wrapper(*args: object, **kwargs: object) -> object:
        counter[op] += 1
        return real(*args, **kwargs)

    return wrapper


def make_job(
    target: Path,
    *,
    io: SaveIo | None = None,
    chunk: int = CHUNK,
    progress: Callable[[SaveProgress], None] | None = None,
) -> tuple[SaveJob, PreadSource]:
    """Create a job saving the unedited content of `target` back to `target`; return it and its source."""
    source = PreadSource(target)
    ticks = itertools.count()  # every report is a second apart, so the throttle never skips one
    job = SaveJob(
        whole_file_planner(source),
        target,
        SaveSettings(chunk=chunk, fsync_every=64),
        progress=progress,
        foreground=None,
        io=io,
        clock=lambda: float(next(ticks)),
    )
    return job, source
