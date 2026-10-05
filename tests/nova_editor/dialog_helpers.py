"""Read and answer the modal dialogs of an editor app through the public widget tree, as a user sees them."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Label, Static

from nova_widgets.dialog import Dialog
from nova_widgets.file_dialog import FileDialog
from nova_widgets.file_provider import LocalFileProvider
from nova_widgets.flat_widgets import Button
from nova_widgets.message_box import MessageBox
from nova_widgets.response import Response
from tests.nova_editor.helpers_view import wait_until


class HomeProvider(LocalFileProvider):
    """The local file system with a home directory in the test directory, so that no test reads the real home."""

    def __init__(self, home: Path) -> None:
        self.directory = home

    def home(self) -> Path:
        return self.directory


class BoxHost(App[None]):
    """Shows one dialog and records its answer."""

    def __init__(self, box: MessageBox) -> None:
        super().__init__()
        self.box = box
        self.answers: list[Response | None] = []

    def on_mount(self) -> None:
        self.push_screen(self.box, callback=self.answers.append)


def open_box(app: App[None]) -> MessageBox | None:
    """Return the `MessageBox` on top of the screen stack, or `None`."""
    screen = app.screen
    return screen if isinstance(screen, MessageBox) else None


def open_file_dialog(app: App[None]) -> FileDialog | None:
    """Return the `FileDialog` on top of the screen stack, or `None`."""
    screen = app.screen
    return screen if isinstance(screen, FileDialog) else None


def has_dialog(app: App[None]) -> bool:
    """Whether any modal dialog is on top of the screen stack."""
    return isinstance(app.screen, Dialog)


def box_title(box: MessageBox) -> str:
    """The title in the border of the box."""
    return str(box.query_one("#dialog_box").border_title)


def open_title(app: App[None]) -> str | None:
    """The title of the `MessageBox` on top of the screen stack, or `None` when there is none."""
    box = open_box(app)
    return None if box is None else box_title(box)


def box_message(box: MessageBox) -> str:
    """The message text of the box."""
    return str(box.query_one(Label).content)


def box_buttons(box: MessageBox) -> list[str]:
    """The labels of the buttons, left to right."""
    return [str(button.label) for button in box.query(Button)]


def dialog_directory(dialog: FileDialog) -> str:
    """The directory that the file dialog shows in its path row."""
    return str(dialog.query_one("#path_bar", Static).content)


async def tab_once(pilot: Pilot[None], app: App[None], previous: object) -> None:
    """Press Tab and wait until the focus has moved away from `previous`."""
    await pilot.press("tab")
    await wait_until(pilot, lambda: app.focused is not previous)


async def answer(pilot: Pilot[None], app: App[None], label: str) -> None:
    """Move the focus to the button `label` with Tab (the first button takes the focus when the dialog is shown) and press Enter."""
    box = open_box(app)
    assert box is not None, "no message box is open"
    await wait_until(pilot, lambda: isinstance(app.focused, Button))
    for _ in range(len(box_buttons(box))):
        focused = app.focused
        assert isinstance(focused, Button)
        if str(focused.label) == label:
            break
        await tab_once(pilot, app, focused)
    else:
        msg = f"no button {label!r} in {box_buttons(box)}"
        raise AssertionError(msg)
    await pilot.press("enter")


async def wait_box(pilot: Pilot[None], app: App[None]) -> MessageBox:
    await wait_until(pilot, lambda: open_box(app) is not None)
    box = open_box(app)
    assert box is not None
    await pilot.pause()  # the children of a pushed screen mount in later messages: let them all arrive, then the button row is whole
    await wait_until(pilot, lambda: len(box_buttons(box)) > 0 and box.query("#button_box").first().is_mounted)
    return box
