"""Pilot tests of searching from `NovaTextArea` (ACT6 task 8): the API, the one-terminal-message invariant, the exact selection and Escape."""

from __future__ import annotations

import threading
from itertools import pairwise
from pathlib import Path

import pytest
from textual import events
from textual.message import Message
from textual.pilot import Pilot
from textual.widgets.text_area import Selection

from nova_editor.core.search import SearchError, SearchPlan, SearchSettings
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp, wait_until

BODY = "".join(f"row {n} alpha\n" for n in range(40)) + "a needle here\n" + "".join(f"tail {n}\n" for n in range(20)) + "last needle\n"
KELVIN = chr(0x212A)
TERMINALS = (NovaTextArea.SearchFound, NovaTextArea.SearchNotFound, NovaTextArea.SearchCancelled, NovaTextArea.SearchFailed)


class SearchHost(HostApp):
    """Host that records every search message of the widget."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.found: list[Message] = []

    def _note(self, message: Message) -> None:
        self.found.append(message)

    def on_nova_text_area_search_found(self, message: NovaTextArea.SearchFound) -> None:
        self._note(message)

    def on_nova_text_area_search_not_found(self, message: NovaTextArea.SearchNotFound) -> None:
        self._note(message)

    def on_nova_text_area_search_cancelled(self, message: NovaTextArea.SearchCancelled) -> None:
        self._note(message)

    def on_nova_text_area_search_failed(self, message: NovaTextArea.SearchFailed) -> None:
        self._note(message)

    def on_nova_text_area_search_progress(self, message: NovaTextArea.SearchProgress) -> None:
        self._note(message)

    def terminals(self) -> list[Message]:
        return [message for message in self.found if isinstance(message, TERMINALS)]


async def settle(pilot: Pilot[None], area: NovaTextArea) -> None:
    """Wait until no search runs, then let the posted messages arrive."""
    await wait_until(pilot, lambda: not area.searching)
    await pilot.pause()


def offset_of(text: str, needle: str, nth: int = 0) -> int:
    """Byte offset of the `nth` occurrence of `needle` in `text`."""
    index = -1
    for _ in range(nth + 1):
        index = text.index(needle, index + 1)
    return len(text[:index].encode())


@pytest.mark.asyncio
async def test_found_selects_the_match_and_shows_it() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search("needle") is True
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == Selection((40, 2), (40, 8))
        assert (done.start, done.end) == (offset_of(BODY, "needle"), offset_of(BODY, "needle") + 6)
        assert (done.row, done.column, done.wrapped) == (40, 8, False)
        assert not area.searching
        top, bottom = area._visible_line_indices
        assert top <= 40 < bottom


@pytest.mark.asyncio
async def test_backward_selects_with_the_anchor_at_the_end() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((50, 0))
        assert area.search("needle", backward=True)
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == Selection((40, 8), (40, 2))
        assert (done.row, done.column) == (40, 2)


@pytest.mark.asyncio
async def test_not_found_leaves_cursor_and_selection() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((3, 1))
        area.move_cursor((3, 4), select=True)
        assert area.search("absent")
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchNotFound)
        assert done.needle == "absent"
        assert area.selection == Selection((3, 1), (3, 4))
        assert area.cursor_location == (3, 4)


@pytest.mark.asyncio
async def test_wrap_reports_wrapped_and_no_wrap_reports_not_found() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((61, 11))  # the end of the last row
        assert area.search("row 5 ", wrap=False)
        await settle(pilot, area)
        assert isinstance(host.terminals()[-1], NovaTextArea.SearchNotFound)
        assert area.search("row 5 ")
        await settle(pilot, area)
        done = host.terminals()[-1]
        assert isinstance(done, NovaTextArea.SearchFound)
        assert done.wrapped
        assert area.selection == Selection((5, 0), (5, 6))


@pytest.mark.asyncio
async def test_repeat_forward_and_backward() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.search("needle")
        await settle(pilot, area)
        area.search("needle")
        await settle(pilot, area)
        assert area.selection == Selection((61, 5), (61, 11))
        area.search("needle", backward=True)
        await settle(pilot, area)
        assert area.selection == Selection((40, 8), (40, 2))
        assert [m.wrapped for m in host.terminals() if isinstance(m, NovaTextArea.SearchFound)] == [False, False, False]


@pytest.mark.asyncio
async def test_overlapping_matches_are_found_twice_then_wrap() -> None:
    area = NovaTextArea(text="aaaa")
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        selections = []
        for _ in range(3):
            area.search("aa")
            await settle(pilot, area)
            selections.append(area.selection)
        assert selections == [Selection((0, 0), (0, 2)), Selection((0, 2), (0, 4)), Selection((0, 0), (0, 2))]
        found = [m for m in host.terminals() if isinstance(m, NovaTextArea.SearchFound)]
        assert [m.wrapped for m in found] == [False, False, True]


@pytest.mark.asyncio
async def test_case_insensitive_finds_the_kelvin_sign() -> None:
    area = NovaTextArea(text=f"one {KELVIN}ey\n")
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.search("k", case_sensitive=False)
        await settle(pilot, area)
        assert area.selection == Selection((0, 4), (0, 5))
        area.move_cursor((0, 0))
        area.search("k")
        await settle(pilot, area)
        assert isinstance(host.terminals()[-1], NovaTextArea.SearchNotFound)


@pytest.mark.asyncio
@pytest.mark.parametrize("needle", ["", "a\ud800b"])
async def test_invalid_needle_posts_failed_and_starts_nothing(needle: str) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.search(needle) is False
        assert not area.searching
        await pilot.pause()
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFailed)
        assert isinstance(done.error, SearchError)


@pytest.mark.asyncio
async def test_a_closed_widget_does_not_search() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.close()
        assert area.search("needle") is False
        await pilot.pause()
        assert host.found == []


class Throttle:
    """Replaces `search_plan` of the document of a widget: each call waits for a token (or the timeout)."""

    def __init__(self, area: NovaTextArea, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real = area.document.search_plan
        self.tokens = threading.Semaphore(0)
        monkeypatch.setattr(area.document, "search_plan", self.search_plan)

    def search_plan(self, offset: int, limit: int) -> SearchPlan:
        assert self.tokens.acquire(timeout=10)
        return self._real(offset, limit)

    def give(self, count: int = 1_000_000) -> None:
        self.tokens.release(count)


@pytest.mark.asyncio
async def test_a_second_search_replaces_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        throttle = Throttle(area, monkeypatch)
        assert area.search("needle")
        assert area.search("tail 3")
        throttle.give()
        await settle(pilot, area)
        terminals = host.terminals()
        assert [type(m) for m in terminals] == [NovaTextArea.SearchCancelled, NovaTextArea.SearchFound]
        first = terminals[0]
        assert isinstance(first, NovaTextArea.SearchCancelled)
        assert first.reason == "replaced"
        assert area.selection == Selection((44, 0), (44, 6))


@pytest.mark.asyncio
async def test_progress_is_monotonic_throttled_and_never_after_the_terminal_message(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "".join(f"line {n:05d} filler filler filler\n" for n in range(2000))  # about 64 KiB
    area = NovaTextArea(text=text)
    host = SearchHost(area)
    now = [0.0]
    monkeypatch.setattr(area, "search_clock", lambda: now[0])
    monkeypatch.setattr(NovaTextArea, "search_settings", SearchSettings(chunk=4, progress_interval=0.0))
    stamps: list[float] = []

    def stamp(message: Message) -> None:
        if isinstance(message, NovaTextArea.SearchProgress):
            stamps.append(now[0])

    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        throttle = Throttle(area, monkeypatch)
        assert area.search("absent")
        seen = 0
        for _ in range(40):
            throttle.give(3)
            now[0] += 0.05
            await pilot.pause()
            for message in host.found[seen:]:
                stamp(message)
            seen = len(host.found)
        progress = [m for m in host.found if isinstance(m, NovaTextArea.SearchProgress)]
        assert len(progress) >= 5
        assert len(progress) <= 21  # 2 fake seconds, at most 10 per second
        assert all(later - earlier >= 0.1 - 1e-9 for earlier, later in pairwise(stamps))
        throttle.give()
        await settle(pilot, area)
        await pilot.pause(0.1)
        assert [type(m) for m in host.found if isinstance(m, TERMINALS)] == [NovaTextArea.SearchNotFound]
        assert isinstance(host.found[-1], NovaTextArea.SearchNotFound)
        done = [m.done for m in progress]
        assert done == sorted(done)
        assert all(m.total == len(text.encode()) for m in progress)


@pytest.mark.asyncio
async def test_a_match_is_found_in_the_original_and_in_edited_text() -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("z", "q", "x", "k", "w")
        assert area.search("qxkw", backward=True) is True  # the typed text sits before the cursor
        await settle(pilot, area)
        assert area.selection == Selection((0, 5), (0, 1))
        area.move_cursor((0, 0))
        area.search("alpha")
        await settle(pilot, area)
        assert area.selection == Selection((0, 11), (0, 16))
        area.search("needle")
        await settle(pilot, area)
        assert area.selection == Selection((40, 2), (40, 8))
        assert [type(m) for m in host.terminals()] == [NovaTextArea.SearchFound] * 3


@pytest.mark.asyncio
async def test_escape_cancels_a_running_search(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.focus()
        assert area.check_action("cancel_pending", ()) is False
        throttle = Throttle(area, monkeypatch)
        assert area.search("needle")
        assert area.searching
        assert area.check_action("cancel_pending", ()) is True
        await pilot.press("escape")
        throttle.give()
        await settle(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "cancelled"
        assert area.cursor_location == (0, 0)
        assert area.check_action("cancel_pending", ()) is False


@pytest.mark.asyncio
async def test_search_in_a_file_backed_document(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, lambda: area.indexing_complete)
        assert area.search("last needle")
        await settle(pilot, area)
        assert area.selection == Selection((61, 0), (61, 11))
        assert isinstance(host.terminals()[-1], NovaTextArea.SearchFound)


@pytest.mark.asyncio
async def test_an_outcome_that_cannot_be_posted_does_not_leave_the_widget_searching(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        real_post = area.post_message

        def post(message: Message) -> bool:
            if isinstance(message, events.Callback):
                return False
            return real_post(message)

        monkeypatch.setattr(area, "post_message", post)
        assert area.search("needle") is True
        await wait_until(pilot, lambda: not area.searching)
        assert host.terminals() == []


@pytest.mark.asyncio
async def test_a_thread_that_cannot_start_ends_the_search_with_search_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()

        def refuse(*_args: object) -> None:
            raise RuntimeError("no more threads")

        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", refuse)
            assert area.search("needle") is False
        assert not area.searching
        await pilot.pause()
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SearchFailed)


@pytest.mark.asyncio
async def test_a_defect_while_placing_a_match_posts_one_search_failed_and_drops_the_jump(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        real = area.scroll_cursor_visible

        def broken(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("injected defect")

        monkeypatch.setattr(area, "scroll_cursor_visible", broken)
        assert area.search("needle") is True
        await settle(pilot, area)
        assert [type(m) for m in host.terminals()] == [NovaTextArea.SearchFailed]
        monkeypatch.setattr(area, "scroll_cursor_visible", real)
        area._run_jump(notify=True)  # a leftover jump would place the match and post SearchFound now
        await pilot.pause()
        assert [type(m) for m in host.terminals()] == [NovaTextArea.SearchFailed]
        assert area.pending_progress is None
