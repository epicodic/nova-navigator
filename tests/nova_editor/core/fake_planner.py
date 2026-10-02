"""A fake `SavePlanner` over named byte sources and a list of `(src, a, b)` runs."""

from __future__ import annotations

from nova_editor.core.byte_source import ByteSource
from nova_editor.core.save import PlanPart


class FakePlanner:
    """Plan the document formed by concatenating `runs`, each a range `[a, b)` of the source numbered `src`."""

    def __init__(self, runs: list[tuple[int, int, int]], sources: dict[int, ByteSource]) -> None:
        self._runs = runs
        self._sources = sources

    def length(self) -> int:
        return sum(b - a for _, a, b in self._runs)

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        del unverified
        parts: list[PlanPart] = []
        pos = 0
        end = offset + limit
        for src, a, b in self._runs:
            run_end = pos + (b - a)
            lo, hi = max(offset, pos), min(end, run_end)
            if lo < hi:
                parts.append(PlanPart(src, self._sources[src], a + (lo - pos), a + (hi - pos)))
            pos = run_end
        return parts


def whole_file_planner(source: ByteSource) -> FakePlanner:
    """Return a planner for the unedited document over `source`."""
    return FakePlanner([(0, 0, source.length())], {0: source})


def edited_planner(runs: list[tuple[int, int, int]], original: ByteSource, add: ByteSource) -> FakePlanner:
    """Return a planner over `runs` of the original (src 0) and an add store (src 1)."""
    return FakePlanner(runs, {0: original, 1: add})
