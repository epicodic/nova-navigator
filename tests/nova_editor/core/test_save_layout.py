"""`SaveLayout.intervals`: disjoint ascending source intervals with their output offsets (ACT5 design 8.2)."""

from __future__ import annotations

import sys
from itertools import pairwise

import pytest

from nova_editor.core import save_layout
from nova_editor.core.save_layout import SaveLayout

from .reference import Rng

SEEDS = range(20)
RUNS = 10_000
BYTES_PER_RUN = 32
SLACK = 4096


def _expected(runs: list[tuple[int, int, int, int]], src: int) -> dict[int, int]:
    """Brute force: map each source byte to the output offset of its first (lowest output) occurrence."""
    first: dict[int, int] = {}
    for run_src, a, b, out in sorted(runs, key=lambda run: run[3]):
        if run_src != src:
            continue
        for offset in range(a, b):
            first.setdefault(offset, out + offset - a)
    return first


def _marked(intervals: list[tuple[int, int, int]]) -> dict[int, int]:
    marks: dict[int, int] = {}
    for a, b, out in intervals:
        for offset in range(a, b):
            assert offset not in marks
            marks[offset] = out + offset - a
    return marks


def test_internal_copy_paste_keeps_the_first_occurrence() -> None:
    layout = SaveLayout()
    layout.add(0, 10, 20, 0)
    layout.add(1, 0, 4, 10)
    layout.add(0, 10, 20, 14)  # the same source range twice
    assert layout.intervals(0) == [(10, 20, 0)]
    assert layout.intervals(1) == [(0, 4, 10)]
    assert layout.intervals(7) == []


def test_partial_overlap_keeps_the_first_bytes() -> None:
    layout = SaveLayout()
    layout.add(0, 10, 20, 0)
    layout.add(0, 15, 30, 100)
    assert layout.intervals(0) == [(10, 20, 0), (20, 30, 105)]


def test_a_later_run_inside_the_gap_of_earlier_ones_fills_it() -> None:
    layout = SaveLayout()
    layout.add(0, 0, 3, 0)
    layout.add(0, 10, 13, 3)
    layout.add(0, 2, 11, 6)
    assert layout.intervals(0) == [(0, 3, 0), (3, 10, 7), (10, 13, 3)]


def test_intervals_are_disjoint_ascending_and_first_wins() -> None:
    for seed in SEEDS:
        rng = Rng(seed)
        layout = SaveLayout()
        runs: list[tuple[int, int, int, int]] = []
        out = 0
        for _ in range(rng.randint(1, 25)):
            src = rng.randint(0, 2)
            a = rng.randint(0, 40)
            b = a + rng.randint(1, 12)
            layout.add(src, a, b, out)
            runs.append((src, a, b, out))
            out += b - a
        for src in range(3):
            found = layout.intervals(src)
            assert all(x[1] <= y[0] for x, y in pairwise(found))
            assert all(a < b for a, b, _ in found)
            assert _marked(found) == _expected(runs, src)


def test_the_arrays_use_32_bytes_per_run() -> None:
    layout = SaveLayout()
    for k in range(RUNS):
        layout.add(0, k * 10, k * 10 + 5, k * 5)
    assert len(layout) == RUNS
    held = sum(sys.getsizeof(getattr(layout, name)) for name in ("_src", "_a", "_b", "_out"))
    assert held <= BYTES_PER_RUN * RUNS + SLACK + BYTES_PER_RUN * RUNS // 10
    assert layout.intervals(0)[-1] == ((RUNS - 1) * 10, (RUNS - 1) * 10 + 5, (RUNS - 1) * 5)


MANY_RUNS = 20_000
RUN_BYTES = 4


def test_intervals_of_many_scattered_runs_take_a_linearithmic_number_of_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    pushes: list[int] = []
    pops: list[int] = []
    real_push, real_pop = save_layout.heappush, save_layout.heappop

    def counting_push(heap: list[tuple[int, int]], item: tuple[int, int]) -> None:
        pushes.append(1)
        real_push(heap, item)

    def counting_pop(heap: list[tuple[int, int]]) -> tuple[int, int]:
        pops.append(1)
        return real_pop(heap)

    monkeypatch.setattr(save_layout, "heappush", counting_push)
    monkeypatch.setattr(save_layout, "heappop", counting_pop)
    rng = Rng(7)
    positions = list(range(MANY_RUNS))
    for k in range(MANY_RUNS - 1, 0, -1):  # a fixed-seed shuffle: runs land all over the source, never in ascending order
        j = rng.randint(0, k)
        positions[k], positions[j] = positions[j], positions[k]
    layout = SaveLayout()
    runs: list[tuple[int, int, int, int]] = []
    for k, position in enumerate(positions):
        a = position * RUN_BYTES // 2  # neighbouring runs overlap by half
        runs.append((0, a, a + RUN_BYTES, k * RUN_BYTES))
        layout.add(0, a, a + RUN_BYTES, k * RUN_BYTES)
    found = layout.intervals(0)
    assert len(pushes) <= MANY_RUNS
    assert len(pops) <= MANY_RUNS
    assert all(x[1] <= y[0] for x, y in pairwise(found))
    assert _marked(found) == _expected(runs, 0)
