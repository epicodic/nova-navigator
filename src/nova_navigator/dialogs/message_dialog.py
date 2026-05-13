"""MessageDialog — reusable plain-text message/error dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label

from .dialog import ButtonSpec, DefaultButton, Dialog


class MessageDialog(Dialog):
    """Modal dialog that displays a plain text message with an OK button."""

    _message: str

    def __init__(
        self,
        message: str,
        title: str = "Error",
        buttons: list[ButtonSpec | DefaultButton] | None = None,
    ) -> None:
        super().__init__(title=title, buttons=buttons if buttons is not None else [DefaultButton.OK])
        self._message = message

    def compose_content(self) -> ComposeResult:
        yield Label(self._message)
