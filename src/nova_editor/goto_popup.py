"""The Go to popup of the editor screen: a line number or a byte offset (S0001 REQ-7, S0002 REQ-16)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Static

from nova_widgets.popup_widget import PopupWidget

HINT = "Line number, or @ and a byte offset"
"""The message row while nothing is wrong."""
INVALID = "Not a line number or @byte offset (for example 120 or @4096)"
"""The message row for text that `parse_goto` refuses."""


@dataclass
class GotoTarget:
    """Parsed goto target: line number or byte offset."""

    kind: Literal["line", "byte"]
    value: int


def parse_goto(text: str) -> GotoTarget | None:
    """Parse goto input: 'N' for line, '@N' for byte offset.

    Args:
        text: Input text (stripped of surrounding whitespace).

    Returns:
        GotoTarget if valid, None otherwise.
    """
    text = text.strip()
    if not text:
        return None

    if text.startswith("@"):
        try:
            offset = int(text[1:])
            if offset < 0:
                return None
            return GotoTarget("byte", offset)
        except ValueError:
            return None

    try:
        line_num = int(text)
        if line_num < 0:
            return None
        return GotoTarget("line", line_num)
    except ValueError:
        return None


class GotoPopup(PopupWidget, can_focus=True):
    """Inline popup that takes a line number or an `@` byte offset.

    The popup validates the form (`parse_goto`) and posts `Requested`; the screen runs the jump and tells the popup the outcome:
    it hides the popup when the jump completed and calls `reject` when the widget rejected the target.
    Like the navigator's popups it never calls `PopupWidget.close()`, which would focus whatever had the focus at `show()` time.
    """

    WIDTH: ClassVar[int] = 48
    CLOSE_ACTION = PopupWidget.CloseAction.KEEP
    CLOSE_ON_BLUR = False

    DEFAULT_CSS = """
    GotoPopup {
        height: auto;
        max-width: 100%;

        Horizontal {
            height: 1;
        }

        #goto_label {
            width: 8;
        }

        Input {
            width: 1fr;
        }

        #goto_message {
            height: 1;
            color: $text-muted;
        }

        #goto_message.-error {
            color: $error;
        }
    }
    """

    @dataclass
    class Requested(Message):
        """The user pressed Enter on a valid target."""

        target: GotoTarget

    @dataclass
    class Dismissed(Message):
        """The user pressed Escape: the screen closes the popup and focuses the editor."""

    def __init__(self) -> None:
        super().__init__("Go to", (0, 0))
        self.styles.width = self.WIDTH
        self.input = Input(placeholder="Line, or @byte offset", compact=True)
        """The input field."""
        self.awaiting = False
        """Whether a jump that this popup started has not answered yet."""
        self.shown = HINT
        """The text of the message row."""
        self._message = Static(HINT, id="goto_message", markup=False)
        self.display = False

    def compose(self) -> ComposeResult:
        """Compose the label, the input and the message row."""
        yield Horizontal(Static("Go to:", id="goto_label"), self.input)
        yield self._message

    def open(self, offset: tuple[int, int]) -> None:
        """Show the popup at `offset`, with the hint, and focus the input with its text selected."""
        self.offset = offset
        self.awaiting = False
        self.show_message(HINT, error=False)
        self.show()
        self.input.focus()
        self.input.select_all()

    def show_message(self, text: str, *, error: bool) -> None:
        """Set the message row; an error is drawn in the error colour and marks the input invalid."""
        self.shown = text
        self._message.update(text)
        self._message.set_class(error, "-error")
        self.input.set_class(error, "-invalid")

    def reject(self, reason: str) -> None:
        """The widget rejected the target: keep the popup open, show `reason` and select the input so the user can type again."""
        self.awaiting = False
        self.show_message(reason, error=True)
        self.input.select_all()

    def on_focus(self, event: events.Focus) -> None:
        """A click on the popup itself passes the focus to its input."""
        self.input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter: report text that is no target, post `Requested` for a valid one."""
        event.stop()
        target = parse_goto(event.value)
        if target is None:
            self.show_message(INVALID, error=True)
            return
        self.awaiting = True
        self.post_message(self.Requested(target))

    def action_close_popup(self) -> None:
        """Escape: ask the screen to close the popup."""
        self.post_message(self.Dismissed())
