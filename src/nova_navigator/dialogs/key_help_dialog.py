"""Read-only, scrollable list of the effective key bindings (F1)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Label, Static

from nova_navigator.key_help import HelpGroup
from nova_widgets import ButtonSpec, Dialog, Response


class KeyHelpDialog(Dialog):
    """Read-only, scrollable list of key bindings."""

    DEFAULT_CSS = """
    KeyHelpDialog #dialog_box {
        width: 80%;
    }

    KeyHelpDialog #key_help {
        max-height: 24;
    }

    KeyHelpDialog .help-group {
        text-style: bold;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar = [Binding("f1", "dismiss_dialog", "Close", show=False)]

    def __init__(self, groups: list[HelpGroup]) -> None:
        super().__init__("Key help", buttons=[ButtonSpec(Response.OK, label="Close")])
        self.groups = groups

    def compose_content(self) -> ComposeResult:
        children: list[Static] = []
        for group in self.groups:
            children.append(Static(group.title, classes="help-group", markup=False))
            children.extend(Static(f"{row.keys:<24}{row.label}", classes="help-row", markup=False) for row in group.rows)
        yield Label(self._title)
        yield VerticalScroll(*children, id="key_help")

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()
