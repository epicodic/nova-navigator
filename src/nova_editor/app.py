"""Standalone Nova Editor application.

Provides a minimal Textual app that uses NovaTextArea to edit text files.
Supports Ctrl+S to save, Ctrl+Q to quit, and shows file path in footer.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.widgets import Header, Static

from nova_editor.widget import NovaTextArea


class EditorFooter(Static):
    """Custom footer showing file information."""

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.file_path = file_path

    def render(self) -> str:
        if self.file_path:
            return f"File: {self.file_path} | Ctrl+S: Save | Ctrl+Q: Quit"
        return "Ctrl+S: Save | Ctrl+Q: Quit"


class NovaEditApp(App[None]):
    """A minimal text editor application using NovaTextArea."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+s", "action_save", "Save"),
        ("ctrl+q", "action_quit", "Quit"),
    ]

    CSS: ClassVar[str] = """
    Screen {
        layout: vertical;
    }

    #header {
        height: 1;
        dock: top;
    }

    #editor {
        width: 1fr;
        height: 1fr;
    }

    #footer {
        height: 1;
        dock: bottom;
    }
    """

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.file_path = file_path
        self.editor: NovaTextArea | None = None

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header(show_clock=False)
        self.editor = NovaTextArea(
            id="editor",
            text=self._load_file() if self.file_path else "",
        )
        yield self.editor
        yield EditorFooter(self.file_path)

    def _load_file(self) -> str:
        """Load file content."""
        if not self.file_path or not self.file_path.exists():
            return ""
        try:
            return self.file_path.read_text()
        except (OSError, ValueError) as e:
            self.notify(f"Error loading file: {e}", severity="error")
            return ""

    def action_save(self) -> None:
        """Save the current document."""
        if not self.editor or not self.file_path:
            self.notify("No file loaded", severity="warning")
            return

        try:
            text = self.editor.text
            self.file_path.write_text(text)
            self.notify(f"Saved to {self.file_path}")
        except (OSError, ValueError) as e:
            self.notify(f"Error saving file: {e}", severity="error")

    async def action_quit(self) -> None:
        """Quit the application."""
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
    parser.add_argument(
        "--help-extended",
        action="store_true",
        help="Show extended help",
    )

    args = parser.parse_args()

    file_path: Path | None = None
    if args.file:
        file_path = Path(args.file)

    app = NovaEditApp(file_path=file_path)
    app.run()


if __name__ == "__main__":
    main()
