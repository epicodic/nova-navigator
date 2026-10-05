"""Open through the modal file dialog (REQ-13, REQ-15): the dialog, the swap, the unsaved-changes question, failures and cancel changing nothing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea
from nova_widgets.file_dialog import FileDialogMode
from tests.nova_editor.dialog_helpers import HomeProvider, answer, box_buttons, box_message, box_title, dialog_directory, has_dialog, open_box, open_file_dialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import Gate
from tests.nova_editor.view_menu import NARROW, view_marks

SIZE = (100, 30)


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def app_for(tmp_path: Path, path: Path | None) -> NovaEditApp:
    """An editor whose file dialogs never look at the real home: the provider's home is `tmp_path/home`."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return NovaEditApp(file_path=path, file_provider=HomeProvider(home))


def listing_index(directory: Path, name: str) -> int:
    """How many `down` presses move the highlight from `..` to `name` (the listing shows `..`, the directories, then the files, each sorted by lower-case name)."""
    entries = sorted((entry for entry in directory.iterdir() if not entry.name.startswith(".")), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
    return [entry.name for entry in entries].index(name) + 1  # the first row is `..`


async def pick(pilot: Pilot[None], directory: Path, name: str) -> None:
    """Move the highlight of the open dialog to `name` and press Enter."""
    await pilot.press(*["down"] * listing_index(directory, name), "enter")


@pytest.mark.asyncio
async def test_ctrl_o_opens_the_dialog_in_the_directory_of_the_current_file(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog.mode is FileDialogMode.OPEN
        assert dialog_directory(dialog) == str(tmp_path)
        assert str(dialog.query_one("#dialog_box").border_title) == "Open"
        assert dialog.query_one("#filename_input", Input).disabled  # Open picks an existing file
        assert app.focused is dialog.query_one("#listing")


@pytest.mark.asyncio
async def test_open_without_a_file_starts_in_the_provider_home(tmp_path: Path) -> None:
    app = app_for(tmp_path, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog_directory(dialog) == str(tmp_path / "home")


@pytest.mark.asyncio
async def test_open_picks_a_file_and_replaces_the_document(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    second = write(tmp_path / "second.txt", "two\ntwo\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        old = app.editor
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: app.editor is not old and app.editor.text == "two\ntwo\n")
        assert not has_dialog(app)  # an unmodified document is replaced without a question
        assert screen.document.file_path == second
        assert app.editor.file_path == second
        assert app.focused is app.editor
        assert app.sub_title == str(second)
        assert len(list(screen.query(NovaTextArea))) == 1  # the old widget is gone
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.text.startswith("second.txt"))


@pytest.mark.asyncio
async def test_the_wrap_and_gutter_settings_carry_over_and_the_new_editor_is_watched(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("f10", "f11")  # wrap on, gutter off (it starts on)
        assert app.editor.soft_wrap
        assert not app.editor.show_line_numbers
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: app.editor.text == "two\n")
        assert app.editor.soft_wrap
        assert not app.editor.show_line_numbers
        assert await view_marks(pilot, screen) == {"Line Numbers": False, "Wrap Mode": True}
        await pilot.press("f10")  # the watchers follow the new editor: the check mark flips with the widget
        assert await view_marks(pilot, screen) == {"Line Numbers": False, "Wrap Mode": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["escape", "cancel button"])
async def test_cancel_and_escape_change_nothing(tmp_path: Path, how: str) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        old = app.editor
        await pilot.press("x", "ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        if how == "escape":
            await pilot.press("escape")
        else:
            await pilot.click("#CANCEL")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.editor is old
        assert old.modified
        assert old.text == "xone\n"
        assert screen.document.file_path == first
        assert app.focused is old


@pytest.mark.asyncio
async def test_open_over_edits_asks_and_cancel_changes_nothing(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        old = app.editor
        await pilot.press("x", "ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: open_box(app) is not None)
        box = open_box(app)
        assert box is not None
        assert box_title(box) == "Open"
        assert box_message(box) == "Discard the unsaved changes and open second.txt?"
        assert box_buttons(box) == ["Cancel", "Discard & Open"]
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.editor is old
        assert old.text == "xone\n"
        assert old.modified
        assert app.focused is old


@pytest.mark.asyncio
async def test_open_over_edits_discard_replaces_the_document(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        old = app.editor
        await pilot.press("x", "ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: open_box(app) is not None)
        await answer(pilot, app, "Discard & Open")
        await wait_until(pilot, lambda: app.editor is not old and app.editor.text == "two\n")
        assert not app.editor.modified
        assert app.focused is app.editor
    assert first.read_text() == "one\n"  # the discarded edit was never written


@pytest.mark.asyncio
async def test_an_unreadable_file_shows_an_error_and_keeps_the_document(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    locked = write(tmp_path / "locked.txt", "secret\n")
    locked.chmod(0)
    if os.access(locked, os.R_OK):
        locked.chmod(0o644)
        pytest.skip("permissions are not enforced for this user")
    app = app_for(tmp_path, first)
    try:
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            old = app.editor
            await pilot.press("ctrl+o")
            await wait_until(pilot, lambda: open_file_dialog(app) is not None)
            await pick(pilot, tmp_path, "locked.txt")
            await wait_until(pilot, lambda: open_box(app) is not None)
            box = open_box(app)
            assert box is not None
            assert box_title(box) == "Open"
            assert box_message(box).startswith("Error loading file")
            await pilot.press("enter")
            await wait_until(pilot, lambda: not has_dialog(app))
            assert app.editor is old
            assert old.text == "one\n"
            assert app.focused is old
    finally:
        locked.chmod(0o644)


@pytest.mark.asyncio
async def test_a_file_that_vanished_shows_an_error_and_keeps_the_document(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    gone = write(tmp_path / "gone.txt", "soon gone\n")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        old = app.editor
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        index = listing_index(tmp_path, "gone.txt")
        gone.unlink()  # the dialog still lists it
        await pilot.press(*["down"] * index, "enter")
        await wait_until(pilot, lambda: open_box(app) is not None)
        box = open_box(app)
        assert box is not None
        assert box_message(box).startswith("Error loading file")
        await pilot.press("enter")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.editor is old
        assert old.file_path == first


@pytest.mark.asyncio
async def test_open_while_a_save_runs_does_not_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = write(tmp_path / "first.txt", "a" * 100 + "\n")
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = app_for(tmp_path, first)
    try:
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            assert app.editor.saving
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.pause()
            assert not has_dialog(app)  # a toast says "A save is running"; no dialog opens
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_the_timing_file_variable_reaches_the_widget_opened_after_the_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    timing = tmp_path / "timing.log"
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        monkeypatch.setenv("NOVA_EDIT_TIMING_FILE", str(timing))  # set after the start: only the Open path can read it
        old = app.editor
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: app.editor is not old and app.editor.text == "two\n")
        editor = app.editor
        assert isinstance(editor, TimedNovaTextArea)
        assert editor.timing_file == str(timing)
        await wait_until(pilot, timing.exists)
        assert timing.read_text().startswith("FIRST_CONTENT ")


@pytest.mark.asyncio
async def test_opening_a_symlink_opens_its_target(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    target = write(tmp_path / "target.txt", "target text\n")
    (tmp_path / "link.txt").symlink_to(target)
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        old = app.editor
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pick(pilot, tmp_path, "link.txt")
        await wait_until(pilot, lambda: app.editor is not old and app.editor.text == "target text\n")


@pytest.mark.asyncio
async def test_the_standalone_app_browses_the_real_file_system_by_default(tmp_path: Path) -> None:
    first = write(tmp_path / "first.txt", "one\n")
    write(tmp_path / "second.txt", "two\n")
    app = NovaEditApp(file_path=first)  # no provider: the local one (DEC-32 A3)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog_directory(dialog) == str(tmp_path)
        await pick(pilot, tmp_path, "second.txt")
        await wait_until(pilot, lambda: app.editor.text == "two\n")
