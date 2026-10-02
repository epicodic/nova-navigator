"""Pilot tests of a search meeting edits, saves, reloads, external changes and a close (ACT6 task 9, design 8).

Every test gates the `nova-search` thread on a `threading.Event` (a wrapper around `search_plan` of the document), so the interleaving of the
search thread and the UI thread is fixed; no test sleeps to wait for a thread.
Exactly one terminal message per started search is asserted in every test (none when the widget is closed).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from textual.message import Message
from textual.pilot import Pilot

from nova_editor.core import ChangeKind, SourceChanged
from nova_editor.core.save import SaveSettings
from nova_editor.core.search import SearchPlan, SearchSettings
from nova_editor.document._document import Selection
from nova_editor.widget import NovaTextArea
from nova_editor.widget._search_run import SearchOutcome
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import Gate, text_of, wait_saved
from tests.nova_editor.test_widget_search import BODY, TERMINALS, SearchHost, settle

GATE_SECONDS = 10.0
HOLD_AT = 4
"""The call of `search_plan` that is held: the first one plans the revision, so this is the third unit."""
SMALL_CHUNKS = SearchSettings(chunk=4, progress_interval=0.0)
FILE_ROWS = 2000
FILE_BODY = "".join(f"line {n:06d} filler\n" for n in range(FILE_ROWS))
FILE_NEEDLE = "line 000300"


class PlanGate:
    """Wraps `search_plan` of a document: the call number `hold_at` waits for `release()`; `fail_once_at` raises `ValueError` once (a released source)."""

    def __init__(self, area: NovaTextArea, monkeypatch: pytest.MonkeyPatch, hold_at: int | None = HOLD_AT, fail_once_at: int | None = None) -> None:
        self._real = area.document.search_plan
        self._hold_at = hold_at
        self._fail_once_at = fail_once_at
        self.calls = 0
        self.reached = threading.Event()
        self.opened = threading.Event()
        monkeypatch.setattr(area.document, "search_plan", self.search_plan)

    def search_plan(self, offset: int, limit: int) -> SearchPlan:
        self.calls += 1
        if self.calls == self._fail_once_at:
            raise ValueError("source released")
        if self.calls == self._hold_at:
            self.reached.set()
            assert self.opened.wait(GATE_SECONDS)
        return self._real(offset, limit)

    def release(self) -> None:
        self.opened.set()


def search_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "nova-search"]


async def threads_gone(pilot: Pilot[None]) -> None:
    await wait_until(pilot, lambda: not search_threads())


def assert_one_terminal(host: SearchHost, started: int = 1) -> list[Message]:
    """The invariant: one terminal message per started search."""
    found = host.terminals()
    assert len(found) == started, [type(message).__name__ for message in found]
    assert all(isinstance(message, TERMINALS) for message in found)
    return found


@pytest.fixture(autouse=True)
def small_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "search_settings", SMALL_CHUNKS)


# --- edits


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["type", "delete", "paste", "undo", "redo"])
async def test_an_edit_during_a_search_is_accepted_and_cancels_it(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.focus()
        if kind in {"undo", "redo"}:
            await pilot.press("z")
            area.history.checkpoint()
        if kind == "redo":
            await pilot.press("ctrl+z")
        if kind == "delete":
            area.selection = Selection((1, 0), (1, 3))
        before_text = text_of(area)
        gate = PlanGate(area, monkeypatch)
        assert area.search("needle")
        await wait_until(pilot, gate.reached.is_set)
        if kind == "type":
            await pilot.press("z")
        elif kind == "delete":
            await pilot.press("delete")
        elif kind == "paste":
            area.app.copy_to_clipboard("zz")
            area.action_paste()
        elif kind == "undo":
            await pilot.press("ctrl+z")
        else:
            await pilot.press("ctrl+y")
        assert text_of(area) != before_text  # the edit was accepted at once
        assert not area.searching
        selection = area.selection
        gate.release()
        await threads_gone(pilot)
        await pilot.pause()
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "text changed"
        assert area.selection == selection  # never a stale offset


@pytest.mark.asyncio
async def test_a_result_for_a_changed_revision_is_dropped_as_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    held: list[SearchOutcome] = []
    ready = threading.Event()
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        real = area._post_search_outcome

        def hold(_run: object, outcome: SearchOutcome) -> None:
            held.append(outcome)
            ready.set()

        monkeypatch.setattr(area, "_post_search_outcome", hold)
        assert area.search("needle")
        await wait_until(pilot, ready.is_set)
        run = area._search_run
        assert run is not None
        assert held[0].result is not None  # the thread found the match on the old text
        area.document.replace_range((0, 0), (0, 0), "zz")  # a change that bypasses the widget (no cancel hook ran)
        real(run, held[0])
        await settle(pilot, area)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "text changed"
        assert area.selection == Selection.cursor((0, 0))


# --- save


@pytest.mark.asyncio
async def test_a_search_continues_through_a_save_and_its_rebase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SaveSettings(chunk=4096, fsync_every=1 << 20))
    monkeypatch.setattr("nova_editor.widget._text_area.SMALL_FILE_LIMIT", 1024)
    path = tmp_path / "doc.txt"
    path.write_text(FILE_BODY)
    area = NovaTextArea.open(path)
    host = SearchHost(area)
    saving = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", saving.io())
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.focus()
        assert area.document.wait_indexed(10)
        await pilot.press("x")
        gate = PlanGate(area, monkeypatch, hold_at=HOLD_AT + 20)
        assert area.search(FILE_NEEDLE)
        await wait_until(pilot, gate.reached.is_set)
        assert area.save() is True
        await wait_until(pilot, saving.reached.is_set)
        assert area.searching  # a save does not cancel a search
        saving.release()
        await wait_saved(pilot, area)
        assert area.searching
        text = text_of(area)
        gate.release()
        await settle(pilot, area)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchFound)
        expected = text.find(FILE_NEEDLE.encode(), 1)
        assert (done.start, done.end) == (expected, expected + len(FILE_NEEDLE))
        assert not done.wrapped
        assert path.read_bytes() == text


@pytest.mark.asyncio
async def test_a_read_that_meets_a_released_source_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        PlanGate(area, monkeypatch, hold_at=None, fail_once_at=3)
        assert area.search("needle")
        await settle(pilot, area)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == Selection((40, 2), (40, 8))


# --- reload, load_text


async def _reload(area: NovaTextArea) -> None:
    assert area.reload() is True


async def _load_text(area: NovaTextArea) -> None:
    area.load_text("brand new text\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [_reload, _load_text], ids=["reload", "load_text"])
async def test_replacing_the_document_cancels_the_search(replace: Callable[[NovaTextArea], Awaitable[None]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        gate = PlanGate(area, monkeypatch)
        assert area.search("needle")
        await wait_until(pilot, gate.reached.is_set)
        await replace(area)
        assert not area.searching
        selection = area.selection
        gate.release()
        await threads_gone(pilot)  # the old document joins the thread when it closes
        await pilot.pause()
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchCancelled)
        assert done.reason == "reloaded"
        assert area.selection == selection


# --- external change


def _large_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("nova_editor.widget._text_area.SMALL_FILE_LIMIT", 1024)
    path = tmp_path / "big.txt"
    path.write_text(FILE_BODY)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("poll", [True, False], ids=["checked-first", "found-by-the-search"])
async def test_an_external_truncation_fails_the_search_and_the_view(poll: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _large_file(tmp_path, monkeypatch)
    area = NovaTextArea.open(path)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.document.wait_indexed(10)
        gate = PlanGate(area, monkeypatch)
        assert area.search(FILE_NEEDLE)
        await wait_until(pilot, gate.reached.is_set)
        os.truncate(path, 0)
        if poll:
            assert area.check_external_change() is ChangeKind.TRUNCATED
        gate.release()
        await settle(pilot, area)
        await threads_gone(pilot)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchFailed)
        assert isinstance(done.error, SourceChanged)
        assert area.check_external_change() is ChangeKind.TRUNCATED  # the view is stale
        assert len(host.source_changed) == 1
        length = area.document.length
        area.focus()
        await pilot.press("x")
        assert area.document.length == length  # edits are locked


@pytest.mark.asyncio
async def test_a_search_of_a_stale_view_fails_through_the_same_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _large_file(tmp_path, monkeypatch)
    area = NovaTextArea.open(path)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.document.wait_indexed(10)
        os.truncate(path, 0)
        assert area.check_external_change() is ChangeKind.TRUNCATED
        assert area.search(FILE_NEEDLE) is True
        await settle(pilot, area)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchFailed)
        assert isinstance(done.error, SourceChanged)
        assert len(host.source_changed) == 1
        await threads_gone(pilot)


# --- close


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["close", "unmount"])
async def test_closing_the_widget_joins_the_thread_and_posts_nothing(how: str, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        gate = PlanGate(area, monkeypatch)
        assert area.search("needle")
        await wait_until(pilot, gate.reached.is_set)
        assert search_threads()
        if how == "close":
            area.close()
        else:
            await area.remove()
        gate.release()
        await threads_gone(pilot)
        await pilot.pause()
        assert not area.searching
        assert_one_terminal(host, started=0)


# --- wrap and resize


@pytest.mark.asyncio
async def test_wrap_and_resize_during_a_search_do_not_change_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=BODY)
    host = SearchHost(area)
    async with host.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        gate = PlanGate(area, monkeypatch)
        assert area.search("needle")
        await wait_until(pilot, gate.reached.is_set)
        area.soft_wrap = True
        await pilot.resize_terminal(20, 6)
        await pilot.pause()
        area.soft_wrap = False
        await pilot.resize_terminal(60, 12)
        await pilot.pause()
        gate.release()
        await settle(pilot, area)
        (done,) = assert_one_terminal(host)
        assert isinstance(done, NovaTextArea.SearchFound)
        assert area.selection == Selection((40, 2), (40, 8))
