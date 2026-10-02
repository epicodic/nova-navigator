"""`SaveJob` failures after the replace: the file is written, nothing leaks and `SaveFailed.committed` says so."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_editor.core import LineIndex, PreadSource
from nova_editor.core.save import SaveFailed, SaveIo, SaveJob, SaveProgress, SaveSettings

from .fake_planner import FakePlanner
from .save_helpers import ORIGINAL, failing_io, make_job

NEW = ORIGINAL[30:] + ORIGINAL[:30]


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _boom(*args: object, **kwargs: object) -> object:
    del args, kwargs
    raise RuntimeError("injected")


def _unlink_recorder(calls: list[str]) -> SaveIo:
    io = SaveIo()
    real = io.unlink

    def unlink(name: str) -> None:
        calls.append(name)
        real(name)

    io.unlink = unlink
    return io


def _swapped_job(target: Path, io: SaveIo | None = None, progress: Callable[[SaveProgress], None] | None = None) -> tuple[SaveJob, PreadSource]:
    """Return a job saving the two halves of `target` swapped, and the source it reads."""
    source = PreadSource(target)
    planner = FakePlanner([(0, 30, 60), (0, 0, 30)], {0: source})
    return SaveJob(planner, target, SaveSettings(chunk=7, fsync_every=64), progress=progress, foreground=None, io=io), source


def _break_from_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LineIndex, "from_scan", _boom)


def _break_from_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PreadSource, "from_fd", _boom)


@pytest.mark.parametrize("breaker", [_break_from_scan, _break_from_fd], ids=["from_scan", "from_fd"])
def test_post_commit_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breaker: Callable[[pytest.MonkeyPatch], None]) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    unlinked: list[str] = []
    job, source = _swapped_job(target, io=_unlink_recorder(unlinked))
    before = _fd_count()
    breaker(monkeypatch)
    with pytest.raises(SaveFailed) as info:
        job.run()
    source.close()
    assert target.read_bytes() == NEW
    assert info.value.committed is True
    assert info.value.stage == "internal"
    assert "could not switch" in str(info.value)
    assert _fd_count() == before - 1  # only the planner's own source was closed above
    assert unlinked == []
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_progress_failure_after_commit(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)

    def progress(report: SaveProgress) -> None:
        if report.phase == "finishing":
            raise RuntimeError("injected")

    job, source = _swapped_job(target, progress=progress)
    before = _fd_count()
    with pytest.raises(SaveFailed) as info:
        job.run()
    source.close()
    assert info.value.committed is True
    assert target.read_bytes() == NEW
    assert _fd_count() == before - 1  # only the planner's own source was closed above
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


@pytest.mark.parametrize("op", ["write", "fsync", "fchmod", "replace", "mkstemp"])
def test_pre_commit_failures_are_not_committed(tmp_path: Path, op: str) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    job, source = make_job(target, io=failing_io(op, 1, errno.EIO))
    with pytest.raises(SaveFailed) as info:
        job.run()
    source.close()
    assert info.value.committed is False


def test_job_reports_the_commit_point(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    seen: list[bool] = []
    io = SaveIo()
    job, source = _swapped_job(target, io)
    io.replace = lambda src, dst: (seen.append(job.committed), os.replace(src, dst))[1]
    io.fsync_dir = lambda _directory: seen.append(job.committed)
    assert job.committed is False
    result = job.run()
    result.source.close()
    source.close()
    assert seen == [False, True]
    assert job.committed is True
