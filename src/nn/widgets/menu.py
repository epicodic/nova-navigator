from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.events import (
    Enter,
    Event,
    Key,
    Leave,
    Mount,
    MouseDown,
    MouseMove,
    MouseUp,
)
from textual.geometry import Offset, Region
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Label, Static


class MenuHeader(Widget):
    DEFAULT_CSS = """
    MenuHeader {
        padding: 0 2;
        content-align: left middle;
        width: auto;
        max-width: 15;
    }

    MenuHeader:hover {
        background: $primary-background;
    }

    MenuHeader:focus {
        background: $accent;
    }

    """

    name = reactive("")
    menu_id = reactive("")

    can_focus = False

    def __init__(self, menu_id="", name=""):
        super().__init__()
        self.name = name
        self.menu_id = menu_id

    def render(self) -> str:
        return self.name

    # async def on_mouse_down(self, event: MouseDown) -> None:
    #     await self.app.get_screen("menu").open_menu(self)

    # async def on_key(self, event: Key) -> None:
    #     if event.key in ("space", "enter", "down"):
    #         await self.app.get_screen("menu").open_menu(self, key=True)
    #         self.can_focus = False
    #         event.stop()
    #     elif event.key == ("escape"):
    #         self.can_focus = False
    #         self.app.set_focus(self.parent.parent.previous_focus)

    # async def on_mouse_release(self, event: Event):
    #     event.stop()
    #     self.app.set_focus(None)

    # async def on_event(self, event: Event) -> None:
    #     return await super().on_event(event)


def get_child_index(parent: Widget, child: Widget) -> int:
    for index, node in enumerate(parent.children):
        if node == child:
            return index
    return -1


class MenuBar(Widget):
    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        width: 100%;
        background: $background-lighten-2;
        height: 1;
    }
    """

    container: Horizontal
    previous_focus: Widget | None = None
    headers: list[Widget] | None = None

    def __init__(
        self,
        *headers: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.container = Horizontal()
        super().__init__(self.container, name=name, id=id, classes=classes, disabled=disabled)
        self.headers = list(headers)

    async def on_mount(self) -> None:
        if self.headers:
            for child in self.headers:
                self.container.mount(child)

    def activate(self) -> None:
        if self.container.children:
            self.previous_focus = self.app.focused
            child = self.container.children[0]
            child.can_focus = True
            child.focus()

    async def on_key(self, event: Key) -> None:
        current = self.app.focused
        if not current:
            return

        index = get_child_index(self.container, current)
        if event.key in ("tab", "right"):
            for i in range(index + 1, len(self.container.children)):
                child = self.container.children[i]
                child.can_focus = True
                child.focus()
                current.can_focus = False
                break
            event.stop()
        elif event.key in ("shift+tab", "left"):
            for i in range(index - 1, -1, -1):
                child = self.container.children[i]
                child.can_focus = True
                child.focus()
                current.can_focus = False
                break
            event.stop()
