"""The Go to popup of the editor screen: a line number or a byte offset (S0001 REQ-7, S0002 REQ-16)."""

from __future__ import annotations

import re
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
INVALID = "Not a line number or byte offset (for example 120, @4096, 0x1F00 or 1F00h)"
"""The message row for text that `parse_goto` refuses."""
HEX_HINT = "Byte offset: 0x1F00, 1F00h or @7936"
"""The message row of the popup in the hex view, while nothing is wrong."""
HEX_INVALID = "Not a byte offset (for example 0x1F00, 1F00h or @7936)"
"""The message row in the hex view for text that `parse_goto` refuses."""
HEX_LINE_MESSAGE = "Line numbers are not available in the hex view; use 0x…, …h or @offset"
"""The reason for a line number in the hex view, which has no lines."""

_HEX_PREFIXED = re.compile(r"0[xX]([0-9a-fA-F]+)")
_HEX_SUFFIXED = re.compile(r"([0-9a-fA-F]+)[hH]")


@dataclass
class GotoTarget:
    """Parsed goto target: line number or byte offset."""

    kind: Literal["line", "byte"]
    value: int


def parse_goto(text: str) -> GotoTarget | None:
    """Parse goto input: 'N' for a line, '@N', '0x1F00' or '1F00h' for a byte offset (hex digits in either case).

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

    for form in (_HEX_PREFIXED, _HEX_SUFFIXED):
        found = form.fullmatch(text)
        if found is not None:
            return GotoTarget("byte", int(found.group(1), 16))

    try:
        line_num = int(text)
        if line_num < 0:
            return None
        return GotoTarget("line", line_num)
    except ValueError:
        return None


def hex_goto(target: GotoTarget, length: int) -> int | str:
    """Resolve `target` for the hex view, which has bytes and no lines.

    Args:
        target: The parsed target.
        length: The document length in bytes.

    Returns:
        The byte offset to move the cursor to (`length` is the end-of-document position), or the reason the target is refused:
        a line number has no meaning there and an offset beyond `length` does not exist.
    """
    if target.kind == "line":
        return HEX_LINE_MESSAGE
    if target.value > length:
        return f"byte offset {target.value} is beyond the end of the file ({length} bytes)"
    return target.value


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
        self.hex = False
        """Whether the popup was opened for the hex view."""
        self._message = Static(HINT, id="goto_message", markup=False)
        self.display = False

    def compose(self) -> ComposeResult:
        """Compose the label, the input and the message row."""
        yield Horizontal(Static("Go to:", id="goto_label"), self.input)
        yield self._message

    def open(self, offset: tuple[int, int], *, hex: bool = False) -> None:
        """Show the popup at `offset`, with the hint (the byte offset hint in the hex view), and focus the input with its text selected."""
        self.offset = offset
        self.awaiting = False
        self.hex = hex
        self.input.placeholder = "Byte offset" if hex else "Line, or @byte offset"
        self.show_message(HEX_HINT if hex else HINT, error=False)
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
            self.show_message(HEX_INVALID if self.hex else INVALID, error=True)
            return
        self.awaiting = True
        self.post_message(self.Requested(target))

    def action_close_popup(self) -> None:
        """Escape: ask the screen to close the popup."""
        self.post_message(self.Dismissed())
