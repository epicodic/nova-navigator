from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Enter, MouseDown
from textual.widget import Widget
from textual.widgets import Static

from ._menu import Menu


class MenuBarItem(Static):
    DEFAULT_CSS = """
    MenuBarItem {
        text-wrap: nowrap;
        text-overflow: ellipsis;
        content-align: center middle;
        width: auto;

        &:hover {
            background: $panel-lighten-2;
            color: $text;
        }

        &.-active {
            background: $primary;
            color: $text;
        }
    }
    """

    class MouseOver(events.Event):
        item: "MenuBarItem"

        def __init__(self, item: "MenuBarItem") -> None:
            self.item = item
            super().__init__()

    class Selected(events.Event):
        item: "MenuBarItem"

        def __init__(self, item: "MenuBarItem") -> None:
            self.item = item
            super().__init__()

    _menu_bar: "MenuBar"
    _menu: Menu

    def __init__(
        self,
        menu_bar: "MenuBar",
        menu: Menu,
    ):
        self._menu_bar = menu_bar
        self._menu = menu
        super().__init__(content=f" {menu.title} ")

    @property
    def menu(self) -> Menu:
        return self._menu

    def _on_enter(self, event: Enter) -> None:
        self.post_message(self.MouseOver(self))

    async def _on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        event.prevent_default()
        self.post_message(self.Selected(self))


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

    _menus: list[Menu]
    _items: list[MenuBarItem]
    _menu_opened: Menu | None

    def __init__(
        self,
    ):
        super().__init__()
        self._menus = []
        self._menu_opened = None

    def add_menu(self, title: str) -> Menu:
        menu = Menu(title=title)
        self._menus.append(menu)
        return menu

    def compose(self) -> ComposeResult:
        self._items = [MenuBarItem(self, menu) for menu in self._menus]
        yield Horizontal(
            *self._items,
        )

    def _on_menu_bar_item_mouse_over(self, event: MenuBarItem.MouseOver) -> None:
        if self._menu_opened:
            self._invoke_menu(event.item)

    def _on_menu_bar_item_selected(self, event: MenuBarItem.Selected) -> None:
        if not self._menu_opened:
            self._invoke_menu(event.item)
        else:
            self._menu_opened.dismiss()

    def _on_menu_dismissed(self, event: Menu.Dismissed) -> None:
        if self._menu_opened == event.menu:
            self._menu_opened = None
            self._highlight_item(None)

    def _highlight_item(self, item: MenuBarItem | None) -> None:
        for it in self._items:
            it.remove_class("-active")
        if item:
            item.add_class("-active")

    @work
    async def _invoke_menu(self, item: MenuBarItem) -> None:
        self._highlight_item(item)
        item.menu.show(item.region.bottom_left, self)
        self._menu_opened = item.menu
