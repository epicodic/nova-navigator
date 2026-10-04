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
from textual.app import App, ComposeResult

from nova_widgets.file_provider import FileProvider
from nova_widgets.keybindings_config import KeybindingsConfig

from .bars import GotoBar
from .document._lazy_config import LazyConfig
from .document_view import not_regular_reason
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
            file_provider: FileProvider for the file dialog; defaults to InMemoryFileProvider.
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

    @property
    def editor(self) -> NovaTextArea:
        """Get the editor widget from the current screen.

        Returns:
            The NovaTextArea editor widget.

        Raises:
            RuntimeError: If the screen is not an EditorScreen.
        """
        screen = self.screen
        if not isinstance(screen, EditorScreen):
            msg = f"Expected EditorScreen, got {type(screen).__name__}"
            raise RuntimeError(msg)
        return screen.document.editor

    @property
    def goto_bar(self) -> GotoBar:
        """Get the goto bar widget from the current screen.

        Returns:
            The GotoBar widget.

        Raises:
            RuntimeError: If the screen is not an EditorScreen.
        """
        screen = self.screen
        if not isinstance(screen, EditorScreen):
            msg = f"Expected EditorScreen, got {type(screen).__name__}"
            raise RuntimeError(msg)
        return screen.query_one(GotoBar)

    def compose(self) -> ComposeResult:
        """This app uses a single screen, so nothing to compose."""
        # The screen is returned by get_default_screen
        return
        yield  # Never reached; makes this a generator

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
