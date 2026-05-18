"""InputNameDialog — single-line name prompt dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Label

from .dialog import DefaultButton, Dialog


class InputNameDialog(Dialog):
    """Modal dialog that prompts for a name with an optional placeholder."""

    DEFAULT_CSS = """
    InputNameDialog {
        #dialog_box { width: 50; height: auto; }
        #prompt { margin-bottom: 1; }
        #name_input { width: 1fr; }
    }
    """

    _prompt: str
    _initial_value: str

    def __init__(self, title: str, prompt: str, initial_value: str = "") -> None:
        super().__init__(title=title, buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._prompt = prompt
        self._initial_value = initial_value

    def compose_content(self) -> ComposeResult:
        yield Label(self._prompt, id="prompt")
        yield Input(value=self._initial_value, id="name_input")

    def on_mount(self) -> None:
        self.query_one("#name_input", Input).focus()

    @property
    def value(self) -> str:
        """Return the current text in the input field."""
        return self.query_one("#name_input", Input).value
