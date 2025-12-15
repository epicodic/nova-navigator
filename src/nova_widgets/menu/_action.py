from __future__ import annotations

from collections.abc import Callable
from weakref import ref

from textual import getters
from textual.app import App

IconProvider = Callable[[str], str]


ICON_PROVIDER: IconProvider = lambda s: s  # noqa: E731


def set_icon_provider(provider: IconProvider) -> None:
    global ICON_PROVIDER  # noqa: PLW0603
    ICON_PROVIDER = provider


class ActionGroup:
    _actions: list[ref[Action]]
    _current: ref[Action] | None = None

    def __init__(self, *actions: Action) -> None:
        self._actions = [ref(action) for action in actions]

    def current(self) -> Action | None:
        return self._current() if self._current else None

    def _on_checked(self, action: Action) -> None:
        prev_cur_action = self.current()
        if prev_cur_action is action:
            return

        self._current = ref(action)
        if prev_cur_action is not None:
            prev_cur_action.set_checked(False)


class Action:
    _action: str | None
    _checkable: bool
    _checked: bool
    _enabled: bool
    _icon: str | None
    _is_separator: bool
    _shortcut: str | None
    _text: str
    _group: ActionGroup | None = None

    def __init__(
        self,
        text: str | None = None,
        icon: str | None = None,
        enabled: bool = True,
        shortcut: str | None = None,
        checkable: bool = False,
        checked: bool = False,
        action: str | None = None,
        is_separator: bool = False,
    ) -> None:
        self._icon = ICON_PROVIDER(icon) if icon is not None else None
        self._enabled = enabled
        self._text = text or ""
        self._shortcut = shortcut
        self._checkable = checkable
        self._checked = checked
        self._action = action
        self._is_separator = is_separator

    @property
    def action(self) -> str | None:
        return self._action

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def icon(self) -> str | None:
        return self._icon

    @property
    def text(self) -> str:
        return self._text

    @property
    def shortcut(self) -> str | None:
        return self._shortcut

    @property
    def checkable(self) -> bool:
        return self._checkable

    @property
    def checked(self) -> bool:
        return self._checkable and self._checked

    def set_checked(self, checked: bool) -> None:
        assert self._checkable, "Action is not checkable"

        if self._checked == checked:
            return

        if self._group is not None:
            if not checked and self._group.current() == self:
                return  # can not uncheck the current item in a group

            self._checked = checked

            if checked:
                self._group._on_checked(self)
        else:
            self._checked = checked

    @property
    def is_separator(self) -> bool:
        return self._is_separator

    @property
    def group(self) -> ActionGroup | None:
        return self._group

    def set_group(self, group: ActionGroup) -> None:
        self._group = group

    @property
    def is_exclusive(self) -> bool:
        return self._group is not None

    async def execute(self) -> None:
        app = getters.app(App)
        await app.run_action(self._action)
