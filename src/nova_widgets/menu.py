from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from rich.segment import Segment
from rich.style import Style
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.geometry import Offset
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget

BorderStyle = Literal["round", "solid", "heavy"]

BOX_CHARS: dict[
    BorderStyle, tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]
] = {
    "round": (
        ("╭", "─", "╮"),
        ("│", " ", "│"),
        ("├", "─", "┤"),
        ("╰", "─", "╯"),
    ),
    "solid": (
        ("┌", "─", "┐"),
        ("│", " ", "│"),
        ("├", "─", "┤"),
        ("└", "─", "┘"),
    ),
    "heavy": (
        ("┏", "━", "┓"),
        ("┃", " ", "┃"),
        ("┠", "─", "┨"),
        ("┗", "━", "┛"),
    ),
}


class AbstractMenuItem:
    @property
    def disabled(self) -> bool:
        return True


class AbstractSelectableMenuItem(AbstractMenuItem):
    _icon: str | None
    _disabled: bool

    def __init__(self, *, icon: str | None = None, disabled: bool = False) -> None:
        self._icon = icon
        self._disabled = disabled

    @property
    def label(self) -> str:
        raise NotImplementedError

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def icon(self) -> str | None:
        return self._icon


class MenuItem(AbstractSelectableMenuItem):
    _label: str
    _id: str | None
    _action: Callable[[], None] | None
    _shortcut: str | None

    def __init__(
        self,
        label: str,
        *,
        id: str | None = None,
        action: Callable[[], None] | None = None,
        shortcut: str | None = None,
        icon: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(icon=icon, disabled=disabled)
        self._label = label
        self._id = id
        self._action = action
        self._shortcut = shortcut

    @property
    def label(self) -> str:
        return self._label

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def action(self) -> Callable[[], None] | None:
        return self._action

    @property
    def shortcut(self) -> str | None:
        return self._shortcut


class MenuItemSubmenu(AbstractSelectableMenuItem):
    _menu: "Menu"

    def __init__(self, menu: "Menu", *, icon: str | None = None, disabled: bool = False) -> None:
        super().__init__(icon=icon, disabled=disabled)
        self._menu = menu

    @property
    def label(self) -> str:
        assert self._menu.title
        return self._menu.title

    @property
    def menu(self) -> "Menu":
        return self._menu


class MenuItemSeparator(AbstractMenuItem):
    pass


class Menu(Widget, can_focus=True):
    DEFAULT_CSS = """
    Menu {
        overlay: screen;
        position: absolute;
        layer: above;
        offset: 0 0;
        height: 0;
        width: 0;
        background: $primary;

        .highlight {
            background: $accent;
            color: $text;
        }

        .disabled {
            color: $text-disabled;
        }

        .shortcut {
            color: $text-muted;
        }
    }
    """
    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "disabled",
        "highlight",
        "shortcut",
    }
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "select_cursor", "Select", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding(key="escape", action="dismiss", description="Close"),
    ]

    LABEL_SHORTCUT_GAP = 3

    class Dismissed(events.Event):
        menu: "Menu"
        item: MenuItem | None

        def __init__(self, menu: "Menu", item: MenuItem | None) -> None:
            self.menu = menu
            self.item = item
            super().__init__()
            
    # Menu

    _title: str | None
    _items: list[AbstractMenuItem]
    _has_icons: bool
    _item_width: int
    _border_style: BorderStyle
    highlighted = reactive[int | None](None, init=False)

    def __init__(self, title: str | None = None, items: list[AbstractMenuItem] | None = None, **kwargs : Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._items = items or []
        self._border_style: BorderStyle = "solid"
        self._refresh_items()
        
    #####
        
    def set_title(self, title: str) -> None:
        self._title = title

    @property
    def title(self) -> str | None:
        return self._title

    def add_item(
        self,
        label: str,
        *,
        id: str | None = None,
        action: Callable[[], None] | None = None,
        icon: str | None = None,
        shortcut: str | None = None,
        disabled: bool = False,
    ) -> MenuItem:
        item = MenuItem(
            label=label,
            id=id,
            action=action,
            icon=icon,
            shortcut=shortcut,
            disabled=disabled,
        )
        self._items.append(item)
        self._refresh_items()
        return item

    def add_menu(
        self,
        title: str,
        *,
        icon: str | None = None,
        disabled: bool = False,
    ) -> "Menu":
        menu = Menu(title)
        self._items.append(MenuItemSubmenu(menu, icon=icon, disabled=disabled))
        self._refresh_items()
        return menu

    def add_separator(self) -> None:
        self._items.append(MenuItemSeparator())

    def show(self, position: Offset | None = None, parent: Widget | None = None) -> None:
        self.offset = position or self.app.mouse_position
        if parent:
            parent.mount(self)
        else:
            self.app.mount(self)
        self.focus()

    # exclusive/modal execution of the menu
    async def exec(self, position: Offset | None = None) -> MenuItem | None:
        menu_screen = MenuScreen(self)
        return await menu_screen.run(position)
        
        
    ######
    
    def _refresh_items(self) -> None:
        self._has_icons = any(item.icon is not None for item in self._items if isinstance(item, MenuItem))

        self._item_width = 0
        for item in self._items:
            if isinstance(item, MenuItem):
                if item.shortcut is not None:
                    self._item_width = max(
                        self._item_width, len(item.label) + len(item.shortcut) + self.LABEL_SHORTCUT_GAP
                    )
                else:
                    self._item_width = max(self._item_width, len(item.label))
            elif isinstance(item, MenuItemSubmenu):
                self._item_width = max(self._item_width, len(item.label))

        width = self._item_width
        if self._has_icons:
            width += 3  # space for icon and a gap

        self.styles.width = width + 4  # padding
        self.styles.height = len(self._items) + 2
    
    
    
    #####

    def dismiss(self, item: MenuItem | None = None) -> None:
        self.remove()
        self.post_message(self.Dismissed(self, item))

    def render_line(self, y: int) -> Strip:
        if y < 0 or y > len(self._items) + 2:
            return Strip.blank(0)

        style = self.rich_style

        box_chars = BOX_CHARS[self._border_style]
        if y == 0:
            # top border
            left, mid, right = box_chars[0]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])
        if y == len(self._items) + 1:
            # bottom border
            left, mid, right = box_chars[3]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])

        index = y - 1
        item = self._items[index]
        if isinstance(item, MenuItemSeparator):
            left, mid, right = box_chars[2]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])

        assert isinstance(item, (AbstractSelectableMenuItem))

        item_style = style
        if item.disabled:
            item_style += self.get_component_rich_style("disabled")
        elif self.highlighted is not None and self.highlighted == index:
            item_style += self.get_component_rich_style("highlight")

        left, mid, right = box_chars[1]
        segments: list[Segment] = []
        # left border
        segments.append(Segment(left, style))

        fill_size = self._item_width - len(item.label)
        if isinstance(item, MenuItem) and item.shortcut:
            fill_size -= len(item.shortcut) + self.LABEL_SHORTCUT_GAP

        item_text = item.label + " " * fill_size

        segments.append(Segment(" ", item_style))

        if self._has_icons:
            icon_text = ""
            if item.icon:
                icon_text = item.icon
            segments.append(Segment(icon_text.ljust(3), item_style))

        segments.append(Segment(item_text, item_style))

        if isinstance(item, MenuItem) and item.shortcut:
            if not item.disabled:
                shortcut_style = self.get_component_rich_style("shortcut")
                shortcut_style += Style(bgcolor=item_style.bgcolor)
            else:
                shortcut_style = item_style
            segments.append(Segment(" " * self.LABEL_SHORTCUT_GAP + item.shortcut, shortcut_style))

        if isinstance(item, MenuItemSubmenu):
            segments.append(Segment("❯", item_style))  # noqa: RUF001
        else:
            segments.append(Segment(" ", item_style))

        # right border
        segments.append(Segment(right, style))

        return Strip(segments)

    def watch_highlighted(self, old_index: int | None, new_index: int | None) -> None:
        self.refresh()

    def _next_highlighted(self, old_index: int | None, direction: int) -> int | None:
        if old_index is None:
            if direction > 0:
                new_index = 0
            else:
                new_index = len(self._items) - 1
        else:
            new_index = (old_index + direction) % len(self._items)
        checked = 0
        while self._items[new_index].disabled:
            new_index = (new_index + direction) % len(self._items)
            checked += 1
            if checked >= len(self._items):  # all items are disabled or separators
                return None
        return new_index

    def action_cursor_up(self) -> None:
        self.highlighted = self._next_highlighted(self.highlighted, -1)

    def action_cursor_down(self) -> None:
        self.highlighted = self._next_highlighted(self.highlighted, 1)

    @on(events.MouseMove)
    def _on_mouse_move(self, event: events.MouseMove) -> None:
        if event.widget != self:
            return
        index = event.y - 1
        if 0 <= index < len(self._items):
            new_highlighted = index
            if new_highlighted != self.highlighted:
                self.highlighted = new_highlighted

    @on(events.Leave)
    def on_mouse_leave(self, event: events.Leave) -> None:
        self.highlighted = None

    def action_select_cursor(self) -> None:
        self._handle_section()

    @on(events.Click)
    def on_click(self, event: events.Click) -> None:
        self._handle_section()

    def _handle_section(self) -> None:
        if self.highlighted is None:
            return

        item = self._items[self.highlighted]
        if item.disabled:
            return

        if isinstance(item, MenuItem):
            self.dismiss(item)
            return

        if isinstance(item, MenuItemSubmenu):
            item.menu.show(
                position=Offset(self.region.width, self.highlighted),
                parent=self,
            )

    def _on_blur(self, event: events.Blur) -> None:
        super()._on_blur(event)
        if not self.has_focus and not self.has_focus_within:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class MenuScreen(ModalScreen[MenuItem]):
    DEFAULT_CSS = """
    MenuScreen {
        align: center middle;
        background: $background 0%;
    }
    """

    _menu: Menu

    def __init__(
        self,
        menu: Menu,
    ) -> None:
        super().__init__()
        self._menu = menu

    async def run(
        self,
        position: Offset | None = None,
    ) -> MenuItem | None:
        self._menu.offset = position or self.app.mouse_position
        return await self.app.push_screen_wait(screen=self)

    def compose(self) -> ComposeResult:
        yield self._menu
        
    def on_menu_dismissed(self, event: Menu.Dismissed) -> None:
        self.dismiss(event.item)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        # check if the click is outside the ListView
        if not self._menu.region.contains(event.screen_x, event.screen_y):
            self._menu.dismiss(None)

        await super()._on_mouse_down(event)


