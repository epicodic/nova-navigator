"""ActionsSupport — base class that provides id-based action lookup via _act()."""

from __future__ import annotations

from typing import ClassVar

from nova_widgets.action import Action


class ActionsSupport:
    """Mixin that enables action lookup by id from ACTIONS class variables.

    Subclasses declare an ACTIONS class variable (list[Action]).
    ActionsSupport merges all ACTIONS from the MRO into _actions_by_id,
    so child classes automatically inherit parent actions.
    """

    ACTIONS: ClassVar[list[Action]] = []
    _actions_by_id: ClassVar[dict[str, Action]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Build merged actions dict from MRO (parent classes first, child last)
        merged: dict[str, Action] = {}
        for base in reversed(cls.__mro__):
            for action in base.__dict__.get("ACTIONS", []):
                if action.id:
                    merged[action.id] = action
        cls._actions_by_id = merged

    def _act(self, id: str) -> Action:
        """Return the Action registered under the given id.

        Args:
            id: The action id (Action.id attribute).

        Raises:
            KeyError: If no action with that id is registered on this class.
        """
        try:
            return self._actions_by_id[id]
        except KeyError:
            raise KeyError(f"No action id {id!r} on {type(self).__name__}") from None
