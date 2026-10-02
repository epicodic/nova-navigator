"""Unit tests of `SearchJob`: cancel, gate, stale, retry, progress, window reads and chunk independence."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

import pytest

from nova_editor.core.byte_source import ChangeKind, SourceChanged
from nova_editor.core.memory_source import BytesSource
from nova_editor.core.search import (
    CONTEXT_AFTER,
    CONTEXT_BEFORE,
    SearchCancelled,
    SearchJob,
    SearchPlan,
    SearchProgress,
    SearchResult,
    SearchSettings,
    SearchSpec,
    SearchStale,
    compile_matcher,
)
from tests.nova_editor.core.fake_planner import FakePlanner, whole_file_planner

DATA = b"abc" * 20 + b"needle" + b"xyz" * 20


class RecordingSource:
    """A `ByteSource` over bytes that records every read as `(offset, size, cache)` and can fail on demand."""

    def __init__(self, data: bytes, fail_reads: int = 0) -> None:
        self._inner = BytesSource(data)
        self.reads: list[tuple[int, int, bool]] = []
        self.fail_reads = fail_reads

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        self.reads.append((offset, size, cache))
        if self.fail_reads > 0:
            self.fail_reads -= 1
            raise ValueError("released")
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        """Nothing to release."""


class Gate:
    """A `Foreground` double that counts how often it is consulted."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    def pause_seconds(self) -> float:
        self.calls += 1
        return self.seconds


def _job(
    planner: FakePlanner,
    *,
    needle: str = "needle",
    chunk: int = 16,
    origin: int = 0,
    spec: SearchSpec | None = None,
    gate: Gate | None = None,
    sleep: Callable[[float], None] = lambda _seconds: None,
    progress: Callable[[SearchProgress], None] | None = None,
    clock: Callable[[], float] = lambda: 0.0,
    interval: float = 0.05,
) -> SearchJob:
    return SearchJob(
        planner,
        spec or SearchSpec(needle),
        origin,
        SearchSettings(chunk=chunk, progress_interval=interval),
        progress=progress,
        foreground=gate,
        clock=clock,
        sleep=sleep,
    )


def _planner(data: bytes = DATA) -> FakePlanner:
    return whole_file_planner(BytesSource(data))


def test_finds_the_needle() -> None:
    assert _job(_planner()).run() == SearchResult(60, 66, False)


def test_revision_is_none_before_run_and_the_first_plan_after() -> None:
    planner = _planner()
    planner.revision = 7
    job = _job(planner)
    assert job.revision is None
    job.run()
    assert job.revision == 7


def test_cancel_before_the_first_unit_reads_nothing() -> None:
    planner = _planner()
    job = _job(planner)
    job.cancel()
    with pytest.raises(SearchCancelled):
        job.run()
    assert planner.plan_calls == []


def test_cancel_after_k_units_stops_planning() -> None:
    planner = _planner()
    job = _job(planner)

    def on_plan(call: int) -> None:
        if call == 3:  # the initial plan, unit 1, unit 2
            job.cancel()

    planner.on_plan = on_plan
    with pytest.raises(SearchCancelled):
        job.run()
    assert len(planner.plan_calls) == 3


def test_the_gate_is_consulted_once_per_unit_and_its_pause_slept() -> None:
    planner = _planner(b"x" * 50)
    gate = Gate(0.001)
    slept: list[float] = []
    assert _job(planner, gate=gate, sleep=slept.append, chunk=10).run() is None
    assert gate.calls == 5
    assert slept == [0.001] * 5


def test_no_sleep_when_the_gate_says_zero() -> None:
    slept: list[float] = []
    _job(_planner(b"x" * 30), gate=Gate(0.0), sleep=slept.append, chunk=10).run()
    assert slept == []


def test_source_changed_from_the_planner_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    planner = _planner()
    error = SourceChanged("gone", ChangeKind.DELETED)

    def raising(_offset: int, _limit: int) -> SearchPlan:
        raise error

    monkeypatch.setattr(planner, "search_plan", raising)
    with pytest.raises(SourceChanged) as caught:
        _job(planner).run()
    assert caught.value is error


