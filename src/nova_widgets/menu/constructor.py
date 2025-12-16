from ._action import Action, ActionGroup
from ._menu import Menu


def action(
    text: str,
    *,
    name: str | None = None,
    action: str | None = None,
    shortcut: str | None = None,
    icon: str | None = None,
    enabled: bool = True,
    checkable: bool = False,
    checked: bool = False,
) -> Action:
    return Action(
        text,
        name=name,
        action=action,
        shortcut=shortcut,
        icon=icon,
        enabled=enabled,
        checkable=checkable,
        checked=checked,
    )


def group(*actions: Action) -> tuple[Action, ...]:
    group = ActionGroup(*actions)
    for action in actions:
        action.set_group(group)
    return actions


def separator() -> Action:
    return Action("", is_separator=True)


def menu(title: str | None = None, *actions: Action, name: str | None = None) -> Action:
    return Menu(title, *actions, name=name)
