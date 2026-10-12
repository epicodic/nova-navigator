"""The in-place layout rule: table-driven cases and a property test against a per-byte model (ACT3 design 2)."""

from __future__ import annotations

import pytest

from nova_editor.core.inplace_layout import inplace_ranges

from .reference import Rng

LEGACY_ORIGINAL = (1 << 24) | 0

Run = tuple[int, int, int]
Range = tuple[int, int]

CASES: list[tuple[list[Run], int, int, list[Range], list[Range] | None]] = [
    ([(0, 0, 10)], 10, 10, [], []),  # untouched
    ([(0, 0, 3), (1, 0, 1), (0, 4, 10)], 10, 10, [], [(3, 4)]),  # one byte overwritten
    ([(0, 0, 3), (1, 0, 2), (0, 5, 10)], 10, 10, [], [(3, 5)]),
    ([(0, 0, 3), (1, 0, 1), (0, 3, 9)], 10, 10, [], None),  # insert and delete: the original moved
    ([(0, 0, 3), (1, 0, 1), (0, 4, 10)], 11, 10, [], None),  # the length differs
    ([(0, 0, 3), (1, 0, 1), (1, 1, 2), (0, 5, 10)], 10, 10, [], [(3, 5)]),  # adjacent dirty runs merge
    ([(0, 0, 2), (0, 2, 4), (0, 4, 10)], 10, 10, [(2, 4)], [(2, 4)]),  # an original over a patched range is dirty (split runs)
    ([(0, 0, 10)], 10, 10, [(2, 4)], [(2, 4)]),  # the same with one unsplit run
    ([(0, 0, 10)], 10, 10, [(0, 2), (5, 7), (9, 10)], [(0, 2), (5, 7), (9, 10)]),
    ([(LEGACY_ORIGINAL, 0, 10)], 10, 10, [], [(0, 10)]),  # a legacy generation run is dirty
    ([(0, 0, 4), (LEGACY_ORIGINAL, 2, 4), (0, 6, 10)], 10, 10, [], [(4, 6)]),
    ([(1, 0, 10)], 10, 10, [], [(0, 10)]),  # everything new
    ([(0, 0, 4), (2, 5, 7), (1, 0, 1), (0, 7, 10)], 10, 10, [], [(4, 7)]),  # several adds merge into one range
    ([(0, 0, 3), (1, 0, 1), (0, 4, 6), (2, 0, 1), (0, 7, 10)], 10, 10, [], [(3, 4), (6, 7)]),  # separate ranges
    ([(0, 5, 10), (0, 0, 5)], 10, 10, [], None),  # the halves swapped
    ([(0, 0, 4), (1, 0, 2), (0, 6, 10)], 10, 10, [(4, 6)], [(4, 6)]),  # a dirty run inside a patched range
    ([], 0, 0, [], []),  # an empty document
    ([(0, 0, 3), (0, 3, 3), (0, 3, 10)], 10, 10, [], []),  # an empty run is ignored
]


@pytest.mark.parametrize(("runs", "length", "orig", "patched", "expected"), CASES)
def test_the_rule_on_table_cases(runs: list[Run], length: int, orig: int, patched: list[Range], expected: list[Range] | None) -> None:
    assert inplace_ranges(runs, length, orig, patched) == expected


def test_runs_may_be_a_one_shot_iterator() -> None:
    assert inplace_ranges(iter([(0, 0, 3), (1, 0, 1), (0, 4, 10)]), 10, 10, []) == [(3, 4)]


def _model(runs: list[Run], length: int, orig_length: int, patched: list[Range]) -> list[Range] | None:
    """Mark each document byte clean or dirty and merge the dirty bytes."""
    if length != orig_length:
        return None
    dirty: list[bool] = []
    pos = 0
    for src, a, b in runs:
        for k in range(b - a):
            if src != 0:
                dirty.append(True)
            elif a != pos:
                return None
            else:
                dirty.append(any(lo <= pos + k < hi for lo, hi in patched))
        pos += b - a
    result: list[Range] = []
    for offset, flag in enumerate(dirty):
        if not flag:
            continue
        if result and result[-1][1] == offset:
            result[-1] = (result[-1][0], offset + 1)
        else:
            result.append((offset, offset + 1))
    return result


def _random_layout(rng: Rng, size: int) -> list[Run]:
    runs: list[Run] = []
    pos = 0
    while pos < size:
        step = rng.randint(1, min(6, size - pos))
        kind = rng.randint(0, 99)
        if kind < 50:
            runs.append((0, pos, pos + step))
        elif kind < 90:
            runs.append((rng.choice([1, 2, LEGACY_ORIGINAL, (1 << 24) | 3]), 0, step))
        else:
            start = rng.randint(0, size - step)  # an original piece at a random offset (mostly the wrong one)
            runs.append((0, start, start + step))
        pos += step
    return runs


@pytest.mark.parametrize("seed", range(200))
def test_the_rule_equals_a_per_byte_model(seed: int) -> None:
    rng = Rng(seed)
    size = 40
    runs = _random_layout(rng, size)
    cuts = sorted({rng.randint(0, size) for _ in range(rng.choice([0, 2, 4, 6]))})
    cuts = cuts[: len(cuts) // 2 * 2]
    patched = [(cuts[i], cuts[i + 1]) for i in range(0, len(cuts), 2)]
    length = size if rng.randint(0, 99) < 90 else size + 1
    assert inplace_ranges(runs, length, size, patched) == _model(runs, length, size, patched)
