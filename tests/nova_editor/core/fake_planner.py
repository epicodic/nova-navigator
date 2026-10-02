"""A fake `SavePlanner` over named byte sources and a list of `(src, a, b)` runs."""

from __future__ import annotations

from collections.abc import Callable

from nova_editor.core.byte_source import ByteSource
from nova_editor.core.save import PlanPart
from nova_editor.core.search import SearchPlan


class FakePlanner:
    """Plan the document formed by concatenating `runs`, each a range `[a, b)` of the source numbered `src`."""

    def __init__(self, runs: list[tuple[int, int, int]], sources: dict[int, ByteSource]) -> None:
        self._runs = runs
        self._sources = sources
        self.revision = 0
        """The revision `search_plan` reports; tests change it to simulate an edit."""
        self.plan_calls: list[tuple[int, int]] = []
        """Every `(offset, limit)` asked of `plan` or `search_plan`, in order."""
        self.on_plan: Callable[[int], None] | None = None
        """Called with the call number before a plan is returned (tests cancel or edit from here)."""

    def length(self) -> int:
        return sum(b - a for _, a, b in self._runs)

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        del unverified
        self.plan_calls.append((offset, limit))
        if self.on_plan is not None:
            self.on_plan(len(self.plan_calls))
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

    def search_plan(self, offset: int, limit: int) -> SearchPlan:
        """Return the plan of `[offset, offset + limit)` with the current `revision` and the document length."""
        return SearchPlan(self.revision, self.length(), self.plan(offset, limit, False))


def whole_file_planner(source: ByteSource) -> FakePlanner:
    """Return a planner for the unedited document over `source`."""
    return FakePlanner([(0, 0, source.length())], {0: source})


def edited_planner(runs: list[tuple[int, int, int]], original: ByteSource, add: ByteSource) -> FakePlanner:
    """Return a planner over `runs` of the original (src 0) and an add store (src 1)."""
    return FakePlanner(runs, {0: original, 1: add})
