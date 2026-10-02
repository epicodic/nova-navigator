"""Pilot tests of the nova_edit search bar, status line and keys (ACT6 task 11)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.core.search import SearchPlan
from nova_editor.search_bar import SearchBar, SearchStatus
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import wait_until

TEXT = "alpha\nneedle one\nbeta\nNEEDLE two\ngamma\n"
GIB = 1024**3


def make_file(tmp_path: Path, text: str = TEXT) -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


def parts(app: NovaEditApp) -> tuple[NovaTextArea, SearchBar, SearchStatus]:
    assert app.editor is not None
    return app.editor, app.query_one(SearchBar), app.query_one(SearchStatus)


async def search_done(pilot: Pilot[None], editor: NovaTextArea) -> None:
    await wait_until(pilot, lambda: not editor.searching)
    await pilot.pause()


async def type_and_enter(pilot: Pilot[None], needle: str) -> None:
    await pilot.press("f7")
    await pilot.press(*needle)
    await pilot.press("enter")


class Throttle:
    """Makes every window plan of the document of `editor` wait for a token."""

    def __init__(self, editor: NovaTextArea, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real = editor.document.search_plan
        self.tokens = threading.Semaphore(0)
        monkeypatch.setattr(editor.document, "search_plan", self.search_plan)

    def search_plan(self, offset: int, limit: int) -> SearchPlan:
        assert self.tokens.acquire(timeout=10)
        return self._real(offset, limit)

    def give(self) -> None:
        self.tokens.release(1_000_000)


@pytest.mark.asyncio
async def test_f7_shows_and_focuses_the_bar(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        _, bar, _ = parts(app)
        assert not bar.display
        await pilot.press("f7")
        assert bar.display
        assert app.focused is bar
        assert bar.placeholder == "Search (case-sensitive)"


@pytest.mark.asyncio
async def test_enter_searches_forward_and_returns_the_focus(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, status = parts(app)
        await type_and_enter(pilot, "needle")
        await search_done(pilot, editor)
        assert editor.selection == Selection((1, 0), (1, 6))
        assert app.focused is editor
        assert not bar.display
        assert status.line == "Found"


@pytest.mark.asyncio
async def test_alt_c_toggles_the_case_and_ignore_case_finds_both(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, _ = parts(app)
        await pilot.press("f7")
        await pilot.press("alt+c")
        assert bar.placeholder == "Search (ignore case)"
        await pilot.press(*"needle", "enter")
        await search_done(pilot, editor)
        await pilot.press("f3")
        await search_done(pilot, editor)
        assert editor.selection == Selection((3, 0), (3, 6))
        await pilot.press("f7")
        await pilot.press("alt+c")
        assert bar.placeholder == "Search (case-sensitive)"


@pytest.mark.asyncio
async def test_f3_and_shift_f3_repeat_with_the_bar_closed(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path, "x ab\nab\nab y\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, _ = parts(app)
        await type_and_enter(pilot, "ab")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 2), (0, 4))
        assert not bar.display
        await pilot.press("f3")
        await search_done(pilot, editor)
        assert editor.selection == Selection((1, 0), (1, 2))
        await pilot.press("f3")
        await search_done(pilot, editor)
        assert editor.selection == Selection((2, 0), (2, 2))
        await pilot.press("shift+f3")
        await search_done(pilot, editor)
        assert editor.selection == Selection((1, 2), (1, 0))


@pytest.mark.asyncio
async def test_wrap_texts(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path, "one\nab\nthree\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        await type_and_enter(pilot, "ab")
        await search_done(pilot, editor)
        assert status.line == "Found"
        await pilot.press("f3")
        await search_done(pilot, editor)
        assert status.line == "Wrapped to the top"
        await pilot.press("shift+f3")
        await search_done(pilot, editor)
        assert status.line == "Wrapped to the bottom"


@pytest.mark.asyncio
async def test_not_found_shows_the_needle_cut_at_40_characters(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        await type_and_enter(pilot, "zzz")
        await search_done(pilot, editor)
        assert status.line == "Not found: zzz"
        assert editor.cursor_location == (0, 0)
        app.search("q" * 60)
        await search_done(pilot, editor)
        assert status.line == "Not found: " + "q" * 40 + chr(0x2026)


@pytest.mark.asyncio
async def test_empty_bar_value_does_nothing(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, status = parts(app)
        await pilot.press("f7", "enter")
        await pilot.pause()
        assert not editor.searching
        assert status.line == ""
        assert bar.display
        assert app.focused is bar


@pytest.mark.asyncio
async def test_a_needle_with_a_line_break_through_the_app_api(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        app.search("alpha\nneedle")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 0), (1, 6))
        assert status.line == "Found"


@pytest.mark.asyncio
async def test_f3_without_a_needle_opens_the_bar(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        _, bar, _ = parts(app)
        await pilot.press("f3")
        assert bar.display
        assert app.focused is bar


@pytest.mark.asyncio
async def test_escape_closes_the_bar(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, _ = parts(app)
        await pilot.press("f7")
        await pilot.press("escape")
        assert not bar.display
        assert app.focused is editor


@pytest.mark.asyncio
async def test_escape_cancels_a_running_search_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        throttle = Throttle(editor, monkeypatch)
        await type_and_enter(pilot, "needle")
        assert editor.searching
        await pilot.press("escape")
        throttle.give()
        await search_done(pilot, editor)
        assert status.line == "Search cancelled"
        assert editor.cursor_location == (0, 0)


@pytest.mark.asyncio
async def test_progress_text_and_cancel_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        throttle = Throttle(editor, monkeypatch)
        app.search("needle")
        assert editor.searching
        editor.post_message(NovaTextArea.SearchProgress(2 * GIB + GIB // 10, 5 * GIB, "forward", editor))
        await pilot.pause()
        assert status.line == "Searching 42% (2.1 of 5.0 GiB), Esc cancels"
        assert status.display
        editor.post_message(NovaTextArea.SearchCancelled("text changed", editor))
        await pilot.pause()
        assert status.line == "Search cancelled: text changed"
        editor.post_message(NovaTextArea.SearchFailed(ValueError("bad needle"), editor))
        await pilot.pause()
        assert status.line == "Search failed: bad needle"
        throttle.give()
        await search_done(pilot, editor)


@pytest.mark.asyncio
async def test_result_text_disappears_after_three_seconds(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _, status = parts(app)
        now = [100.0]
        reads = [0]

        def clock() -> float:
            reads[0] += 1
            return now[0]

        status.clock = clock
        await type_and_enter(pilot, "needle")
        await search_done(pilot, editor)
        assert status.line == "Found"
        now[0] += 2.9
        target = reads[0] + 3
        await wait_until(pilot, lambda: reads[0] >= target)
        assert status.display
        now[0] += 0.2
        await wait_until(pilot, lambda: not status.display)
        assert status.line == ""


@pytest.mark.asyncio
async def test_select_all_is_on_f8_and_ctrl_shift_a_and_f7_is_search(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, bar, _ = parts(app)
        editor.focus()
        await pilot.press("f8")
        assert editor.selected_text == TEXT
        editor.move_cursor((0, 0))
        assert editor.selected_text == ""
        await pilot.press("ctrl+shift+a")
        assert editor.selected_text == TEXT
        await pilot.press("f7")
        assert bar.display
