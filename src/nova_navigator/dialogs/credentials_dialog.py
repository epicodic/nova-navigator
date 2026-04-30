"""CredentialsDialog — ask the user for SSH username and password."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label

from nova_widgets import Input

from .dialog import DefaultButton, Dialog


@dataclass
class Credentials:
    """Username/password pair returned by :class:`CredentialsDialog`."""

    username: str
    password: str


class CredentialsDialog(Dialog):
    """Modal dialog that prompts for SSH username and password."""

    DEFAULT_CSS = """
    CredentialsDialog {
        #dialog_box { width: 50; height: auto; }
        .cred_row { height: auto; margin-top: 1; }
        .cred_label { width: 14; padding-top: 1; }
        .cred_input { width: 1fr; }
    }
    """

    _initial_username: str

    def __init__(self, hostname: str, username: str = "") -> None:
        super().__init__(title=f"Authentication — {hostname}", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._initial_username = username

    def compose_content(self) -> ComposeResult:
        yield Horizontal(
            Label("Username:", classes="cred_label"),
            Input(value=self._initial_username, id="input_username", classes="cred_input"),
            classes="cred_row",
        )
        yield Horizontal(
            Label("Password:", classes="cred_label"),
            Input(password=True, id="input_password", classes="cred_input"),
            classes="cred_row",
        )

    @property
    def credentials(self) -> Credentials:
        """Return the entered username and password."""
        username = self.query_one("#input_username", Input).value
        password = self.query_one("#input_password", Input).value
        return Credentials(username=username, password=password)
