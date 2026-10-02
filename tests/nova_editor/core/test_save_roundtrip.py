"""`SaveJob` happy path: byte-identical round trip, edits, progress, mode and the foreground gate."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from nova_editor.core import BytesSource, LineIndex, PreadSource
from nova_editor.core.save import SaveJob, SaveProgress, SaveSettings

from .fake_planner import edited_planner, whole_file_planner

CASES: dict[str, bytes] = {
    "empty": b"",
    "one": b"x",
    "invalid": b"a\x80\xc3\xe2\x82 b\xc0\xaf\n",
    "bom": b"\xef\xbb\xbfhello\n",
    "crlf": b"a\r\nb\r\n",
    "lf": b"a\nb\n",
    "cr": b"a\rb\r",
    "mixed": b"a\r\nb\nc\rd",
    "no_eol": b"abc",
}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("chunk", [7, 1 << 20])
def test_round_trip_is_byte_identical(tmp_path: Path, name: str, chunk: int) -> None:
    data = CASES[name]
    target = tmp_path / "f.txt"
    target.write_bytes(data)
    source = PreadSource(target)
    job = SaveJob(whole_file_planner(source), target, SaveSettings(chunk=chunk, fsync_every=64), progress=None, foreground=None)
    result = job.run()
    assert target.read_bytes() == data
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]
    assert result.length == len(data)
    assert result.source.read(0, len(data) + 1) == data
    fresh = LineIndex(PreadSource(target), stride=4, long_line_threshold=64)
    fresh.scan_now()
    assert result.line_index is not None
    assert result.line_index.snapshot().count == fresh.snapshot().count
    result.source.close()
    source.close()


def test_edited_plan_writes_the_concatenation(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"0123456789")
    source = PreadSource(target)
    planner = edited_planner([(0, 0, 2), (1, 0, 3), (0, 5, 9)], source, BytesSource(b"abcdef"))
    result = SaveJob(planner, target, SaveSettings(chunk=3, fsync_every=64), progress=None, foreground=None).run()
    assert target.read_bytes() == b"01abc5678"
    assert result.length == 9
    result.source.close()
    source.close()


def test_progress_is_monotonic_and_ends_complete(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"x" * 100)
    source = PreadSource(target)
    calls: list[SaveProgress] = []
    ticks = iter(range(10_000))
    job = SaveJob(
        whole_file_planner(source),
        target,
        SaveSettings(chunk=7, fsync_every=64),
        progress=calls.append,
        foreground=None,
        clock=lambda: float(next(ticks)),
    )
    job.run().source.close()
    source.close()
    writing = [c.done for c in calls if c.phase == "writing"]
    assert writing == sorted(writing)
    assert writing[-1] == 100
    assert all(c.total == 100 for c in calls)


def test_progress_is_throttled_to_twenty_per_second(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"x" * 1000)
    source = PreadSource(target)
    calls: list[SaveProgress] = []
    job = SaveJob(
        whole_file_planner(source),
        target,
        SaveSettings(chunk=1, fsync_every=1 << 30),
        progress=calls.append,
        foreground=None,
        clock=lambda: 0.0,
    )
    job.run().source.close()
    source.close()
    writing = [c for c in calls if c.phase == "writing"]
    assert [c.done for c in writing] == [1, 1000]  # the clock never advances: first and last only


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o755])
def test_mode_of_an_existing_target_is_kept(tmp_path: Path, mode: int) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"hello\n")
    target.chmod(mode)
    source = PreadSource(target)
    result = SaveJob(whole_file_planner(source), target, SaveSettings(), progress=None, foreground=None).run()
    assert stat.S_IMODE(os.stat(target).st_mode) == mode
    assert result.mode == mode
    result.source.close()
    source.close()


class _CountingGate:
    def __init__(self) -> None:
        self.calls = 0

    def pause_seconds(self) -> float:
        self.calls += 1
        return 0.0


def test_foreground_gate_is_consulted_once_per_chunk(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"x" * 50)
    source = PreadSource(target)
    gate = _CountingGate()
    job = SaveJob(whole_file_planner(source), target, SaveSettings(chunk=7, fsync_every=64), progress=None, foreground=gate)
    job.run().source.close()
    source.close()
    assert gate.calls == 8  # ceil(50 / 7)
