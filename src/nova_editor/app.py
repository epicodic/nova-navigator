"""Standalone Nova Editor application.

Provides a thin wrapper around EditorScreen for standalone use.
Handles file loading, key forwarding, and app lifecycle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding

from nova_widgets.file_provider import FileProvider
from nova_widgets.keybindings_config import KeybindingsConfig

from .document._lazy_config import LazyConfig
from .document_view import not_regular_reason
from .screen import EditorScreen
from .timed_text_area import TimedNovaTextArea


class NovaEditApp(App[None]):
    """A thin host app for the Nova Editor using EditorScreen."""

    TITLE: ClassVar[str] = "nova_edit"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("f2", "show_path_bar", "SaveAs", show=False),
        Binding("f5", "reload", "Reload", show=False),
        Binding("ctrl+g", "show_goto", "Goto", show=False),
        Binding("f7", "show_search", "Find", show=False),
        Binding("f3", "search_next", "Next", show=False),
        Binding("shift+f3", "search_prev", "Prev", show=False),
        Binding("f4", "toggle_wrap", "Wrap", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("escape", "cancel_save", "Cancel save", show=False),
    ]

    def __init__(
        self,
        path: Path | None = None,
        *,
        keybindings: KeybindingsConfig | None = None,
        file_provider: FileProvider | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        editor_class: type[TimedNovaTextArea] = TimedNovaTextArea,
    ) -> None:
        """Create the app.

        Args:
            path: The file to open, or `None` for an empty buffer.
            keybindings: User keybinding overrides; `None` for defaults.
            file_provider: FileProvider for the file dialog; defaults to InMemoryFileProvider.
            soft_wrap: Start with soft wrapping.
            config: Thresholds of the lazy document (`None`: the defaults).
            editor_class: The editor widget class; the benchmark harness passes a probe subclass.
        """
        super().__init__()
        self.path = path
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
        )

    def compose(self) -> ComposeResult:
        """This app uses a single screen, so nothing to compose."""
        # The screen is returned by get_default_screen
        return
        yield  # Never reached; makes this a generator

    def on_mount(self) -> None:
        """Set up the app after mounting."""
        screen = self.screen
        if isinstance(screen, EditorScreen) and screen.path is not None:
            self.sub_title = str(screen.path)

    def action_save(self) -> None:
        """Save the document; without a file the path bar asks for one."""
        screen = self.screen
        if not isinstance(screen, EditorScreen):
            return
        editor = screen.document.editor
        # Check if the file failed to load (can't save over it)
        if screen.document.load_state == "failed":
            self.notify("Not saved: the file could not be loaded, saving would replace it with an empty document. Use save as.", severity="error")
            return
        if editor.file_path is None:
            # If the editor doesn't have a file path but the app does, save to that path
            if self.path is not None:
                editor.save(self.path)
            else:
                # Otherwise, open the path bar to ask the user
                screen.action_save()
        else:
            # Editor has a file path, use the screen's save logic
            screen.action_save()

    def action_show_path_bar(self) -> None:
        """Forward to the screen's save-as action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_save_as()

    def action_reload(self) -> None:
        """Forward to the screen's reload action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_reload()

    def action_show_goto(self) -> None:
        """Forward to the screen's goto action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_goto()

    def action_show_search(self) -> None:
        """Forward to the screen's find action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_find()

    def action_search_next(self) -> None:
        """Forward to the screen's find-next action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_find_next()

    def action_search_prev(self) -> None:
        """Forward to the screen's find-prev action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_find_previous()

    def action_toggle_wrap(self) -> None:
        """Forward to the screen's toggle-wrap action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_toggle_wrap()

    async def action_quit(self) -> None:
        """Forward to the screen's quit action."""
        screen = self.screen
        if isinstance(screen, EditorScreen):
            screen.action_quit_editor()
        else:
            self.exit()

    def action_cancel_save(self) -> None:
        """Forward to the screen (Esc)."""
        # The screen should handle this via its bindings

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
