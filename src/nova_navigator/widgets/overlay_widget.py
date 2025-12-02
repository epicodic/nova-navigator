from textual import events, on
from textual.widget import Widget


class OverlayWidget(Widget, can_focus=True):
    """Custom widget for the overlay."""

    DEFAULT_CSS = """
    OverlayWidget {
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

    def __init__(self, title: str, position: tuple[int, int]) -> None:
        super().__init__()
        self.offset = position
        self.border_title = title

    def _on_mount(self, event: events.Mount) -> None:
        pass
        # return super()._on_mount(_)

    @on(events.DescendantBlur)
    def _on_descendant_blur(self, event: events.Blur) -> None:
        self.remove()
