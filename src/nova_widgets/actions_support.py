"""ActionsSupport — base class that provides named-action lookup via _act()."""

from __future__ import annotations

from typing import ClassVar

from nova_widgets.action import Action


class ActionsSupport:
    """Mixin that enables named-action lookup from ACTIONS class variables.

    Subclasses declare an ACTIONS class variable (list[Action]).
    ActionsSupport merges all ACTIONS from the MRO into _actions_by_name,
    so child classes automatically inherit parent actions.
    """

    ACTIONS: ClassVar[list[Action]] = []
    _actions_by_name: ClassVar[dict[str, Action]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Build merged actions dict from MRO (parent classes first, child last)
        merged: dict[str, Action] = {}
        for base in reversed(cls.__mro__):
            for action in base.__dict__.get("ACTIONS", []):
                if action.name:
                    merged[action.name] = action
        cls._actions_by_name = merged

    def _act(self, name: str) -> Action:
        """Return the Action registered under the given name.

        Args:
            name: The action name (Action.name attribute).

        Raises:
            KeyError: If no action with that name is registered on this class.
        """
        try:
            return self._actions_by_name[name]
        except KeyError:
            raise KeyError(f"No action named {name!r} on {type(self).__name__}") from None
