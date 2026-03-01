from textual.widget import Widget


class Separator(Widget):
    """A thin horizontal line for visually separating content."""

    DEFAULT_CSS = """
    Separator {
        height: 1;
        margin: 0;
        padding: 0;
        color: $text-disabled;
    }
    """

    def render(self) -> str:
        return "─" * self.size.width
