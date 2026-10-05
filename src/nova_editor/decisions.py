"""The questions that the editor screen asks (REQ-19): each is a `MessageBox` built here and shown, answered and carried out by the screen."""

from __future__ import annotations

from pathlib import Path

from nova_widgets.dialog import ButtonSpec
from nova_widgets.message_box import MessageBox
from nova_widgets.response import Response

from .core.byte_source import ChangeKind

CHANGE_TEXT: dict[ChangeKind, str] = {
    ChangeKind.MODIFIED: "The file changed on disk",
    ChangeKind.TRUNCATED: "The file was truncated on disk",
    ChangeKind.REPLACED: "The file was replaced on disk",
    ChangeKind.DELETED: "The file was deleted on disk",
    ChangeKind.CREATED: "The file was created since the editor started",
    ChangeKind.EXISTS: "The file already exists",
    ChangeKind.UNREADABLE: "The file cannot be read",
}
"""The head line of the question about a file that differs from what the editor expects."""

RELOAD_NOTE = "Reloading discards your edits."
WIDE = "90%"
"""Box width of the questions with three or four buttons (they do not fit the default 50 % at 80 columns)."""


def _cancel(label: str = "Cancel") -> ButtonSpec:
    """The harmless first button: it holds the focus, Enter presses it and Escape answers it."""
    return ButtonSpec(Response.CANCEL, label, "default")


def quit_question(*, save_running: bool, standalone: bool) -> MessageBox:
    """Ask whether to quit (standalone host) or close (embedded host) with unsaved changes or a save that still runs; `Response.DISCARD` leaves."""
    verb = "Quit" if standalone else "Close"
    if save_running:
        message = f"A save is still running. {verb} anyway?"
        leave = ButtonSpec(Response.DISCARD, f"{verb} Anyway", "error")
    else:
        message = f"Discard the unsaved changes and {verb.lower()}?"
        leave = ButtonSpec(Response.DISCARD, f"Discard & {verb}", "error")
    return MessageBox(message, title=verb, buttons=[_cancel(), leave], variant="warning")


def open_question(name: str) -> MessageBox:
    """Ask whether to discard the unsaved changes and open the file `name`; `Response.DISCARD` opens it."""
    return MessageBox(
        f"Discard the unsaved changes and open {name}?",
        title="Open",
        buttons=[_cancel(), ButtonSpec(Response.DISCARD, "Discard & Open", "error")],
        variant="warning",
    )


def reload_question() -> MessageBox:
    """Ask whether to discard the edits and reload the file; `Response.DISCARD` reloads."""
    return MessageBox(
        "Discard the edits and reload?",
        title="Reload",
        buttons=[_cancel(), ButtonSpec(Response.DISCARD, "Discard & Reload", "error")],
        variant="warning",
    )


def file_question(kind: ChangeKind, path: Path | None, *, overwrite: bool, save_as: bool, reload: bool, modified: bool) -> MessageBox:
    """Ask what to do about a file that differs from what the editor expects.

    The buttons, left to right: Cancel (`Keep` for a change of the file), `Reload` (`Response.DISCARD`) when
    `reload`, `Save As…` (`Response.SAVE`) when `save_as`, `Overwrite` (`Response.OVERWRITE`) when `overwrite`.
    `modified` adds the line that says that a reload discards the edits.
    """
    exists = kind in {ChangeKind.EXISTS, ChangeKind.CREATED}
    lines = [CHANGE_TEXT.get(kind, "The file changed")]
    if path is not None:
        lines.append(str(path))
    if reload and modified:
        lines.append(RELOAD_NOTE)
    buttons: list[ButtonSpec | Response] = [_cancel("Cancel" if exists else "Keep")]
    if reload:
        buttons.append(ButtonSpec(Response.DISCARD, "Reload", "error"))
    if save_as:
        buttons.append(ButtonSpec(Response.SAVE, "Save As…", "primary"))
    if overwrite:
        buttons.append(ButtonSpec(Response.OVERWRITE, "Overwrite", "warning"))
    return MessageBox(
        "\n".join(lines),
        title="Overwrite" if exists else "File changed",
        buttons=buttons,
        variant="warning",
        width=WIDE,
    )


def failure_box(title: str, text: str) -> MessageBox:
    """Tell the user that something failed; the only answer is `OK` (and Escape, which answers `None`)."""
    return MessageBox(text, title=title, buttons=[ButtonSpec(Response.OK, "OK", "primary")], variant="error")
