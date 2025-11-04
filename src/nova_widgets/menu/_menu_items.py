from __future__ import annotations

from collections.abc import Callable

from . import _menu


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
    _menu: _menu.Menu

    def __init__(self, menu: _menu.Menu, *, icon: str | None = None, disabled: bool = False) -> None:
        super().__init__(icon=icon, disabled=disabled)
        self._menu = menu

    @property
    def label(self) -> str:
        assert self._menu.title
        return self._menu.title

    @property
    def menu(self) -> _menu.Menu:
        return self._menu


class MenuItemSeparator(AbstractMenuItem):
    pass
