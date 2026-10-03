"""MessageBox — reusable plain-text message/error dialog."""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.widgets import Label

from .dialog import ButtonSpec, DefaultButton, Dialog

MessageBoxVariant = Literal["default", "success", "warning", "error"]


class MessageBox(Dialog):
    """Modal dialog that displays a plain text message with an OK button."""

    DEFAULT_CSS = """
    MessageBox {
        #dialog_box.-success {
            background: $success-darken-3;
        }

        #dialog_box.-warning {
            background: $warning-darken-3;
        }

        #dialog_box.-error {
            background: $error-darken-3;
        }
    }
    """

    _message: str
    _variant: MessageBoxVariant

    def __init__(
        self,
        message: str,
        title: str = "Error",
        buttons: list[ButtonSpec | DefaultButton] | None = None,
        variant: MessageBoxVariant = "default",
    ) -> None:
        super().__init__(title=title, buttons=buttons if buttons is not None else [DefaultButton.OK])
        self._message = message
        self._variant = variant

    def on_mount(self) -> None:
        if self._variant != "default" and self._dialog_box is not None:
            self._dialog_box.add_class(f"-{self._variant}")

    def compose_content(self) -> ComposeResult:
        yield Label(self._message)


# Backward-compatible alias.
MessageDialog = MessageBox
