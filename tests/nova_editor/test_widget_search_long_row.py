"""Pilot tests of placing a search match exactly in unresolved and long rows (ACT6 task 10): pending, never provisional, one `SearchFound`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from textual.message import Message
from textual.pilot import Pilot

from nova_editor.core import ByteSource
from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    TrickleSource,
    gated_source,
    open_with_gated_line_scan,
    release_line_scan,
    wait_until,
)
from tests.nova_editor.test_widget_search import TERMINALS, SearchHost

LONG_ROW = ("0123456789" * 400)[:3000] + "NEEDLE" + ("0123456789" * 400)[3006:]
"""A 4,000-byte row (long under the lowered thresholds) with a needle at byte 3,000."""

ROWS = ["head line", LONG_ROW, "next row"]


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


def _as_source(source: TrickleSource) -> ByteSource:
    return source


def _long_area(tmp_path: Path, *, wrap: bool) -> tuple[NovaTextArea, TrickleSource]:
    path = tmp_path / "long.txt"
    path.write_text("\n".join(ROWS), encoding="utf-8")
    source = TrickleSource(path, budget=2)
    return NovaTextArea.open(_as_source(source), soft_wrap=wrap, config=_config()), source


async def settle_until(pilot: Pilot[None], area: NovaTextArea, condition: Callable[[], bool], allowed: tuple[Selection, ...]) -> None:
    """Pause until `condition()` holds; the selection is one of `allowed` (unchanged or exact, never an estimate) at every step."""
    for _ in range(500):
        assert area.selection in allowed
        if condition():
            return
        await pilot.pause(0.02)
    msg = "condition not reached"
    raise AssertionError(msg)


def _found(host: SearchHost) -> list[NovaTextArea.SearchFound]:
    return [message for message in host.terminals() if isinstance(message, NovaTextArea.SearchFound)]


def _kinds(messages: list[Message]) -> list[str]:
    return [type(message).__name__ for message in messages]


@pytest.mark.asyncio
async def test_match_beyond_the_line_frontier_stays_pending_then_selects_exactly(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        before = area.selection
        assert area.search("row 2500") is True
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        assert area.pending_progress is not None
        assert 0.0 < area.pending_progress < 1.0
        assert not area.searching
        for _ in range(5):
            await pilot.pause(0.02)
            assert area.selection == before
        assert not host.terminals()
        assert "JumpProgress" in host.names()
        release_line_scan(area)
        exact = Selection((2500, 0), (2500, 8))
        await settle_until(pilot, area, lambda: bool(_found(host)), (before, exact))
        await pilot.pause()
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == exact
        assert area.selected_text == "row 2500"
        assert (done.row, done.column, done.wrapped) == (2500, 8, False)
        assert area.pending_progress is None
        assert "JumpCompleted" not in host.names()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True])
async def test_match_in_a_long_row_waits_for_the_long_index_and_selects_exactly(tmp_path: Path, wrap: bool) -> None:
    area, source = _long_area(tmp_path, wrap=wrap)
    host = SearchHost(area)
    start_column = 3000
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        before = area.selection
        exact = Selection((1, start_column), (1, start_column + 6))
        assert area.search("NEEDLE") is True
        await settle_until(pilot, area, source.blocked.is_set, (before,))
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        progress = area.pending_progress
        assert progress is not None
        assert 0.0 < progress < 1.0
        assert area.cursor_location == (0, 0)
        assert not host.terminals()
        assert "JumpProgress" in host.names()
        source.release()
        await settle_until(pilot, area, lambda: bool(_found(host)), (before, exact))
        await pilot.pause()
        assert _kinds(host.terminals()) == ["SearchFound"]
        assert area.selection == exact
        assert area.selected_text == "NEEDLE"
        assert area.pending_progress is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True])
async def test_match_with_ends_in_different_rows_resolves_each_end_with_its_row(tmp_path: Path, wrap: bool) -> None:
    area, source = _long_area(tmp_path, wrap=wrap)
    host = SearchHost(area)
    needle = LONG_ROW[-4:] + "\nnext"
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        before = area.selection
        exact = Selection((1, len(LONG_ROW) - 4), (2, 4))
        assert area.search(needle) is True
        await settle_until(pilot, area, source.blocked.is_set, (before,))
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        assert not host.terminals()
        source.release()
        await settle_until(pilot, area, lambda: bool(_found(host)), (before, exact))
        await pilot.pause()
        assert _kinds(host.terminals()) == ["SearchFound"]
        assert area.selection == exact
        (done,) = _found(host)
        data = "\n".join(ROWS).encode()
        assert data[done.start : done.end].decode() == needle  # a range from a long row to the next one cannot be read as text


@pytest.mark.asyncio
async def test_backward_match_in_a_long_row_puts_the_cursor_at_its_start(tmp_path: Path) -> None:
    area, source = _long_area(tmp_path, wrap=False)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((2, 8))
        await pilot.pause()
        before = area.selection
        exact = Selection((1, 3006), (1, 3000))
        assert area.search("NEEDLE", backward=True) is True
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        source.release()
        await settle_until(pilot, area, lambda: bool(_found(host)), (before, exact))
        assert area.selection == exact
        assert area.selected_text == "NEEDLE"


@pytest.mark.asyncio
async def test_escape_cancels_a_pending_placement(tmp_path: Path) -> None:
    area, source = _long_area(tmp_path, wrap=False)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.focus()
        before = area.selection
        assert area.search("NEEDLE") is True
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        await pilot.press("escape")
        await pilot.pause()
        assert area.pending_progress is None
        assert area.selection == before
        source.release()
        for _ in range(10):
            await pilot.pause(0.02)
            assert area.selection == before
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "cancelled"


@pytest.mark.asyncio
async def test_a_new_search_cancels_the_pending_placement(tmp_path: Path) -> None:
    area, source = _long_area(tmp_path, wrap=False)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        before = area.selection
        assert area.search("NEEDLE") is True
        await settle_until(pilot, area, lambda: area.pending_progress is not None, (before,))
        assert area.search("head") is True
        exact = Selection((0, 0), (0, 4))
        await settle_until(pilot, area, lambda: len(host.terminals()) >= 2, (before, exact))
        await pilot.pause()
        source.release()
        await pilot.pause()
        kinds = [(type(message).__name__, getattr(message, "reason", None)) for message in host.terminals()]
        assert kinds == [("SearchCancelled", "replaced"), ("SearchFound", None)]
        assert area.selection == exact
        assert all(isinstance(message, TERMINALS) for message in host.terminals())
