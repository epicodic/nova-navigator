"""Standalone Nova Editor application.

Provides a thin wrapper around EditorScreen for standalone use.
Handles file loading, key forwarding, and app lifecycle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.app import App

from nova_widgets.file_provider import FileProvider
from nova_widgets.keybindings_config import KeybindingsConfig

from .document._lazy_config import LazyConfig
from .document_view import not_regular_reason
from .goto_popup import GotoPopup
from .screen import EditorScreen
from .timed_text_area import TimedNovaTextArea
from .widget import NovaTextArea


class NovaEditApp(App[None]):
    """A thin host app for the Nova Editor using EditorScreen."""

    TITLE: ClassVar[str] = "nova_edit"

    POLL_SECONDS: float = 2.0
    """Interval of the check for a change of the file on disk."""

    QUIT_WAIT_SECONDS: ClassVar[float] = 2.0
    """Longest wait for a cancelled save before quitting."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        file_path: Path | None = None,
        keybindings: KeybindingsConfig | None = None,
        file_provider: FileProvider | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        editor_class: type[TimedNovaTextArea] = TimedNovaTextArea,
    ) -> None:
        """Create the app.

        Args:
            path: The file to open, or `None` for an empty buffer.
            file_path: Alias for `path` (for backward compatibility).
            keybindings: User keybinding overrides; `None` for defaults.
            file_provider: FileProvider for the file dialogs; defaults to the local file system provider.
            soft_wrap: Start with soft wrapping.
            config: Thresholds of the lazy document (`None`: the defaults).
            editor_class: The editor widget class; the benchmark harness passes a probe subclass.
        """
        super().__init__()
        # Support file_path as an alias for path
        self.path = path if path is not None else file_path
        self.keybindings = keybindings
        self.file_provider = file_provider
        self._soft_wrap = soft_wrap
        self._config = config
        self._editor_class = editor_class

    def get_default_screen(self) -> EditorScreen:
        """Return the editor screen as the main screen."""
        return EditorScreen(
            path=self.path,
            keybindings=self.keybindings,
            file_provider=self.file_provider,
            soft_wrap=self._soft_wrap,
            config=self._config,
            editor_class=self._editor_class,
            standalone=True,
            poll_seconds=self.POLL_SECONDS,
            quit_wait_seconds=self.QUIT_WAIT_SECONDS,
        )

    def _editor_screen(self) -> EditorScreen:
        """Return the editor screen, also while a dialog is on top of it.

        Raises:
            RuntimeError: If no EditorScreen is on the screen stack.
        """
        for screen in reversed(self.screen_stack):
            if isinstance(screen, EditorScreen):
                return screen
        msg = f"Expected EditorScreen, got {type(self.screen).__name__}"
        raise RuntimeError(msg)

    @property
    def editor(self) -> NovaTextArea:
        """Get the editor widget of the editor screen, also while a dialog is open.

        Returns:
            The NovaTextArea editor widget.

        Raises:
            RuntimeError: If no EditorScreen is on the screen stack.
        """
        return self._editor_screen().document.editor

    @property
    def goto_popup(self) -> GotoPopup:
        """Get the Go to popup of the current screen.

        Returns:
            The GotoPopup widget.

        Raises:
            RuntimeError: If no EditorScreen is on the screen stack.
        """
        return self._editor_screen().goto_popup

    async def on_event(self, event: events.Event) -> None:
        """Give every raw key press to the screen's keymap first, before Textual's priority bindings."""
        if isinstance(event, events.Key) and not event.is_forwarded:
            screen = self.screen
            if isinstance(screen, EditorScreen) and await screen.press_key(event.key):
                return
        await super().on_event(event)

    def on_app_focus(self, event: events.AppFocus) -> None:
        """The terminal got the focus back: the file may have changed meanwhile."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.poll()

    def search(self, needle: str, *, backward: bool = False, case_sensitive: bool | None = None) -> None:
        """Search the editor for the given text.

        Args:
            needle: The text to find.
            backward: Search towards the start of the document.
            case_sensitive: Distinguish case; the case state of the search bar when `None`.
        """
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.search(needle, backward=backward, case_sensitive=case_sensitive)

    async def action_quit(self) -> None:
        """Quit: run the quit flow of the editor screen (it asks when edits would be lost), else exit.

        The screen is looked up in the screen stack because a dialog may be on top of it; the screen ignores the request while a flow is open.
        """
        for screen in reversed(self.screen_stack):
            if isinstance(screen, EditorScreen):
                await screen.action_quit_editor()
                return
        self.exit()

    def on_editor_screen_closed(self, message: EditorScreen.Closed) -> None:
        """Exit when the editor screen closes."""
        self.exit()


def main() -> None:
    """Entry point for the nova_edit command."""
    parser = argparse.ArgumentParser(
        prog="nova_edit",
        description="Nova Editor - a Textual-based text editor",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="File to edit (optional)",
    )

    args = parser.parse_args()

    file_path: Path | None = None
    if args.file:
        file_path = Path(args.file)
        reason = not_regular_reason(file_path)
        if reason is not None:
            sys.stderr.write(f"nova_edit: {reason}\n")
            sys.exit(1)

    app = NovaEditApp(path=file_path)
    app.run()


if __name__ == "__main__":
    main()
