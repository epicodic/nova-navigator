"""Standalone Nova Editor application.

Provides a minimal Textual app that uses NovaTextArea to edit text files.
Supports Ctrl+S to save, Ctrl+Q to quit, and shows file path in footer.
Supports lazy loading for large files with Ctrl+G goto navigation and F4 wrap toggle.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from rich.console import RenderableType
from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.content import Content
from textual.strip import Strip
from textual.widgets import Header, Input, Static

from nova_editor.core import ByteSource
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from nova_editor.widget._text_area import TEXT_LIMIT


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
    """Input field for goto line/byte navigation. Escape closes it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__(id="goto_bar", placeholder="Line or @byte")

    def action_close(self) -> None:
        """Hide the bar, clear it and give the focus back to the editor."""
        self.display = False
        self.value = ""
        editors = self.app.query(NovaTextArea)
        if editors:
            editors.first().focus()


class TimedNovaTextArea(NovaTextArea):
    """NovaTextArea subclass that tracks first content render for timing hook."""

    def __init__(
        self,
        text: str = "",
        *,
        language: str | None = None,
        theme: str = "css",
        soft_wrap: bool = False,
        tab_behavior: Literal["focus", "indent"] = "focus",
        read_only: bool = False,
        show_cursor: bool = True,
        show_line_numbers: bool = False,
        line_number_start: int = 1,
        max_checkpoints: int | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        compact: bool = False,
        highlight_cursor_line: bool = True,
        placeholder: str | Content = "",
        timing_file: str | None = None,
        _prebuilt_document: LazyDocument | None = None,
    ) -> None:
        super().__init__(
            text=text,
            language=language,
            theme=theme,
            soft_wrap=soft_wrap,
            tab_behavior=tab_behavior,
            read_only=read_only,
            show_cursor=show_cursor,
            show_line_numbers=show_line_numbers,
            line_number_start=line_number_start,
            max_checkpoints=max_checkpoints,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            compact=compact,
            highlight_cursor_line=highlight_cursor_line,
            placeholder=placeholder,
            _prebuilt_document=_prebuilt_document,
        )
        self.timing_file = timing_file
        self._timing_first_content_written = False

    @classmethod
    def open(
        cls,
        source: Path | str | ByteSource,
        *,
        language: str | None = None,
        soft_wrap: bool = False,
        config: LazyConfig | None = None,
        highlight_limit: int = 1_048_576,
        timing_file: str | None = None,
        **kwargs: Any,
    ) -> TimedNovaTextArea:
        """Open a file lazily with timing support.

        Args:
            source: A path or a ByteSource.
            language: Language to highlight.
            soft_wrap: Start with soft wrapping.
            config: Thresholds of the lazy document.
            highlight_limit: Largest source (bytes) that is highlighted.
            timing_file: Optional file path for timing hooks.
            **kwargs: Additional parameters passed to the constructor.

        Returns:
            The widget, ready to be mounted.
        """
        widget = cast(
            "TimedNovaTextArea",
            super().open(
                source,
                language=language,
                soft_wrap=soft_wrap,
                config=config,
                highlight_limit=highlight_limit,
                **kwargs,
            ),
        )
        widget.timing_file = timing_file
        return widget

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
        ("ctrl+s", "save", "Save"),
        ("ctrl+q", "quit", "Quit"),
        ("f4", "toggle_wrap", "Toggle wrap"),
        ("ctrl+g", "show_goto", "Show goto"),
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

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.file_path = file_path
        self.editor: NovaTextArea | None = None
        self.goto_bar: GotoBar | None = None
        self._timing_file: str | None = os.environ.get("NOVA_EDIT_TIMING_FILE")
        self._timing_first_content_written = False

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header(show_clock=False)

        # Open the file using TimedNovaTextArea for timing hook support
        if self.file_path:
            try:
                self.editor = TimedNovaTextArea.open(
                    self.file_path,
                    id="editor",
                    soft_wrap=False,
                    timing_file=self._timing_file,
                )
            except OSError as e:
                self.notify(f"Error loading file: {e}", severity="error")
                self.editor = TimedNovaTextArea(
                    id="editor",
                    text="",
                    soft_wrap=False,
                    timing_file=self._timing_file,
                )
        else:
            self.editor = TimedNovaTextArea(
                id="editor",
                text="",
                soft_wrap=False,
                timing_file=self._timing_file,
            )

        yield self.editor

        # Add goto bar (initially hidden)
        self.goto_bar = GotoBar()
        yield self.goto_bar

        yield EditorFooter(self.file_path)

    def action_save(self) -> None:
        """Save the current document.

        ACT5 replaces this with streaming save.
        """
        if not self.editor or not self.file_path:
            self.notify("No file loaded", severity="warning")
            return

        # `text` is "" above TEXT_LIMIT: never write that over the file (streaming save arrives with ACT5)
        if self.editor.document.length > TEXT_LIMIT:
            self.notify("Saving large files arrives with ACT5", severity="warning")
            return

        # Write interim save to temp file and os.replace
        try:
            text = self.editor.text
            text_bytes = text.encode("utf-8", "surrogateescape")

            # Create temp file in the same directory as the target
            temp_path = self.file_path.with_stem(f"{self.file_path.stem}.tmp")
            try:
                # Get original file permissions if it exists
                original_stat = None
                if self.file_path.exists():
                    with contextlib.suppress(OSError):
                        original_stat = self.file_path.stat()

                # Write to temp file
                with open(temp_path, "wb") as f:
                    f.write(text_bytes)

                # Set permissions if original file existed
                if original_stat is not None:
                    with contextlib.suppress(OSError):
                        os.chmod(temp_path, original_stat.st_mode)

                # Replace original with temp
                os.replace(temp_path, self.file_path)

                self.notify("Interim save (replaced by streaming save in ACT5)", severity="information")
            except OSError:
                # Clean up temp file if it was created
                with contextlib.suppress(OSError):
                    temp_path.unlink()
                raise

        except OSError as e:
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

    args = parser.parse_args()

    file_path: Path | None = None
    if args.file:
        file_path = Path(args.file)

    app = NovaEditApp(file_path=file_path)
    app.run()


if __name__ == "__main__":
    main()
