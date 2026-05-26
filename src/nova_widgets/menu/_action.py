from __future__ import annotations

from collections.abc import Callable
from weakref import ref

from nova_widgets.icon import Icon
from nova_widgets.key_types import KeySequence

IconProvider = Callable[[str], Icon]


ICON_PROVIDER: IconProvider = Icon.of


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
    _icon: Icon | None
    _name: str | None
    _is_separator: bool
    _shortcut: KeySequence | None
    _text: str
    _group: ActionGroup | None = None
    _description: str
    _default_key: KeySequence | None
    _show_in_bar: bool
    _bar_priority: int

    def __init__(
        self,
        text: str | None = None,
        *,
        name: str | None = None,
        icon: str | None = None,
        enabled: bool = True,
        shortcut: str | None = None,
        checkable: bool = False,
        checked: bool = False,
        action: str | None = None,
        is_separator: bool = False,
        description: str = "",
        key: str | None = None,
        show: bool = False,
        bar_priority: int = 100,
    ) -> None:
        self._icon = ICON_PROVIDER(icon) if icon is not None else None
        self._enabled = enabled
        self._text = text or ""
        self._name = name or action
        self._shortcut = KeySequence.parse(shortcut) if shortcut else None
        self._checkable = checkable
        self._checked = checked
        self._action = action
        self._is_separator = is_separator
        self._description = description
        self._default_key = KeySequence.parse(key) if key else None
        self._show_in_bar = show
        self._bar_priority = bar_priority

    @property
    def action(self) -> str | None:
        return self._action

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def icon(self) -> Icon | None:
        return self._icon

    @property
    def text(self) -> str:
        return self._text

    @property
    def shortcut(self) -> KeySequence | None:
        return self._shortcut

    def set_shortcut(self, shortcut: str | KeySequence | None) -> None:
        """Set the displayed shortcut (e.g. after loading user config)."""
        if isinstance(shortcut, str):
            self._shortcut = KeySequence.parse(shortcut) if shortcut else None
        else:
            self._shortcut = shortcut

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
    def description(self) -> str:
        return self._description

    @property
    def default_key(self) -> KeySequence | None:
        return self._default_key

    @property
    def show_in_bar(self) -> bool:
        return self._show_in_bar

    @property
    def bar_priority(self) -> int:
        return self._bar_priority

    @property
    def group(self) -> ActionGroup | None:
        return self._group

    def set_group(self, group: ActionGroup) -> None:
        self._group = group

    @property
    def is_exclusive(self) -> bool:
        return self._group is not None

    # async def execute(self) -> None:
    #     app: App[Any] = getters.app(App[Any])
    #     await app.run_action(self._action)


class ActionCollection:
    _actions: list[Action]

    def __init__(self) -> None:
        self._actions = []

    def _add_action(self, action: Action) -> None:
        self._actions.append(action)

    @property
    def actions(self) -> list[Action]:
        return self._actions

    def find_action(self, path: str | list[str]) -> Action | None:
        parts = path.split(".") if isinstance(path, str) else path
        assert len(parts) > 0

        for action in self._actions:
            if action.name == parts[0]:
                if len(parts) == 1:
                    return action
                if isinstance(action, ActionCollection):
                    return action.find_action(parts[1:])
        return None
