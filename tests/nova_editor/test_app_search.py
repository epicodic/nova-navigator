"""Pilot tests of the nova_edit Find popup, its status texts and the search keys (REQ-17, S0001 REQ-8)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.core.search import SearchPlan
from nova_editor.find_popup import FindPopup
from nova_editor.status_line import StatusLine
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import wait_until

TEXT = "alpha\nneedle one\nbeta\nNEEDLE two\ngamma\n"
GIB = 1024**3


def make_file(tmp_path: Path, text: str = TEXT) -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


def parts(app: NovaEditApp) -> tuple[NovaTextArea, FindPopup]:
    return app.editor, app.find_popup


async def search_done(pilot: Pilot[None], editor: NovaTextArea) -> None:
    await wait_until(pilot, lambda: not editor.searching)
    await pilot.pause()


async def type_and_enter(pilot: Pilot[None], needle: str) -> None:
    await pilot.press("ctrl+f")
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
async def test_ctrl_f_shows_and_focuses_the_popup(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        _, popup = parts(app)
        assert not popup.display
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert app.focused is popup.input
        assert popup.case_sensitive


@pytest.mark.asyncio
async def test_enter_searches_forward_and_keeps_the_popup_open(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await type_and_enter(pilot, "needle")
        await search_done(pilot, editor)
        assert editor.selection == Selection((1, 0), (1, 6))
        assert popup.display
        assert app.focused is popup.input
        assert popup.status == "Found"


@pytest.mark.asyncio
async def test_enter_repeats_the_search_and_wraps_to_the_top(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path, "x ab\nab\nab y\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await type_and_enter(pilot, "ab")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 2), (0, 4))
        for expected in (Selection((1, 0), (1, 2)), Selection((2, 0), (2, 2))):
            await pilot.press("enter")
            await search_done(pilot, editor)
            assert editor.selection == expected
            assert popup.status == "Found"
        await pilot.press("enter")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 2), (0, 4))
        assert popup.status == "Wrapped to the top"


@pytest.mark.asyncio
async def test_alt_c_toggles_the_case_and_ignore_case_finds_both(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await pilot.press("ctrl+f")
        await pilot.press("alt+c")
        assert not popup.case_sensitive
        await pilot.press(*"needle", "enter")
        await search_done(pilot, editor)
        await pilot.press("enter")
        await search_done(pilot, editor)
        assert editor.selection == Selection((3, 0), (3, 6))
        await pilot.press("alt+c")
        assert popup.case_sensitive


@pytest.mark.asyncio
async def test_f3_and_shift_f3_repeat_with_the_popup_closed(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path, "x ab\nab\nab y\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await type_and_enter(pilot, "ab")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 2), (0, 4))
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is editor
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
        editor, popup = parts(app)
        await type_and_enter(pilot, "ab")
        await search_done(pilot, editor)
        assert popup.status == "Found"
        await pilot.press("f3")
        await search_done(pilot, editor)
        assert popup.status == "Wrapped to the top"
        await pilot.press("shift+f3")
        await search_done(pilot, editor)
        assert popup.status == "Wrapped to the bottom"


@pytest.mark.asyncio
async def test_not_found_shows_the_needle_cut_at_40_characters(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await type_and_enter(pilot, "zzz")
        await search_done(pilot, editor)
        assert popup.status == "Not found: zzz"
        assert editor.cursor_location == (0, 0)
        app.search("q" * 60)
        await search_done(pilot, editor)
        assert popup.status == "Not found: " + "q" * 40 + chr(0x2026)


@pytest.mark.asyncio
async def test_an_empty_needle_does_nothing(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await pilot.press("ctrl+f", "enter")
        await pilot.pause()
        assert not editor.searching
        assert popup.status == ""
        assert popup.display
        assert app.focused is popup.input


@pytest.mark.asyncio
async def test_a_needle_with_a_line_break_through_the_app_api(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        app.search("alpha\nneedle")
        await search_done(pilot, editor)
        assert editor.selection == Selection((0, 0), (1, 6))
        assert not popup.display
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.note == "Found")  # the popup is closed: the text is a note of the status line


@pytest.mark.asyncio
async def test_f3_without_a_needle_opens_the_popup(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        _, popup = parts(app)
        await pilot.press("f3")
        await wait_until(pilot, lambda: popup.display)
        assert app.focused is popup.input


@pytest.mark.asyncio
async def test_escape_closes_the_popup_and_focuses_the_editor(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await pilot.press("ctrl+f")
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is editor


@pytest.mark.asyncio
async def test_escape_cancels_a_running_search_first_then_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        throttle = Throttle(editor, monkeypatch)
        await type_and_enter(pilot, "needle")
        assert editor.searching
        await pilot.press("escape")
        await pilot.pause()
        assert popup.display  # the first Escape cancels the search
        throttle.give()
        await search_done(pilot, editor)
        await wait_until(pilot, lambda: popup.status == "Search cancelled")
        assert editor.cursor_location == (0, 0)
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        assert app.focused is editor


@pytest.mark.asyncio
async def test_progress_text_and_cancel_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        await pilot.press("ctrl+f")
        throttle = Throttle(editor, monkeypatch)
        app.search("needle")
        assert editor.searching
        editor.post_message(NovaTextArea.SearchProgress(2 * GIB + GIB // 10, 5 * GIB, "forward", editor))
        await wait_until(pilot, lambda: popup.status == "Searching 42% (2.1 of 5.0 GiB), Esc cancels")
        editor.post_message(NovaTextArea.SearchCancelled("text changed", editor))
        await wait_until(pilot, lambda: popup.status == "Search cancelled: text changed")
        editor.post_message(NovaTextArea.SearchFailed(ValueError("bad needle"), editor))
        await wait_until(pilot, lambda: popup.status == "Search failed: bad needle")
        throttle.give()
        await search_done(pilot, editor)


@pytest.mark.asyncio
async def test_a_result_note_clears_itself_when_the_popup_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StatusLine, "NOTE_SECONDS", 0.5)  # long enough for the polling wait below to see the note
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, _ = parts(app)
        status = app.query_one(StatusLine)
        app.search("needle")
        await search_done(pilot, editor)
        await wait_until(pilot, lambda: status.note == "Found")
        await wait_until(pilot, lambda: status.note is None)


@pytest.mark.asyncio
async def test_select_all_is_on_f8_and_ctrl_shift_a_and_ctrl_f_is_search(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor, popup = parts(app)
        editor.focus()
        await pilot.press("f8")
        assert editor.selected_text == TEXT
        editor.move_cursor((0, 0))
        assert editor.selected_text == ""
        await pilot.press("ctrl+shift+a")
        assert editor.selected_text == TEXT
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
