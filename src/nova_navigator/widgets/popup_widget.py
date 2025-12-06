from enum import Enum, auto
from typing import ClassVar

from textual import events, on
from textual.binding import Binding, BindingType
from textual.widget import Widget


class PopupWidget(Widget):
    """Custom widget for the overlay."""

    DEFAULT_CSS = """
    PopupWidget {
        layer: above;
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
        NONE = auto()
        HIDE = auto()
        REMOVE = auto()

    _close_action: CloseAction
    _close_on_escape: bool
    _close_on_blur: bool

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

    def _on_mount(self, event: events.Mount) -> None:
        pass

    @property
    def parent_widget(self) -> Widget:
        assert isinstance(self.parent, Widget)
        return self.parent

    def show(self) -> None:
        self.display = True

    def hide(self) -> None:
        self.display = False

    def close(self) -> None:
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
        self._check_action_on_blur()

    def _check_action_on_blur(self) -> None:
        if not self.has_focus and not self.has_focus_within:
            self.close()
