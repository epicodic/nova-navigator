"""`SaveJob` ordering of the commit: the mode change is flushed before the rename."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nova_editor.core.save import SaveIo

from .save_helpers import ORIGINAL, make_job


def _recorded(op: str, real: Callable[..., object], calls: list[str]) -> Callable[..., object]:
    def wrapper(*args: object, **kwargs: object) -> object:
        calls.append(op)
        return real(*args, **kwargs)

    return wrapper


def recording_io(calls: list[str]) -> SaveIo:
    """Return a `SaveIo` appending the name of each fsync, fchmod and replace call to `calls`."""
    io = SaveIo()
    for op in ("fsync", "fchmod", "replace"):
        setattr(io, op, _recorded(op, getattr(io, op), calls))
    return io


def test_fchmod_is_flushed_before_the_replace(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    calls: list[str] = []
    job, source = make_job(target, io=recording_io(calls))
    job.run().source.close()
    source.close()
    before_replace = calls[: calls.index("replace")]
    assert "fchmod" in before_replace
    assert before_replace[-1] == "fsync"
    assert before_replace.index("fchmod") < len(before_replace) - 1 - before_replace[::-1].index("fsync")
