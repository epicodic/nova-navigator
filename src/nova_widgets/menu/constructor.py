from collections.abc import Callable

from ._menu import AbstractMenuItem, Menu, MenuItem, MenuItemSeparator, MenuItemSubmenu


def item(
    label: str,
    *,
    id: str | None = None,
    action: Callable[[], None] | None = None,
    shortcut: str | None = None,
    icon: str | None = None,
    disabled: bool = False,
) -> MenuItem:
    return MenuItem(label, id=id, action=action, shortcut=shortcut, icon=icon, disabled=disabled)


def separator() -> MenuItemSeparator:
    return MenuItemSeparator()


def submenu(title: str | None = None, items: list[AbstractMenuItem] | None = None) -> MenuItemSubmenu:
    return MenuItemSubmenu(Menu(title, items))
