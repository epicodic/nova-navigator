from __future__ import annotations

from enum import Enum, auto
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events, on
from textual.binding import Binding, BindingType
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

_CLOSE_GLYPH = "🗙"  # cross, 1 cell wide
_MIN_WIDTH_FOR_CLOSE_BTN = 5  # need at least: left-corner + space + glyph + space + right-corner


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

    CLOSE_ACTION: ClassVar[CloseAction] = CloseAction.HIDE
    CLOSE_ON_BLUR: ClassVar[bool] = True
    SHOW_CLOSE_BUTTON: ClassVar[bool] = False

    _close_btn_hovered: bool
    _saved_focus: Widget | None  # Widget to restore focus to on close.

    def __init__(
        self,
        title: str,
        position: tuple[int, int],
    ) -> None:
        super().__init__()
        self.offset = position
        self.border_title = title
        self._close_btn_hovered = False
        self._saved_focus = None

    def _on_mount(self, event: events.Mount) -> None:
        # Capture focus at mount so close() can restore it after first open.
        self._saved_focus = self.app.focused

    def render_lines(self, crop: Region) -> list[Strip]:
        strips = super().render_lines(crop)
        if not self.SHOW_CLOSE_BUTTON:
            return strips
        # The top border is at outer y=0. Check if it falls within this crop.
        border_y = 0
        if not (crop.y <= border_y < crop.y + crop.height):
            return strips
        idx = border_y - crop.y
        strip = strips[idx]
        w = strip.cell_length
        if w < _MIN_WIDTH_FOR_CLOSE_BTN:
            return strips
        # Layout: … ─ ─   ✕   ┐  (space at w-4, glyph at w-3, space at w-2, corner at w-1)
        btn_x = w - 4
        # Extract border style from the segment at that position.
        border_seg_style: RichStyle | None = None
        for seg in strip.crop(btn_x, btn_x + 1):
            border_seg_style = seg.style
            break
        if self._close_btn_hovered:
            glyph_style: RichStyle = RichStyle(reverse=True)
            if border_seg_style is not None:
                glyph_style = border_seg_style + glyph_style
        else:
            glyph_style = border_seg_style  # type: ignore[assignment]
        padded = Strip([Segment(f" {_CLOSE_GLYPH} ", glyph_style)], 3)
        strips[idx] = strip.crop(0, btn_x) + padded + strip.crop(btn_x + 3, w)
        return strips

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

        if self.CLOSE_ACTION == self.CloseAction.HIDE:
            self.hide()
        elif self.CLOSE_ACTION == self.CloseAction.REMOVE:
            self.remove()

    def action_close_popup(self) -> None:
        self.close()

    def _is_close_btn(self, x: int, y: int) -> bool:
        w = self.outer_size.width
        return y == 0 and w - 4 <= x <= w - 2

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self.SHOW_CLOSE_BUTTON and self._is_close_btn(event.x, event.y):
            event.stop()
            self.close()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self.SHOW_CLOSE_BUTTON:
            hovered = self._is_close_btn(event.x, event.y)
            if hovered != self._close_btn_hovered:
                self._close_btn_hovered = hovered
                self.refresh()

    def on_leave(self, event: events.Leave) -> None:
        if self._close_btn_hovered:
            self._close_btn_hovered = False
            self.refresh()

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
        if self.CLOSE_ON_BLUR and not self.has_focus and not self.has_focus_within:
            self.close()
