"""Drive and read the View menu of an editor screen through the mouse and the keys, as a user does."""

from __future__ import annotations

from textual.pilot import Pilot

from nova_editor.screen import EditorScreen
from nova_widgets.menu import SYMBOL_TABLE, Menu
from tests.nova_editor.helpers_view import wait_until

FILE_MENU_INDEX = 0
SEARCH_MENU_INDEX = 2
VIEW_MENU_INDEX = 3
NARROW = (80, 24)
"""Terminal size of the menu tests: 80 columns, with a long path in the menu bar (a pytest tmp_path is long)."""
CHECKED = SYMBOL_TABLE["checkbox"][1].glyph


async def open_view_menu(pilot: Pilot[None], screen: EditorScreen, menu_index: int = VIEW_MENU_INDEX) -> Menu:
    """Click the menu entry at `menu_index` and return its menu (the items are drawn from the very `Action` objects)."""
    menu = screen.menu_bar.actions[menu_index]
    items = list(screen.query("MenuBarItem"))
    item = items[menu_index]
    await pilot.hover(item)
    await pilot.click(item)
    await wait_until(pilot, lambda: menu in list(screen.query(Menu)))
    return menu


def marks(menu: Menu) -> dict[str, bool]:
    """Return `{label: checked}` as the menu draws it: whether the row of each item holds the check glyph."""
    return {action.text: CHECKED in menu.render_line(index + 1).text for index, action in enumerate(menu.actions)}


async def view_marks(pilot: Pilot[None], screen: EditorScreen) -> dict[str, bool]:
    """Open the View menu, read the drawn marks and close the menu again."""
    menu = await open_view_menu(pilot, screen)
    drawn = marks(menu)
    await pilot.press("escape")
    await wait_until(pilot, lambda: menu not in list(screen.query(Menu)))
    return drawn


async def choose_item(pilot: Pilot[None], screen: EditorScreen, menu_index: int, label: str) -> None:
    """Open the menu at `menu_index`, move to the item `label` with the arrow keys and press Enter."""
    menu = await open_view_menu(pilot, screen, menu_index)
    index = [action.text for action in menu.actions].index(label)
    await pilot.press(*["down"] * (index + 1), "enter")
    await wait_until(pilot, lambda: menu not in list(screen.query(Menu)))


async def choose_view_item(pilot: Pilot[None], screen: EditorScreen, label: str) -> None:
    """Open the View menu, move to the item `label` with the arrow keys and press Enter."""
    await choose_item(pilot, screen, VIEW_MENU_INDEX, label)
