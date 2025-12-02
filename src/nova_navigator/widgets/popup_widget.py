from textual import events
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

    def __init__(self, title: str, position: tuple[int, int]) -> None:
        super().__init__()
        self.offset = position
        self.border_title = title

    def _on_mount(self, event: events.Mount) -> None:
        pass

    @property
    def parent_widget(self) -> Widget:
        assert isinstance(self.parent, Widget)
        return self.parent

    # @on(events.DescendantBlur)
    # def _on_descendant_blur(self, event: events.Blur) -> None:
    #     self.remove()
