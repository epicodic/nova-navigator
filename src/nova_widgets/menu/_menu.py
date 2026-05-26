from enum import Enum, auto
from typing import ClassVar, Literal

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.geometry import Offset
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.timer import Timer, TimerCallback
from textual.widget import Widget

from ._action import Action, ActionCollection
from ._symbol_table import SYMBOL_TABLE

BorderStyle = Literal["round", "solid", "heavy"]

BOX_CHARS: dict[BorderStyle, tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]] = {
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


class Menu(Widget, Action, ActionCollection, can_focus=True):
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
        Binding("enter", "select", "Select", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding(key="left", action="cursor_left", description="Cursor left", show=False),
        Binding(key="right", action="cursor_right", description="Cursor right", show=False),
        Binding(key="escape", action="dismiss", description="Close"),
    ]

    # region -------------------------- Constants -----------------------------
    LABEL_SHORTCUT_GAP = 3
    TIMER_DELAY = 0.5  # seconds
    # endregion

    # region --------------------------- Events -------------------------------

    class Highlighted(events.Event):
        menu: "Menu"
        index: int | None

        def __init__(self, menu: "Menu", index: int | None) -> None:
            self.menu = menu
            self.index = index
            super().__init__()

    class Triggered(events.Event):
        menu: "Menu"
        action: Action

        def __init__(self, menu: "Menu", action: Action) -> None:
            self.menu = menu
            self.action = action
            super().__init__()

    class Dismissed(events.Event):
        menu: "Menu"
        dismiss_parents: bool

        def __init__(self, menu: "Menu", dismiss_parents: bool = False) -> None:
            self.menu = menu
            self.dismiss_parents = dismiss_parents
            super().__init__()

    class NavigateLeft(events.Event):
        """Posted when left is pressed in a top-level menu (parent is not a Menu)."""

        menu: "Menu"

        def __init__(self, menu: "Menu") -> None:
            self.menu = menu
            super().__init__()

    class NavigateRight(events.Event):
        """Posted when right is pressed in a top-level menu with no submenu to enter."""

        menu: "Menu"

        def __init__(self, menu: "Menu") -> None:
            self.menu = menu
            super().__init__()

    # endregion

    # region --------------------------- Members ------------------------------
    _parent: Widget | None
    _has_icons: bool
    _item_width: int
    _border_style: BorderStyle
    _opened_submenu: "Menu | None"
    _submenu_timer: Timer | None
    _highlighted: int | None

    # endregion
    # region ----------------------- Public Methods ---------------------------
    def __init__(self, text: str | None = None, *actions: Action, name: str | None = None) -> None:
        Widget.__init__(self)
        Action.__init__(self, text=text, name=name)

        self._parent = None
        self._actions = list(actions)
        self._border_style: BorderStyle = "solid"
        self._submenu_timer = None
        self._highlighted = None
        self._reset()
        self._refresh_items()

    def add(self, *actions: Action) -> None:
        self._actions.extend(list(actions))
        self._refresh_items()

    def add_action(
        self,
        text: str,
        *,
        name: str | None = None,
        action: str | None = None,
        icon: str | None = None,
        shortcut: str | None = None,
        enabled: bool = True,
        checkable: bool = False,
        checked: bool = False,
    ) -> Action:
        a = Action(
            text=text,
            name=name,
            action=action,
            icon=icon,
            shortcut=shortcut,
            enabled=enabled,
            checkable=checkable,
            checked=checked,
        )
        self._add_action(a)
        self._refresh_items()
        return a

    def add_menu(
        self,
        title: str,
        *,
        icon: str | None = None,
        disabled: bool = False,
    ) -> "Menu":
        menu = Menu(title)
        self._actions.append(menu)
        self._refresh_items()
        return menu

    def add_separator(self) -> None:
        self._actions.append(Action(is_separator=True))

    def show(self, position: Offset | None = None, parent: Widget | None = None) -> None:
        self.offset = position or self.app.mouse_position
        self._reset()
        if parent:
            parent.mount(self)
            self._parent = parent
        else:
            self.app.mount(self)
            self._parent = None
        self.focus()

    # exclusive/modal execution of the menu
    async def exec(self, position: Offset | None = None) -> Action | None:
        self._parent = None
        self._reset()
        menu_screen = MenuScreen(self)
        return await menu_screen.run(position)

    def dismiss(self, dismiss_parents: bool = False) -> None:
        if self._opened_submenu:
            self._opened_submenu.dismiss()
            self._opened_submenu = None
        self.remove()
        self.post_message(self.Dismissed(self, dismiss_parents=dismiss_parents))

    def is_in_menu_or_submenus(self, x: int, y: int) -> bool:
        # check if the position is inside this menu or any of its open submenus
        menus = self.get_menu_and_open_submenus()
        regions = [menu.region for menu in menus]
        return any(region.contains(x, y) for region in regions)

    def get_menu_and_open_submenus(self) -> list["Menu"]:
        menus: list[Menu] = [self]
        m = self._opened_submenu
        while m:
            assert m is not None
            menus.append(m)
            m = m._opened_submenu
        return menus

    # endregion
    # region ----------------------------- Actions ----------------------------

    def _action_dismiss(self) -> None:
        self.dismiss()

    def _watch_highlighted(self, old_index: int | None, new_index: int | None) -> None:
        self.refresh()

        if old_index != new_index and self._opened_submenu:
            self._opened_submenu.dismiss()  # close submenu if highlighted item changed
            self._opened_submenu = None

    def _action_cursor_up(self) -> None:
        self._set_highlighted(self._next_highlighted(self._highlighted, -1), Menu._HighlightReason.KEYBOARD)

    def _action_cursor_down(self) -> None:
        self._set_highlighted(self._next_highlighted(self._highlighted, 1), Menu._HighlightReason.KEYBOARD)

    def _action_cursor_left(self) -> None:
        if self._parent and isinstance(self._parent, Menu):
            self.dismiss()
        else:
            self.post_message(self.NavigateLeft(self))

    def _action_cursor_right(self) -> None:
        if self._highlighted is not None:
            action = self._actions[self._highlighted]
            if action.enabled and isinstance(action, Menu):
                self._open_submenu(action, self._highlighted)
                return
        self.post_message(self.NavigateRight(self))

    def _action_select(self) -> None:
        if self._highlighted is None:
            return

        action = self._actions[self._highlighted]
        if not action.enabled or action.is_separator:
            return

        if isinstance(action, Menu):
            self._open_submenu(action, self._highlighted)
            return

        self._triggered(action)

    # endregion

    # region ------------------------ Event Handlers --------------------------

    def _on_menu_triggered(self, event: Triggered) -> None:
        self.remove()  # close this menu as well

    def _on_menu_dismissed(self, event: Dismissed) -> None:
        if self._opened_submenu == event.menu:
            self._opened_submenu = None
        if event.dismiss_parents:
            self.remove()

    def _on_leave(self, event: events.Leave) -> None:
        pos = self.app.mouse_position
        if not self.is_in_menu_or_submenus(pos.x, pos.y):
            self._set_highlighted(None, Menu._HighlightReason.HOVER)

    def _on_blur(self, event: events.Blur) -> None:
        # dismiss menu if we lose focus not to any the submenus
        if not self.has_focus_within:
            self.dismiss(dismiss_parents=True)

    async def _on_click(self, event: events.Click) -> None:
        event.stop()
        self._action_select()

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        event.stop()
        index = event.y - 1
        if 0 <= index < len(self._actions):
            self._set_highlighted(index, Menu._HighlightReason.HOVER)
        else:
            self._set_highlighted(None, Menu._HighlightReason.HOVER)

    def _on_menu_highlighted(self, event: Highlighted) -> None:
        if event.index is not None and event.menu == self._opened_submenu:
            # propagate highlight to parent menu
            for i, action in enumerate(self._actions):
                if isinstance(action, Menu) and action == event.menu:
                    self._set_highlighted(i, Menu._HighlightReason.HOVER)
                    # self._stop_timer()
                    break

    def render_line(self, y: int) -> Strip:
        if y < 0 or y > len(self._actions) + 2:
            return Strip.blank(0)

        style = self.rich_style

        box_chars = BOX_CHARS[self._border_style]
        if y == 0:
            # top border
            left, mid, right = box_chars[0]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])
        if y == len(self._actions) + 1:
            # bottom border
            left, mid, right = box_chars[3]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])

        index = y - 1
        action = self._actions[index]
        if action.is_separator:
            left, mid, right = box_chars[2]
            line = left + mid * (self.size.width - 2) + right
            return Strip([Segment(line, style)])

        item_style = style
        if not action.enabled:
            item_style += self.get_component_rich_style("disabled")
        elif self._highlighted is not None and self._highlighted == index:
            item_style += self.get_component_rich_style("highlight")

        left, mid, right = box_chars[1]
        segments: list[Segment] = []
        # left border
        segments.append(Segment(left, style))

        fill_size = self._item_width - len(action.text)
        if action.shortcut:
            fill_size -= len(str(action.shortcut)) + self.LABEL_SHORTCUT_GAP

        item_text = action.text + " " * fill_size

        if action.checkable:
            kind = "radio" if action.is_exclusive else "checkbox"
            segments.append(Segment(SYMBOL_TABLE[kind][1 if action.checked else 0].glyph, item_style))
        else:
            segments.append(Segment("  ", item_style))

        if self._has_icons:
            icon_glyph = action.icon.glyph if action.icon else ""
            segments.append(Segment(icon_glyph.ljust(3), item_style))

        segments.append(Segment(item_text, item_style))

        if action.shortcut:
            if action.enabled:
                shortcut_style = self.get_component_rich_style("shortcut")
                shortcut_style += Style(bgcolor=item_style.bgcolor)
            else:
                shortcut_style = item_style
            segments.append(Segment(" " * self.LABEL_SHORTCUT_GAP + str(action.shortcut), shortcut_style))

        if isinstance(action, Menu):
            segments.append(Segment(" 🞂", item_style))
        else:
            segments.append(Segment("  ", item_style))

        # right border
        segments.append(Segment(right, style))

        return Strip(segments)

    # endregion
    # region ----------------------- Private Methods --------------------------

    def _triggered(self, action: Action) -> None:
        self.remove()
        if action.checkable:
            action.set_checked(not action.checked)
        self.post_message(self.Triggered(self, action))

    def _reset(self) -> None:
        self._opened_submenu = None
        self._set_highlighted(None, Menu._HighlightReason.PROGRAMMATIC)

    def _refresh_items(self) -> None:
        self._has_icons = any(action.icon is not None for action in self._actions)

        self._item_width = 0
        for action in self._actions:
            if action.shortcut is not None:
                self._item_width = max(self._item_width, len(action.text) + len(str(action.shortcut)) + self.LABEL_SHORTCUT_GAP)
            else:
                self._item_width = max(self._item_width, len(action.text))

        width = self._item_width
        if self._has_icons:
            width += 3  # space for icon and a gap

        self.styles.width = width + 6  # padding
        self.styles.height = len(self._actions) + 2

    class _Mode(Enum):
        IMMEDIATE = auto()
        DELAYED = auto()

    def _open_submenu(self, menu: "Menu", y: int, mode: _Mode = _Mode.IMMEDIATE) -> None:
        if mode == Menu._Mode.DELAYED:
            self._start_timer(lambda: self._open_submenu(menu, y, mode=Menu._Mode.IMMEDIATE))
            return

        self._stop_timer()
        if self._opened_submenu == menu:
            return  # already opened

        if self._opened_submenu:
            self._opened_submenu.dismiss()

        menu.show(
            position=Offset(self.region.width, y),
            parent=self,
        )
        self._opened_submenu = menu

    def _dismiss_submenu(self, mode: _Mode = _Mode.IMMEDIATE) -> None:
        if mode == Menu._Mode.DELAYED:
            self._start_timer(lambda: self._dismiss_submenu(mode=Menu._Mode.IMMEDIATE))
            return

        if self._opened_submenu:
            self._opened_submenu.dismiss()
            self._opened_submenu = None

    class _HighlightReason(Enum):
        HOVER = auto()
        KEYBOARD = auto()
        PROGRAMMATIC = auto()

    def _set_highlighted(self, index: int | None, reason: _HighlightReason) -> None:
        if index == self._highlighted:
            return

        self._highlighted = index
        self.refresh()

        self.post_message(self.Highlighted(self, index))

        # close any open submenu if highlight changed
        self._dismiss_submenu(Menu._Mode.DELAYED if reason == Menu._HighlightReason.HOVER else Menu._Mode.IMMEDIATE)

        # open submenu if hovering over a submenu item
        if reason == Menu._HighlightReason.HOVER and self._highlighted is not None:
            action = self._actions[self._highlighted]
            if isinstance(action, Menu) and action.enabled:
                self._open_submenu(
                    action,
                    self._highlighted,
                    mode=Menu._Mode.DELAYED,
                )

    def _next_highlighted(self, old_index: int | None, direction: int) -> int | None:
        if old_index is None:
            if direction > 0:
                new_index = 0
            else:
                new_index = len(self._actions) - 1
        else:
            new_index = (old_index + direction) % len(self._actions)
        checked = 0
        while not self._actions[new_index].enabled or self._actions[new_index].is_separator:
            new_index = (new_index + direction) % len(self._actions)
            checked += 1
            if checked >= len(self._actions):  # all items are disabled or separators
                return None
        return new_index

    def _stop_timer(self) -> None:
        if self._submenu_timer:
            self._submenu_timer.stop()
            self._submenu_timer = None

    def _start_timer(self, callback: TimerCallback) -> None:
        self._stop_timer()
        self._submenu_timer = self.set_timer(self.TIMER_DELAY, callback)

    # endregion


class MenuScreen(ModalScreen[Action]):
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
    ) -> Action | None:
        self._menu.offset = position or self.app.mouse_position
        return await self.app.push_screen_wait(screen=self)

    def compose(self) -> ComposeResult:
        yield self._menu

    def on_menu_dismissed(self, event: Menu.Dismissed) -> None:
        if event.menu != self._menu:
            return
        self.dismiss(None)

    def on_menu_triggered(self, event: Menu.Triggered) -> None:
        self.dismiss(event.action)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        # check if the click is outside the menu and all its open submenus
        if not self._menu.is_in_menu_or_submenus(event.screen_x, event.screen_y):
            self._menu.dismiss()

        await super()._on_mouse_down(event)
