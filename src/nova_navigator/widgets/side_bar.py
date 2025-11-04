from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget


class SideBarButton(Widget):
    DEFAULT_CSS = """
    SideBarButton {
        margin:1  0;
        width: 2;
        height: 1;

        &:hover {
            background: $accent;
        }
    }
    """

    _icon: str

    def __init__(self, icon: str) -> None:
        super().__init__()
        self._icon = icon

    def render(self) -> str:
        return self._icon


class SideBar(Widget):
    DEFAULT_CSS = """
    SideBar {
        dock: left;
        height: 100%;
        background: $background-lighten-2;
        width: 2;
    }
    """

    _buttons: list[SideBarButton]
    _container: Vertical

    def __init__(self) -> None:
        super().__init__()

        self._buttons = [
            SideBarButton("⭐"),  # File Explorer
            SideBarButton("🔍"),  # Search
        ]
        self._container = Vertical(
            *self._buttons,
            id="side-bar-container",
        )

    def compose(self) -> ComposeResult:
        yield self._container
