
from rich.text import Text

from textual.app import ComposeResult, RenderResult
from textual.widget import Widget
from textual.containers import Horizontal
from textual.widgets import Static


class MenuBarItem(Static):

    DEFAULT_CSS = """
    MenuBarItem {
        text-wrap: nowrap;
        text-overflow: ellipsis;
        content-align: center middle;
        width: auto;
        
        &:hover {
            background: $accent;
            color: $text;
        }
    }
    """
    
    def __init__(
        self,
    ):
        super().__init__( content="ITEM")


class MenuBar(Widget):

    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    """

    DEFAULT_CLASSES = ""


    def __init__(
        self,
    ):
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Horizontal(
            MenuBarItem(),
            MenuBarItem(),
            MenuBarItem(),
        )


