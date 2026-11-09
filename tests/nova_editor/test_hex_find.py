"""Find in the hex view and the Hex option of the Find popup (S0003 ACT6 commit 3, REQ-17)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets.text_area import Selection

from nova_editor.app import NovaEditApp
from nova_editor.core.search import SearchPlan
from nova_editor.document import LazyDocument
from nova_editor.screen import EditorScreen
from nova_editor.widget import NovaTextArea
from nova_editor.widget._search_run import SearchOutcome, SearchRun
from nova_editor.widget.hex_view import ByteSelection
from nova_widgets.flat_widgets import Checkbox
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_editor_screen_hex import NOTE, app_for, note_of, screen_of, toggle

WIDE = (160, 24)
HITS = (100, 1000, 3000)
SIZE = 3200
"""200 rows of 16 bytes; `DE AD BE EF` sits at `HITS`, the rest is filled with `0x11`."""


def make_data() -> bytes:
    data = bytearray(b"\x11" * SIZE)
    for hit in HITS:
        data[hit : hit + 4] = bytes.fromhex("DEADBEEF")
    return bytes(data)


def make_file(tmp_path: Path, data: bytes | None = None, name: str = "f.bin") -> Path:
    path = tmp_path / name
    path.write_bytes(make_data() if data is None else data)
    return path


class Throttle:
    """Makes every window plan of the document of `editor` wait for a token."""

    def __init__(self, editor: NovaTextArea, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real = editor.document.search_plan
        self.tokens = threading.Semaphore(0)
        self.calls = 0
        """How many plans passed the gate."""
        monkeypatch.setattr(editor.document, "search_plan", self.search_plan)

    def search_plan(self, offset: int, limit: int) -> SearchPlan:
        assert self.tokens.acquire(timeout=10)
        self.calls += 1
        return self._real(offset, limit)

    def give(self, count: int = 1_000_000) -> None:
        self.tokens.release(count)


def selection_of(screen: EditorScreen) -> tuple[int, int] | None:
    selection: ByteSelection | None = screen.document.hex_view.selection
    return None if selection is None else (selection.start, selection.end)


async def find(pilot: Pilot[None], screen: EditorScreen, text: str, *, hex_on: bool | None = None, key: str = "enter") -> None:
    """Open the popup, type `text` and press `key`; `hex_on` sets the Hex box first."""
    popup = screen.find_popup
    if not popup.display:
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
    if hex_on is not None and popup.hex is not hex_on:
        await pilot.press("alt+h")
    popup.input.value = text
    await pilot.press(key)


async def done(pilot: Pilot[None], editor: NovaTextArea) -> None:
    await wait_until(pilot, lambda: not editor.searching)
    await pilot.pause()


async def hex_screen(pilot: Pilot[None], app: NovaEditApp) -> EditorScreen:
    await pilot.pause()
    screen = screen_of(app)
    await toggle(pilot, screen)
    return screen


@pytest.mark.asyncio
async def test_the_find_popup_opens_in_hex_mode_and_has_the_hex_box_off(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: screen.find_popup.display)
        assert note_of(app) != NOTE
        assert not screen.find_popup.hex
        assert [str(box.label) for box in screen.find_popup.query(Checkbox)] == ["Match case", "Hex"]
        assert screen.focused is screen.find_popup.input


@pytest.mark.asyncio
async def test_alt_h_and_a_click_toggle_the_hex_option_and_the_placeholder(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        popup = screen.find_popup
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert popup.input.placeholder == "Text to find"
        await pilot.press("alt+h")
        assert popup.hex
        assert popup.input.placeholder == "Hex bytes, e.g. DE AD BE EF"
        await pilot.click(popup.query_one("#find_hex", Checkbox))
        await pilot.pause()
        assert not popup.hex
        assert popup.input.placeholder == "Text to find"


@pytest.mark.asyncio
async def test_hex_option_survives_close_and_reopen(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        popup = screen.find_popup
        await find(pilot, screen, "", hex_on=True, key="space")
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert popup.hex


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["DE AD BE EF", "deadbeef", "de ad be ef", "D E ADB EEF"])
async def test_hex_needle_with_spaces_selects_the_bytes_in_the_hex_view(tmp_path: Path, text: str) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await find(pilot, screen, text, hex_on=True)
        await done(pilot, screen.document.editor)
        assert selection_of(screen) == (100, 104)
        assert screen.document.hex_view.cursor.offset == 104
        assert screen.find_popup.status == "Found"


@pytest.mark.asyncio
async def test_match_case_has_no_effect_with_hex_on(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: screen.find_popup.display)
        await pilot.press("alt+c")
        assert not screen.find_popup.case_sensitive
        await find(pilot, screen, "de ad be ef", hex_on=True)
        await done(pilot, screen.document.editor)
        assert selection_of(screen) == (100, 104)
        await pilot.press("alt+c")
        assert screen.find_popup.case_sensitive
        await find(pilot, screen, "dE aD be Ef", key="enter")
        await done(pilot, screen.document.editor)
        assert selection_of(screen) == (1000, 1004)


@pytest.mark.asyncio
async def test_next_previous_and_enter_walk_the_matches_in_hex_mode(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        view = screen.document.hex_view
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        await done(pilot, editor)
        assert selection_of(screen) == (100, 104)
        await find(pilot, screen, "DE AD BE EF")
        await done(pilot, editor)
        assert selection_of(screen) == (1000, 1004)
        await pilot.click(screen.find_popup.query_one("#find_next"))
        await done(pilot, editor)
        assert selection_of(screen) == (3000, 3004)
        row = 3004 // view.hex_layout.bytes_per_row
        await pilot.pause()
        top = view.scroll_offset.y
        assert top <= row < top + view.scrollable_content_region.height
        await pilot.click(screen.find_popup.query_one("#find_previous"))
        await done(pilot, editor)
        assert selection_of(screen) == (1000, 1004)
        assert view.cursor.offset == 1000  # backward: the cursor sits at the start


@pytest.mark.asyncio
async def test_wrap_and_not_found_texts_for_a_hex_needle(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        popup = screen.find_popup
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        for _ in range(3):
            await find(pilot, screen, "DE AD BE EF")
            await done(pilot, editor)
        assert selection_of(screen) == (100, 104)
        assert popup.status == "Wrapped to the top"
        await find(pilot, screen, "DE AD BE EF", key="enter")
        await find(pilot, screen, "de ad be ee")
        await done(pilot, editor)
        assert popup.status == "Not found: DE AD BE EE"
        assert selection_of(screen) == (1000, 1004)


@pytest.mark.asyncio
async def test_odd_digits_is_rejected_in_the_popup_and_no_search_starts(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await find(pilot, screen, "ABC", hex_on=True)
        await pilot.pause()
        assert screen.find_popup.status == "Odd number of hex digits (3)"
        assert screen.find_popup.input.has_class("-invalid")
        assert not screen.document.editor.searching
        assert screen.document.needle is None  # no search was started
        assert selection_of(screen) is None
        screen.find_popup.input.value = "ABCD"
        assert not screen.find_popup.input.has_class("-invalid")


@pytest.mark.asyncio
async def test_a_non_hex_character_is_rejected(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await find(pilot, screen, "DE ZZ", hex_on=True)
        await pilot.pause()
        assert screen.find_popup.status == "Not a hex digit: 'Z'"
        assert screen.find_popup.input.has_class("-invalid")
        assert not screen.document.editor.searching
        assert selection_of(screen) is None
        await find(pilot, screen, "DE AD")
        await done(pilot, screen.document.editor)
        assert not screen.find_popup.input.has_class("-invalid")
        assert selection_of(screen) == (100, 102)


@pytest.mark.asyncio
async def test_a_hex_search_works_from_the_text_view_and_selects_in_the_text_view(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, b"xx ab yy\nab\n"))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await find(pilot, screen, "61 62", hex_on=True)
        await done(pilot, screen.document.editor)
        assert screen.document.editor.selection == Selection((0, 3), (0, 5))
        assert not screen.document.hex_mode
        await find(pilot, screen, "61 62")
        await done(pilot, screen.document.editor)
        assert screen.document.editor.selection == Selection((1, 0), (1, 2))


@pytest.mark.asyncio
async def test_hex_off_runs_the_text_search_from_the_hex_view_and_selects_the_bytes(tmp_path: Path) -> None:
    text = "aaé bb\r\nxx\r\ncc"
    data = text.encode()
    app = app_for(tmp_path, make_file(tmp_path, data))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        await find(pilot, screen, "é")
        await done(pilot, editor)
        assert selection_of(screen) == (2, 4)  # two bytes
        screen.search("bb\r\nxx", case_sensitive=True)  # a line break cannot be typed in the popup
        await done(pilot, editor)
        assert selection_of(screen) == (data.index(b"bb"), data.index(b"xx") + 2)
        assert screen.document.hex_mode


@pytest.mark.asyncio
async def test_f3_and_shift_f3_repeat_a_hex_needle_with_the_popup_closed(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        await done(pilot, editor)
        await pilot.press("escape")
        await wait_until(pilot, lambda: not screen.find_popup.display)
        assert screen.document.needle_hex
        await pilot.press("f3")
        await done(pilot, editor)
        assert selection_of(screen) == (1000, 1004)
        await pilot.press("f3")
        await done(pilot, editor)
        assert selection_of(screen) == (3000, 3004)
        await pilot.press("shift+f3")
        await done(pilot, editor)
        assert selection_of(screen) == (1000, 1004)
        assert not screen.find_popup.display
        assert note_of(app) in ("Found", None)


@pytest.mark.asyncio
async def test_f3_without_a_needle_opens_the_popup_in_hex_mode(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        await pilot.press("f3")
        await wait_until(pilot, lambda: screen.find_popup.display)
        assert note_of(app) != NOTE


@pytest.mark.asyncio
@pytest.mark.parametrize("popup_open", [True, False])
async def test_escape_cancels_a_running_hex_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, popup_open: bool) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        throttle = Throttle(editor, monkeypatch)
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        assert editor.searching
        if not popup_open:
            screen.find_popup.hide()
            screen.document.focus_shown()
            await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        throttle.give()
        await done(pilot, editor)
        assert selection_of(screen) is None
        if popup_open:
            await wait_until(pilot, lambda: screen.find_popup.status == "Search cancelled")
            assert screen.find_popup.display
        else:
            await wait_until(pilot, lambda: note_of(app) == "Search cancelled")


@pytest.mark.asyncio
async def test_switching_views_cancels_a_running_hex_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        throttle = Throttle(editor, monkeypatch)
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        assert editor.searching
        screen.find_popup.hide()
        await toggle(pilot, screen)
        throttle.give()
        await done(pilot, editor)
        assert selection_of(screen) is None
        assert not editor.searching


@pytest.mark.asyncio
async def test_an_edit_during_a_hex_search_cancels_it_as_text_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        throttle = Throttle(editor, monkeypatch)
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        assert editor.searching
        throttle.give(1)  # the job takes its revision with the first plan; the edit comes after it
        await wait_until(pilot, lambda: throttle.calls >= 1)
        screen.find_popup.hide()
        screen.document.focus_shown()
        await pilot.press("a", "b")  # an edit in the hex view
        throttle.give()
        await done(pilot, editor)
        await pilot.pause(0.3)
        assert note_of(app) == "Search cancelled: text changed", note_of(app)
        assert selection_of(screen) is None


@pytest.mark.asyncio
async def test_a_match_across_a_piece_boundary_after_a_hex_edit(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, b"\x11" * 64))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        document = editor.document
        document.splice_bytes(10, 11, document._table.add(b"\xde\xad"))  # original, added, original
        await find(pilot, screen, "11 DE AD 11", hex_on=True)
        await done(pilot, editor)
        assert selection_of(screen) == (9, 13)


@pytest.mark.asyncio
async def test_a_match_in_an_unscanned_file_is_selected_without_waiting_for_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LazyDocument, "start_scan", lambda *_: None)
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        editor = screen.document.editor
        await toggle(pilot, screen)
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        await done(pilot, editor)
        assert selection_of(screen) == (100, 104)
        assert editor.pending_progress is None
        assert screen.find_popup.status == "Found"


@pytest.mark.asyncio
async def test_find_and_f3_no_longer_show_the_interim_note(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        screen.document.needle = "a"
        for key in ("f3", "shift+f3", "ctrl+f"):
            await pilot.press(key)
            await pilot.pause(0.1)
            assert note_of(app) != NOTE
        assert screen.find_popup.display


@pytest.mark.asyncio
async def test_reopening_after_a_hex_search_turns_the_hex_box_on_again(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        popup = screen.find_popup
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        await done(pilot, screen.document.editor)
        await pilot.press("alt+h")
        assert not popup.hex
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert popup.hex
        assert popup.input.value == "DE AD BE EF"


@pytest.mark.asyncio
async def test_reopening_after_a_text_search_turns_the_hex_box_off_again(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path, b"hello world " * 100))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        popup = screen.find_popup
        await find(pilot, screen, "world", hex_on=False)
        await done(pilot, screen.document.editor)
        await pilot.press("alt+h")
        assert popup.hex
        await pilot.press("escape")
        await wait_until(pilot, lambda: not popup.display)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: popup.display)
        assert not popup.hex
        assert popup.input.value == "world"


@pytest.mark.asyncio
async def test_a_finished_search_whose_completion_is_queued_is_dropped_by_the_view_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=WIDE) as pilot:
        screen = await hex_screen(pilot, app)
        editor = screen.document.editor
        held: list[tuple[SearchRun, SearchOutcome]] = []
        original = editor._finish_search
        monkeypatch.setattr(editor, "_finish_search", lambda run, outcome: held.append((run, outcome)))
        await find(pilot, screen, "DE AD BE EF", hex_on=True)
        await wait_until(pilot, lambda: bool(held))  # the job is finished, its completion has not been concluded
        screen.find_popup.hide()
        await toggle(pilot, screen)
        for run, outcome in held:
            original(run, outcome)
        await pilot.pause()
        assert selection_of(screen) is None
        assert not editor.searching
