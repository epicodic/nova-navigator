"""Save As through the modal file dialog (REQ-13): the dialog, the prefill, the answers, and cancel changing nothing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.widgets import Input

from nova_editor.app import NovaEditApp
from nova_editor.status_line import StatusLine
from nova_widgets.file_dialog import FileDialogMode
from tests.nova_editor.dialog_helpers import HomeProvider, dialog_directory, has_dialog, open_file_dialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import wait_saved

SIZE = (100, 30)


def make_file(tmp_path: Path, text: str = "one\n") -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


def app_for(tmp_path: Path, path: Path | None) -> NovaEditApp:
    """An editor whose file dialogs never look at the real home: the provider's home is `tmp_path/home`."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return NovaEditApp(file_path=path, file_provider=HomeProvider(home))


@pytest.mark.asyncio
async def test_ctrl_shift_s_opens_the_save_dialog_in_the_files_directory_with_its_name(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog.mode is FileDialogMode.SAVE
        assert dialog_directory(dialog) == str(tmp_path)
        name = dialog.query_one("#filename_input", Input)
        assert name.value == "f.txt"
        assert app.focused is name
        assert str(dialog.query_one("#dialog_box").border_title) == "Save As"


@pytest.mark.asyncio
async def test_typing_a_new_name_saves_there_and_rebinds_the_document(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    other = tmp_path / "other.txt"
    app = app_for(tmp_path, path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("ctrl+e", "ctrl+u", *"other.txt", "enter")
        await wait_until(pilot, other.exists)
        await wait_saved(pilot, app.editor)
        assert other.read_text() == "one\n"
        assert path.read_text() == "one\n"
        assert app.editor.file_path == other
        assert not has_dialog(app)
        assert app.focused is app.editor
        status = app.query_one(StatusLine)
        await wait_until(pilot, lambda: status.text.startswith("other.txt"))


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["escape", "cancel button"])
async def test_cancel_and_escape_change_nothing(tmp_path: Path, how: str) -> None:
    path = make_file(tmp_path)
    app = app_for(tmp_path, path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("ctrl+e", "ctrl+u", *"other.txt")
        if how == "escape":
            await pilot.press("escape")
        else:
            await pilot.click("#CANCEL")
        await wait_until(pilot, lambda: not has_dialog(app))
        await pilot.pause()
        assert sorted(entry.name for entry in tmp_path.iterdir()) == ["f.txt", "home"]
        assert path.read_text() == "one\n"
        assert app.editor.file_path == path
        assert app.editor.modified
        assert app.editor.text == "xone\n"
        assert app.focused is app.editor


@pytest.mark.asyncio
async def test_ctrl_s_without_a_file_opens_the_dialog_in_the_provider_home(tmp_path: Path) -> None:
    app = app_for(tmp_path, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("a", "ctrl+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        dialog = open_file_dialog(app)
        assert dialog is not None
        assert dialog_directory(dialog) == str(tmp_path / "home")
        name = dialog.query_one("#filename_input", Input)
        assert name.value == ""
        assert app.focused is name
        await pilot.press(*"new.txt", "enter")
        target = tmp_path / "home" / "new.txt"
        await wait_until(pilot, target.exists)
        await wait_saved(pilot, app.editor)
        assert target.read_text() == "a"


@pytest.mark.asyncio
async def test_a_file_that_failed_to_load_refuses_ctrl_s_and_opens_the_dialog(tmp_path: Path) -> None:
    path = make_file(tmp_path, "precious")
    path.chmod(0)
    if os.access(path, os.R_OK):
        path.chmod(0o644)
        pytest.skip("permissions are not enforced for this user")
    app = app_for(tmp_path, path)
    try:
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, lambda: open_file_dialog(app) is not None)
            dialog = open_file_dialog(app)
            assert dialog is not None
            assert dialog.query_one("#filename_input", Input).value == "f.txt"
            await pilot.press("escape")
            await wait_until(pilot, lambda: not has_dialog(app))
    finally:
        path.chmod(0o644)
    assert path.read_text() == "precious"


@pytest.mark.asyncio
async def test_a_name_in_a_missing_directory_keeps_the_dialog_open_and_creates_nothing(tmp_path: Path) -> None:
    app = app_for(tmp_path, make_file(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("ctrl+e", "ctrl+u", *"no-such-dir/new.txt", "enter")
        await pilot.pause()
        assert open_file_dialog(app) is not None  # "Invalid path": the provider found no parent directory
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
    assert not (tmp_path / "no-such-dir").exists()
