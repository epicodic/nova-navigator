"""Nova Navigator context resolver for the keymap system."""

from __future__ import annotations

from typing import Any

from textual.app import App
from textual.widget import Widget

from nova_navigator.dialogs.dialog import Dialog
from nova_navigator.terminal.terminal import Terminal
from nova_navigator.widgets.directory_browser import DirectoryBrowser

_CONTEXT_BROWSER = "browser"
_CONTEXT_BROWSER_SELECTION = "browser.selection"
_CONTEXT_TERMINAL = "terminal"
_CONTEXT_DIALOG = "dialog"


class NovaContextResolver:
    """Resolves the active keymap context for Nova Navigator.

    Priority order: dialog > terminal > browser.selection > browser.
    """

    def __init__(self, app: App[Any]) -> None:
        self._app = app
        self._hover_widget: Widget | None = None

    def resolve(self) -> str:
        """Return the current dispatch context string."""
        # Dialog open? (Dialog is a ModalScreen — when open, app.screen IS the dialog)
        if isinstance(self._app.screen, Dialog):
            return _CONTEXT_DIALOG

        focused = self._app.focused

        # Terminal focused?
        if isinstance(focused, Terminal):
            return _CONTEXT_TERMINAL

        # Directory browser focused?
        if isinstance(focused, DirectoryBrowser):
            if focused.has_selection:
                return _CONTEXT_BROWSER_SELECTION
            return _CONTEXT_BROWSER

        return _CONTEXT_BROWSER

    def hover_context(self) -> str | None:
        """Return context for the widget currently under the mouse, or None."""
        if self._hover_widget is None:
            return None
        if isinstance(self._hover_widget, DirectoryBrowser):
            return _CONTEXT_BROWSER_SELECTION if self._hover_widget.has_selection else _CONTEXT_BROWSER
        return None

    def on_mouse_move(self, widget: Widget) -> None:
        """Called by the app when a MouseMove event targets a widget."""
        self._hover_widget = widget

    def on_mouse_leave(self) -> None:
        """Called when the mouse leaves a tracked widget."""
        self._hover_widget = None
