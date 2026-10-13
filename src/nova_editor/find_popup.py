"""The Find popup of the editor screen: the needle, the case option, Next and Previous (S0001 REQ-8, S0002 REQ-17)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Input, Static

from nova_editor.core.search import HexNeedleError, parse_hex_needle
from nova_widgets.flat_widgets import Checkbox
from nova_widgets.popup_widget import PopupWidget


class FindPopup(PopupWidget, can_focus=True):
    """Inline popup for a literal search: no replace, no regular expression, no whole word.

    The popup posts `Requested` for Enter, Next and Previous and `Dismissed` for Escape; the screen starts the search and shows the outcome with `set_status`.
    The case option and the Hex option are kept for the session (like the wrap and gutter toggles); `Alt+C` and `Alt+H` flip them.
    With Hex on, the needle is read as hex byte pairs and checked here: a fault is shown in the status row and nothing is posted.
    """

    WIDTH: ClassVar[int] = 56
    CLOSE_ACTION = PopupWidget.CloseAction.KEEP
    CLOSE_ON_BLUR = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("alt+c", "toggle_case", "Match case", show=False),
        Binding("alt+h", "toggle_hex", "Hex", show=False),
    ]

    TEXT_PLACEHOLDER: ClassVar[str] = "Text to find"
    """The placeholder of the needle field with Hex off."""
    HEX_PLACEHOLDER: ClassVar[str] = "Hex bytes, e.g. DE AD BE EF"
    """The placeholder of the needle field with Hex on."""

    DEFAULT_CSS = """
    FindPopup {
        height: auto;
        max-width: 100%;

        Horizontal {
            height: 1;
        }

        #find_label {
            width: 7;
        }

        Input {
            width: 1fr;
        }

        Checkbox {
            width: 1fr;
            height: 1;
        }

        Button {
            margin-left: 1;
        }

        #find_status {
            height: 1;
        }
    }
    """

    @dataclass
    class Requested(Message):
        """Search for `needle`; posted for Enter and Next (`backward` is False) and for Previous."""

        needle: str
        backward: bool
        case_sensitive: bool
        hex: bool = False
        """The needle is hex byte pairs (already checked by the popup)."""

    @dataclass
    class Dismissed(Message):
        """The user pressed Escape: the screen cancels a running search first, else closes the popup and focuses the editor."""

    def __init__(self) -> None:
        super().__init__("Find", (0, 0))
        self.styles.width = self.WIDTH
        self.input = Input(placeholder=self.TEXT_PLACEHOLDER, compact=True)
        """The needle field."""
        self.status = ""
        """The text of the status row (the progress and the result of the search)."""
        self._case = Checkbox("Match case", True, id="find_case", compact=True)
        self._hex = Checkbox("Hex", False, id="find_hex", compact=True)
        self._status = Static("", id="find_status", markup=False)
        self.display = False

    @property
    def case_sensitive(self) -> bool:
        """Whether the next search distinguishes upper and lower case (starts `True`, S0001 REQ-8)."""
        return self._case.value

    @property
    def hex(self) -> bool:
        """Whether the needle is read as hex bytes (starts `False`); independent of the shown view."""
        return self._hex.value

    def compose(self) -> ComposeResult:
        """Compose the needle row, the options row and the status row."""
        yield Horizontal(Static("Find:", id="find_label"), self.input)
        yield Horizontal(
            self._case,
            self._hex,
            Button("Previous", id="find_previous", compact=True),
            Button("Next", id="find_next", compact=True),
        )
        yield self._status

    def open(self, offset: tuple[int, int], needle: str | None) -> None:
        """Show the popup at `offset` with an empty status row; a `needle` pre-fills the input. The input is focused with its text selected."""
        self.offset = offset
        if needle is not None:
            self.input.value = needle
        self.set_status("")
        self.show()
        self.input.focus()
        self.input.select_all()

    def set_status(self, text: str) -> None:
        """Show `text` in the status row."""
        self.status = text
        self._status.update(text)

    def on_focus(self, event: events.Focus) -> None:
        """A click on the popup itself passes the focus to its input."""
        self.input.focus()

    def action_toggle_case(self) -> None:
        """Alt+C: flip the case option."""
        self._case.value = not self._case.value

    def action_toggle_hex(self) -> None:
        """Alt+H: flip the Hex option."""
        self._hex.value = not self._hex.value

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """The Hex option switches the placeholder and clears an earlier fault mark."""
        if event.control is self._hex:
            self.input.placeholder = self.HEX_PLACEHOLDER if self._hex.value else self.TEXT_PLACEHOLDER
            self.input.remove_class("-invalid")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Editing the needle clears the fault mark."""
        self.input.remove_class("-invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter searches forward; the popup stays open so that Enter repeats."""
        event.stop()
        self._request(backward=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Next and Previous."""
        event.stop()
        self._request(backward=event.button.id == "find_previous")

    def _request(self, *, backward: bool) -> None:
        needle = self.input.value
        if not needle:
            return
        if self.hex:
            try:
                parse_hex_needle(needle)
            except HexNeedleError as error:
                self.input.add_class("-invalid")
                self.set_status(str(error))
                return
        self.input.remove_class("-invalid")
        self.post_message(self.Requested(needle, backward, self.case_sensitive, self.hex))

    def action_close_popup(self) -> None:
        """Escape: ask the screen to cancel a running search or close the popup."""
        self.post_message(self.Dismissed())
