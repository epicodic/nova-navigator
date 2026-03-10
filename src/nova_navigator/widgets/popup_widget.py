from enum import Enum, auto
from typing import ClassVar

from textual import events, on
from textual.binding import Binding, BindingType
from textual.widget import Widget


class PopupWidget(Widget):
    """Base class for popup panels that float over the screen.

    Subclass this and implement `compose()` to add content.
    The widget uses `overlay: screen` to position itself in front of all other content.
    """

    DEFAULT_CSS = """
    PopupWidget {
        overlay: screen;
        position: absolute;
        offset: 0 0;
        width: auto;
        height: auto;
        background: $panel;
        color: white;
        border: solid white;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close_popup", "Close Popup", show=False),
    ]

    class CloseAction(Enum):
        """Controls what happens to the widget when `close()` is called.

        In all cases focus is restored to the previously focused widget first.
        """

        KEEP = auto()  # Leave widget visible and in the DOM.
        HIDE = auto()  # Set display=False; widget stays in the DOM.
        REMOVE = auto()  # Remove the widget from the DOM entirely.

    _close_action: CloseAction
    _close_on_escape: bool
    _close_on_blur: bool
    _saved_focus: Widget | None  # Widget to restore focus to on close.

    def __init__(
        self,
        title: str,
        position: tuple[int, int],
        *,
        close_on_escape: bool = True,
        close_on_blur: bool = True,
        close_action: CloseAction = CloseAction.HIDE,
    ) -> None:
        super().__init__()
        self.offset = position
        self.border_title = title
        self._close_action = close_action
        self._close_on_escape = close_on_escape
        self._close_on_blur = close_on_blur
        self._saved_focus = None

    def _on_mount(self, event: events.Mount) -> None:
        # Capture focus at mount so close() can restore it after first open.
        self._saved_focus = self.app.focused

    def show(self) -> None:
        # Refresh saved focus each time the popup is shown, not just on first mount.
        self._saved_focus = self.app.focused
        self.display = True

    def hide(self) -> None:
        self.display = False

    def close(self) -> None:
        """Restore focus and apply the configured close action."""
        if self._saved_focus:
            self._saved_focus.focus()

        if self._close_action == self.CloseAction.HIDE:
            self.hide()
        elif self._close_action == self.CloseAction.REMOVE:
            self.remove()

    def action_close_popup(self) -> None:
        if self._close_on_escape:
            self.close()

    @on(events.Blur)
    def _on_blur(self, event: events.Blur) -> None:
        self._check_action_on_blur()

    @on(events.DescendantBlur)
    def _on_descendant_blur(self, event: events.Blur) -> None:
        # Also fires when a child widget loses focus, e.g. an input inside the popup.
        self._check_action_on_blur()

    def _check_action_on_blur(self) -> None:
        # Only close if neither this widget nor any of its children has focus.
        # This keeps the popup open while the user interacts with child widgets.
        if self._close_on_blur and not self.has_focus and not self.has_focus_within:
            self.close()
