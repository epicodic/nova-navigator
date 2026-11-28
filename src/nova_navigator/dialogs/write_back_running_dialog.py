"""WriteBackRunningDialog — the choice offered by the quit key while a quit waits for a write-back."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.widgets import Label

from nova_widgets import ButtonSpec, Dialog, Response


class WriteBackRunningDialog(Dialog):
    """Ask whether to keep waiting for a running write-back, or to quit and keep the local copies.

    Answers ``Response.IGNORE`` for "Quit and keep files" and ``Response.CANCEL`` for "Keep waiting".
    """

    DEFAULT_CSS: ClassVar[str] = """
    WriteBackRunningDialog {
        #dialog_box {
            width: 70%;
        }

        Label {
            width: 100%;
        }
    }
    """

    def __init__(self, names: list[str], directory: Path) -> None:
        super().__init__(
            title="Write-back still running",
            buttons=[
                ButtonSpec(Response.CANCEL, label="Keep waiting", variant="primary"),
                ButtonSpec(Response.IGNORE, label="Quit and keep files", variant="default"),
            ],
        )
        # No accept button: only pressing "Quit and keep files" itself quits, never Enter beside the buttons.
        self._button_accept = None
        self._names = names
        self._directory = directory
        self._obsolete = False

    def compose_content(self) -> ComposeResult:
        # markup=False: a file or directory name may contain brackets
        yield Label(f"Still writing back: {', '.join(self._names)}", markup=False)
        yield Label("Quitting now stops it, and any other running file operation. The source may not contain your changes.")
        yield Label(f"Your local copies stay in {self._directory}", markup=False)

    def withdraw(self) -> None:
        """Dismiss the dialog as "Keep waiting", now or as soon as it is on top."""
        self._obsolete = True
        if self.is_active:
            self.dismiss(Response.CANCEL)

    def on_screen_resume(self) -> None:
        if self._obsolete:
            self.dismiss(Response.CANCEL)
