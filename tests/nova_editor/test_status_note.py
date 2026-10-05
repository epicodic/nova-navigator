"""The note slot of the status line: progress and results of a save, a search or a reload (S0001 REQ-12)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.app import NovaEditApp
from nova_editor.status_line import StatusLine, format_sizes, format_status
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_app_status import state

GIB = 1024**3


def test_a_note_follows_the_file_name() -> None:
    text = format_status(state(note="Saved  notes.txt  4 B"), 200)
    assert text.startswith("notes.txt  Saved  notes.txt  4 B  Ln 12,345  Col 17  ")


def test_no_note_leaves_the_text_as_it_was() -> None:
    assert format_status(state(), 200) == "notes.txt  Ln 12,345  Col 17  Byte 5,678,901 (12%)  No wrap  46,944,995 lines  LF"


def test_the_position_parts_drop_first_at_80_columns_while_a_note_shows() -> None:
    text = format_status(state(note="Saving  1.2 / 5.0 GiB  24 %  Esc cancels"), 80)
    assert text == "notes.txt  Saving  1.2 / 5.0 GiB  24 %  Esc cancels  Ln 12,345  Col 17"


def test_a_note_that_does_not_fit_is_cut_not_dropped() -> None:
    text = format_status(state(note="x" * 50), 40)
    assert text == "notes.txt  " + "x" * 28 + "…"


def test_format_sizes_is_the_old_function() -> None:
    assert format_sizes(int(1.2 * GIB), 5 * GIB) == "1.2 / 5.0 GiB"
    assert format_sizes(5, 5) == "5 / 5 B"


class NoteHost(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.status = StatusLine(lambda: state())

    def compose(self) -> ComposeResult:
        yield self.status


@pytest.mark.asyncio
async def test_set_note_shows_it_and_none_clears_it() -> None:
    app = NoteHost()
    async with app.run_test(size=(100, 5)) as pilot:
        await pilot.pause()
        app.status.set_note("Reloaded")
        await wait_until(pilot, lambda: "Reloaded" in app.status.text)
        assert app.status.note == "Reloaded"
        app.status.set_note(None)
        await wait_until(pilot, lambda: "Reloaded" not in app.status.text)
        assert app.status.note is None


@pytest.mark.asyncio
async def test_a_timed_note_clears_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StatusLine, "NOTE_SECONDS", 0.05)
    app = NoteHost()
    async with app.run_test(size=(100, 5)) as pilot:
        await pilot.pause()
        app.status.set_note("Saved", timed=True)
        await wait_until(pilot, lambda: app.status.note is None)
        await wait_until(pilot, lambda: "Saved" not in app.status.text)


@pytest.mark.asyncio
async def test_a_new_note_replaces_a_timed_one_and_its_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StatusLine, "NOTE_SECONDS", 0.05)
    app = NoteHost()
    async with app.run_test(size=(100, 5)) as pilot:
        await pilot.pause()
        app.status.set_note("Saved", timed=True)
        app.status.set_note("Searching")  # untimed: the timer of "Saved" must not clear it
        fired: list[bool] = []
        # A sentinel timer with a later deadline than the 0.05 s note timer: timers fire in deadline order, so once it has fired the note timer has had its turn.
        app.set_timer(0.2, lambda: fired.append(True))
        await wait_until(pilot, lambda: bool(fired))
        assert app.status.note == "Searching"
        assert "Searching" in app.status.text


def test_save_progress_text_phases_and_units() -> None:
    from nova_editor.status_line import save_progress_text

    assert save_progress_text("writing", int(1.2 * GIB), 5 * GIB) == "Saving  1.2 / 5.0 GiB  24 %  Esc cancels"
    assert save_progress_text("writing", 0, 0) == "Saving  0 / 0 B  100 %  Esc cancels"
    assert save_progress_text("flushing", 5, 5) == "Flushing"
    assert save_progress_text("history", 5, 5) == "Preserving undo history"
    assert save_progress_text("finishing", 5, 5) == "Finishing"


@pytest.mark.asyncio
async def test_ctrl_s_on_an_unmodified_document_shows_no_changes_to_save(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.note == "No changes to save")
        await wait_until(pilot, lambda: "No changes to save" in status.text)


@pytest.mark.asyncio
async def test_f5_shows_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        path.write_text("changed on disk\n")
        await pilot.press("f5")
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.note == "Reloaded")
        assert app.editor.text == "changed on disk\n"