def test_a_revision_change_raises_stale() -> None:
    planner = _planner()

    def on_plan(call: int) -> None:
        if call == 3:
            planner.revision += 1

    planner.on_plan = on_plan
    with pytest.raises(SearchStale):
        _job(planner).run()


def test_a_released_source_is_retried_once() -> None:
    source = RecordingSource(DATA, fail_reads=1)
    planner = FakePlanner([(0, 0, len(DATA))], {0: source})
    assert _job(planner).run() == SearchResult(60, 66, False)


def test_a_source_released_twice_is_unreadable() -> None:
    source = RecordingSource(DATA, fail_reads=2)
    planner = FakePlanner([(0, 0, len(DATA))], {0: source})
    with pytest.raises(SourceChanged) as caught:
        _job(planner).run()
    assert caught.value.kind is ChangeKind.UNREADABLE


def test_a_short_read_is_truncated() -> None:
    planner = FakePlanner([(0, 0, len(DATA) + 10)], {0: BytesSource(DATA)})
    with pytest.raises(SourceChanged) as caught:
        _job(planner, needle="zzz").run()
    assert caught.value.kind is ChangeKind.TRUNCATED


@pytest.mark.parametrize("wrap", [True, False])
def test_progress_is_throttled_monotonic_and_bounded(wrap: bool) -> None:
    data = b"x" * 100
    ticks = iter(range(10_000))
    reports: list[tuple[float, SearchProgress]] = []
    now = [0.0]

    def clock() -> float:
        now[0] = next(ticks) * 0.01
        return now[0]

    spec = SearchSpec("needle", wrap=wrap)
    job = _job(_planner(data), spec=spec, chunk=5, origin=40, clock=clock, progress=lambda p: reports.append((now[0], p)))
    assert job.run() is None
    assert reports
    times = [t for t, _ in reports]
    assert all(b - a >= 0.05 - 1e-9 for a, b in pairwise(times))
    dones = [p.done for _, p in reports]
    assert dones == sorted(dones)
    assert {p.total for _, p in reports} == {100 if wrap else 60}
    assert dones[-1] <= reports[-1][1].total
    assert {p.phase for _, p in reports} <= {"forward", "wrapped"}


def test_an_empty_document_returns_none() -> None:
    planner = _planner(b"")
    assert _job(planner).run() is None
    assert planner.plan_calls == [(0, 0)]


def test_a_needle_longer_than_the_document_returns_none() -> None:
    assert _job(_planner(b"abc"), needle="abcd", chunk=1).run() is None


def test_window_reads_are_bounded_and_the_original_is_read_without_the_cache() -> None:
    original = RecordingSource(DATA[:70])
    add = RecordingSource(DATA[70:])
    planner = FakePlanner([(0, 0, 70), (1, 0, len(DATA) - 70)], {0: original, 1: add})
    needle = "needle"
    chunk = 16
    limit = chunk + compile_matcher(needle, case_sensitive=True).max_length - 1 + CONTEXT_BEFORE + CONTEXT_AFTER
    assert _job(planner, needle=needle, chunk=chunk).run() == SearchResult(60, 66, False)
    assert original.reads
    assert all(size <= limit for _, size, _ in original.reads + add.reads)
    assert all(cache is False for _, _, cache in original.reads)


@pytest.mark.parametrize("chunk", [1, 2, 3])
@pytest.mark.parametrize("backward", [False, True])
def test_tiny_chunks_agree_with_a_large_one(chunk: int, backward: bool) -> None:
    data = b"ab\r\nab"
    for origin in range(len(data) + 1):
        spec = SearchSpec("b\n", backward=backward)
        small = SearchJob(_planner(data), spec, origin, SearchSettings(chunk=chunk)).run()
        large = SearchJob(_planner(data), spec, origin, SearchSettings(chunk=1 << 20)).run()
        assert small == large
