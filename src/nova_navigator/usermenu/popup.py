"""UserMenuPopup — modal menu listing the visible user menu entries."""

from __future__ import annotations

from textual import events

from nova_widgets.menu import Action, Menu

from .evaluate import MenuView

_KEY_GAP = "  "


class UserMenuPopup(Menu):
    """Menu with an MC-style hotkey column; pressing an entry's key selects it immediately.

    Show it with ``await popup.exec(position)``; the result's ``id`` is the entry id.
    """

    def __init__(self, view: MenuView) -> None:
        actions = [Action(is_separator=True) if row is None else Action(f"{row.key or ' '}{_KEY_GAP}{row.label}", id=row.id) for row in view.rows]
        super().__init__("User Menu", *actions)
        self._view = view

    def on_mount(self) -> None:
        self._set_highlighted(self._view.default_index, self._HighlightReason.PROGRAMMATIC)

    def on_key(self, event: events.Key) -> None:
        if event.character is None:
            return
        for index, row in enumerate(self._view.rows):
            if row is not None and row.key == event.character:
                event.stop()
                self._triggered(self._actions[index])
                return
