from textual.widget import Widget


class Footer(Widget):
    DEFAULT_CSS = """
    Footer {
        dock: bottom;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    """

    DEFAULT_CLASSES = ""

    def __init__(
        self,
    ) -> None:
        Widget.__init__(self)
