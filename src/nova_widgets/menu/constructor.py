from nova_widgets.action import Action, ActionGroup

from ._menu import Menu


def action(
    text: str,
    *,
    name: str | None = None,
    action: str | None = None,
    icon: str | None = None,
    enabled: bool = True,
    checkable: bool = False,
    checked: bool = False,
) -> Action:
    """Create an :class:`~nova_widgets.menu.Action` with the given display and behaviour properties."""
    return Action(
        text,
        name=name,
        action=action,
        icon=icon,
        enabled=enabled,
        checkable=checkable,
        checked=checked,
    )


def group(*actions: Action) -> tuple[Action, ...]:
    """Bind *actions* into a mutually-exclusive :class:`~nova_widgets.menu.ActionGroup` and return them.

    Checking any one action in the group automatically unchecks the others.
    """
    group = ActionGroup(*actions)
    for action in actions:
        action.set_group(group)
    return actions


def separator() -> Action:
    """Return a separator :class:`~nova_widgets.menu.Action` for use between menu items."""
    return Action("", is_separator=True)


def menu(title: str | None = None, *actions: Action, name: str | None = None) -> Action:
    """Create a :class:`~nova_widgets.menu.Menu` (sub-menu) with the given *title* and child *actions*."""
    return Menu(title, *actions, name=name)
