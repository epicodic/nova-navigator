"""Standalone Nova Editor application.

Provides a minimal Textual app that uses NovaTextArea to edit text files.
Supports Ctrl+S to save, Ctrl+Q to quit, and shows file path in footer.
Supports lazy loading for large files with Ctrl+G goto navigation and F4 wrap toggle.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.strip import Strip
from textual.widgets import Header, Input, Static

from nova_editor.widget import NovaTextArea

EAGER_LIMIT = 1_048_576
"""File size threshold (bytes) above which to open files lazily."""


@dataclass
class GotoTarget:
    """Parsed goto target: line number or byte offset."""

    kind: Literal["line", "byte"]
    value: int


def parse_goto(text: str) -> GotoTarget | None:
    """Parse goto input: 'N' for line, '@N' for byte offset.

    Args:
        text: Input text (stripped of surrounding whitespace).

    Returns:
        GotoTarget if valid, None otherwise.
    """
    text = text.strip()
    if not text:
        return None

    if text.startswith("@"):
        try:
            offset = int(text[1:])
            if offset < 0:
                return None
            return GotoTarget("byte", offset)
        except ValueError:
            return None

    try:
        line_num = int(text)
        if line_num < 0:
            return None
        return GotoTarget("line", line_num)
    except ValueError:
        return None


class GotoBar(Input):
    """Input field for goto line/byte navigation."""

    def __init__(self) -> None:
        super().__init__(id="goto_bar", placeholder="Line or @byte")


class TimedNovaTextArea(NovaTextArea):
    """NovaTextArea subclass that tracks first content render for timing hook."""

    def __init__(self, timing_file: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.timing_file = timing_file
        self._timing_first_content_written = False

    def render_line(self, y: int) -> Strip:
        """Override render_line to implement timing hook on first content."""
        result = super().render_line(y)

        # Check if this line has non-space content
        # result is a Strip object, check if it has non-empty cells
        if self.timing_file and not self._timing_first_content_written:
            # Convert Strip to string to check for content
            try:
                # Check if Strip has any non-whitespace content
                has_content = False
                for segment in result:
                    if isinstance(segment, Segment) and segment.text.strip():
                        has_content = True
                        break

                if has_content:
                    ns = time.perf_counter_ns()
                    with open(self.timing_file, "w") as f:
                        f.write(f"FIRST_CONTENT {ns}\n")
                    self._timing_first_content_written = True
            except (OSError, ValueError, AttributeError):
                # Silently ignore if we can't write or process
                pass

        return result


class EditorFooter(Static):
    """Custom footer showing file information."""

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.file_path = file_path

    def render(self) -> str:
        base = "Ctrl+S: Save | Ctrl+Q: Quit | F4 Wrap | Ctrl+G Goto"
        if self.file_path:
            return f"File: {self.file_path} | {base}"
        return base


class NovaEditApp(App[None]):
    """A minimal text editor application using NovaTextArea."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+s", "action_save", "Save"),
        ("ctrl+q", "action_quit", "Quit"),
        ("f4", "action_toggle_wrap", "Toggle wrap"),
        ("ctrl+g", "action_show_goto", "Show goto"),
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

    #goto_bar {
        width: 100%;
        height: 1;
        border: solid $primary;
        display: none;
    }

    #footer {
        height: 1;
        dock: bottom;
    }
    """

    def __init__(self, file_path: Path | None = None, lazy: bool = False) -> None:
        super().__init__()
        self.file_path = file_path
        self.lazy = lazy
        self.editor: NovaTextArea | None = None
        self.goto_bar: GotoBar | None = None
        self._timing_file: str | None = os.environ.get("NOVA_EDIT_TIMING_FILE")
        self._timing_first_content_written = False

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header(show_clock=False)

        # Determine if we should open lazily
        should_open_lazy = self.lazy
        if not should_open_lazy and self.file_path:
            try:
                file_size = self.file_path.stat().st_size
                should_open_lazy = file_size > EAGER_LIMIT
            except (OSError, ValueError):
                pass

        # Open the file using TimedNovaTextArea for timing hook support
        if should_open_lazy and self.file_path:
            # For lazy files, we need to create the document and widget separately
            # to pass the timing_file parameter
            self.editor = TimedNovaTextArea.open(
                self.file_path,
                id="editor",
                soft_wrap=False,
                timing_file=self._timing_file,
            )
        else:
            self.editor = TimedNovaTextArea(
                id="editor",
                text=self._load_file() if self.file_path else "",
                soft_wrap=False,
                timing_file=self._timing_file,
            )

        yield self.editor

        # Add goto bar (initially hidden)
        self.goto_bar = GotoBar()
        yield self.goto_bar

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

        # Check if editor is lazy (read-only)
        if self.editor.is_lazy:
            self.notify("Read-only: saving arrives with editing", severity="warning")
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

    def action_toggle_wrap(self) -> None:
        """Toggle soft wrap mode."""
        if self.editor:
            self.editor.toggle_wrap()

    def action_show_goto(self) -> None:
        """Show/hide the GotoBar."""
        if self.goto_bar:
            self.goto_bar.display = not self.goto_bar.display
            if self.goto_bar.display:
                self.goto_bar.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission from GotoBar."""
        if event.input.id != "goto_bar":
            return

        target = parse_goto(event.value)
        if target is None:
            self.notify("Invalid goto format", severity="warning")
            return

        if self.editor:
            if target.kind == "line":
                self.editor.goto_line(target.value)
            else:  # byte
                self.editor.goto_byte(target.value)

        # Hide the goto bar and refocus editor
        if self.goto_bar:
            self.goto_bar.display = False
            self.goto_bar.value = ""
        if self.editor:
            self.editor.focus()


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
        "--lazy",
        action="store_true",
        help="Force lazy loading mode",
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

    app = NovaEditApp(file_path=file_path, lazy=args.lazy)
    app.run()


if __name__ == "__main__":
    main()
