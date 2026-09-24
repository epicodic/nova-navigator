"""UserMenuInputDialog — asks for all input fields of a user menu entry at once."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.widgets import Input, Label

from .dialog import DefaultButton, Dialog


@dataclass(frozen=True)
class InputField:
    """One labelled input with its initial value."""

    name: str
    prompt: str
    value: str


class UserMenuInputDialog(Dialog):
    """Modal dialog with one input per field; ``values`` holds the result after OK."""

    DEFAULT_CSS = """
    UserMenuInputDialog {
        #dialog_box { width: 60; height: auto; }
        .field-input { width: 1fr; margin-bottom: 1; }
    }
    """

    def __init__(self, title: str, fields: list[InputField]) -> None:
        super().__init__(title=title, buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._fields = fields
        self._values = {field.name: field.value for field in fields}

    def compose_content(self) -> ComposeResult:
        for field in self._fields:
            yield Label(field.prompt)
            yield Input(value=field.value, id=f"field_{field.name}", classes="field-input")

    def on_mount(self) -> None:
        self.query(Input).first().focus()

    def action_accept_dialog(self) -> None:
        self._values = {field.name: self.query_one(f"#field_{field.name}", Input).value for field in self._fields}
        super().action_accept_dialog()

    @property
    def values(self) -> dict[str, str]:
        """Field values keyed by name (initial values until the dialog is accepted)."""
        return dict(self._values)
