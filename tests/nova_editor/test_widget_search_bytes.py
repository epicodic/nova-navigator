"""Pilot tests of the byte needle, `origin` and `place` arguments of `NovaTextArea.search` (S0003 ACT6 commit 2)."""

from __future__ import annotations

import threading

import pytest
from textual.widgets.text_area import Selection

from nova_editor.widget import NovaTextArea
from nova_editor.widget._search_run import SearchOutcome
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_widget_search import SearchHost, settle

BODY = "alpha\nbeta needle\ngamma\ndelta needle\n"
FIRST = BODY.index("needle")
SECOND = BODY.index("needle", FIRST + 1)


@pytest.mark.asyncio
async def test_a_byte_needle_search_posts_found_with_the_range() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search(b"needle") is True
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert (done.start, done.end) == (FIRST, FIRST + 6)
        assert area.selection == Selection((1, 5), (1, 11))


@pytest.mark.asyncio
async def test_not_found_shows_the_needle_as_spaced_upper_hex() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search(b"\xde\xad\xbe\xef") is True
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchNotFound)
        assert done.needle == "DE AD BE EF"


@pytest.mark.asyncio
async def test_origin_overrides_the_selection_origin() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search(b"needle", origin=FIRST + 1, wrap=False) is True
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert done.start == SECOND


@pytest.mark.asyncio
async def test_place_replaces_the_text_placement() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    placed: list[tuple[int, int, bool]] = []
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        before = area.selection
        assert area.search(b"needle", origin=SECOND, backward=True, place=lambda s, e, b: placed.append((s, e, b))) is True
        await settle(pilot, area)
        assert placed == [(FIRST, FIRST + 6, True)]
        assert area.selection == before
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert (done.start, done.end, done.row, done.column) == (FIRST, FIRST + 6, 0, 0)


@pytest.mark.asyncio
async def test_place_is_skipped_when_the_text_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    held: list[SearchOutcome] = []
    ready = threading.Event()
    placed: list[int] = []
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        real = area._post_search_outcome

        def hold(_run: object, outcome: SearchOutcome) -> None:
            held.append(outcome)
            ready.set()

        monkeypatch.setattr(area, "_post_search_outcome", hold)
        assert area.search(b"needle", place=lambda start, _end, _backward: placed.append(start))
        await wait_until(pilot, ready.is_set)
        run = area._search_run
        assert run is not None
        area.document.replace_range((0, 0), (0, 0), "zz")
        real(run, held[0])
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "text changed"
        assert placed == []


@pytest.mark.asyncio
async def test_a_failing_place_posts_one_search_failed() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)

    def broken(_start: int, _end: int, _backward: bool) -> None:
        raise RuntimeError("boom")

    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search(b"needle", place=broken)
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFailed)


@pytest.mark.asyncio
async def test_a_text_needle_with_origin_and_place() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    placed: list[tuple[int, int, bool]] = []
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search("needle", origin=FIRST + 1, place=lambda s, e, b: placed.append((s, e, b)))
        await settle(pilot, area)
        assert placed == [(SECOND, SECOND + 6, False)]
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)


@pytest.mark.asyncio
async def test_without_origin_and_place_nothing_changes() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.selection = Selection((1, 0), (1, 1))
        assert area._search_origin(False) == BODY.index("e", BODY.index("beta"))
        assert area.search("needle")
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == Selection((1, 5), (1, 11))
