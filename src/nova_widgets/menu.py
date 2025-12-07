from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from rich.segment import Segment
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.geometry import Offset
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.strip import Strip
from rich.style import Style

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


@dataclass
class MenuItem:
    label: str
    id: str | None = None
    action: Callable[[], None] | None = None
    icon: str | None = None
    shortcut: str | None = None
    disabled: bool = False
    separator: bool = False


class MenuWidget(Widget, can_focus=True):
    DEFAULT_CSS = """
    MenuWidget {
        position: absolute;
        offset: 0 0;
        height: 0;
        width: 0;
        
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
    ]
    
    LABEL_SHORTCUT_GAP = 3
    
    class Selected(events.Event):
        def __init__(self, item: MenuItem, index: int) -> None:
            self.item = item
            self.index = index
            super().__init__()

    _border_style: BorderStyle
    _items: list[MenuItem]
    _has_icons: bool
    _item_width: int
    highlighted = reactive[int | None](None, init=False)

    def __init__(self, items: list[MenuItem], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._border_style: BorderStyle = "solid"
        self._items = items
        self._has_icons = any(item.icon is not None for item in items)
        
        self._item_width = 0
        for item in items:
            if item.separator:
                continue
            if item.shortcut is not None:
                self._item_width = max(self._item_width, len(item.label) + len(item.shortcut) + self.LABEL_SHORTCUT_GAP)
            else:
                self._item_width = max(self._item_width, len(item.label))
        
        width = self._item_width
        if self._has_icons:
            width += 3 # space for icon and a gap
            
        self.styles.width = width + 4 # padding
        self.styles.height = len(self._items) + 2

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
        if item.separator:
            left, mid, right = box_chars[2]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])

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
        if item.shortcut:
            fill_size -= len(item.shortcut) + self.LABEL_SHORTCUT_GAP 
        
        item_text = item.label + " " * fill_size
        
        segments.append(Segment(" ", item_style))
        
        if self._has_icons:
            icon_text = ""
            if item.icon:
                icon_text = item.icon
            segments.append(Segment(icon_text.ljust(3), item_style))
        
        segments.append(Segment(item_text, item_style))
        
        if item.shortcut:
            if not item.disabled:
                shortcut_style = self.get_component_rich_style("shortcut")
                shortcut_style += Style(bgcolor=item_style.bgcolor)
            else:
                shortcut_style = item_style
            segments.append(Segment( " " * self.LABEL_SHORTCUT_GAP + item.shortcut, shortcut_style))
            
        segments.append(Segment(" ", item_style))
        # right border
        segments.append(Segment(right, style))
        
        return Strip(segments)

    def watch_highlighted(self, old_index: int | None, new_index: int | None) -> None:
        self.refresh()

    def _next_highlighted(self, old_index: int | None, direction: int) -> None:
        if old_index is None:
            if direction > 0:
                new_index = 0
            else:
                new_index = len(self._items) - 1
        else:
            new_index = (old_index + direction) % len(self._items)
        checked = 0
        while self._items[new_index].disabled or self._items[new_index].separator:
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
    def on_mouse_move(self, event: events.MouseMove) -> None:
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
        if self.highlighted is not None:
            item = self._items[self.highlighted]
            if not item.disabled and not item.separator:
                self.post_message(self.Selected(item, self.highlighted))


class MenuScreen(ModalScreen[MenuItem]):
    DEFAULT_CSS = """
    MenuScreen {
        align: center middle;
        background: $background 0%;
        MenuWidget {
            background: $primary;
        }
    }
    """

    BINDINGS: ClassVar = [Binding(key="escape", action="dismiss", description="Close")]

    _items: list[MenuItem]
    _position: tuple[int, int]
    _list_view: MenuWidget

    def __init__(
        self,
        items: list[MenuItem],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._items = items

    async def run(
        self,
        position: Offset | None = None,
    ) -> MenuItem | None:
        self._position = position or self.app.mouse_position
        self.focus()
        return await self.app.push_screen_wait(screen=self)

    def compose(self) -> ComposeResult:
        max_width = max(len(item.label) for item in self._items)

        self._list_view = MenuWidget(self._items)
        self._list_view.offset = self._position
        yield self._list_view

    def _action_dismiss(self) -> None:
        self.dismiss()

    def _on_mouse_down(self, event):
        # check if the click is outside the ListView
        if not self._list_view.region.contains(event.screen_x, event.screen_y):
            self._action_dismiss()

        return super()._on_mouse_down(event)

    def on_menu_widget_selected(self, event: MenuWidget.Selected) -> None:
        self.dismiss(event.item)


class Menu:
    _items: list[MenuItem]

    def __init__(self, items: list[MenuItem] | None = None) -> None:
        self._items = items or []

    def add_item(
        self,
        label: str,
        *,
        id: str | None = None,
        action: Callable[[], None] | None = None,
        icon: str | None = None,
        shortcut: str | None = None,
        disabled: bool = False,
    ) -> None:
        self._items.append(MenuItem(label=label, id=id, action=action, icon=icon, shortcut=shortcut, disabled=disabled))
    def add_separator(self) -> None:
        self._items.append(MenuItem(label="", separator=True))

    async def exec(self, position: Offset | None = None) -> MenuItem | None:
        widget = MenuScreen(items=self._items)
        return await widget.run(position)
