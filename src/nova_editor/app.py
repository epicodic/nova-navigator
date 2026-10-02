"""Standalone Nova Editor application.

Provides a minimal Textual app that uses NovaTextArea to edit text files.
Supports Ctrl+S to save (streaming, atomic), F2 to save as, F5 to reload, Ctrl+Q to quit (it asks before discarding changes), and shows the file path in the header; the footer lists the keys.
Supports lazy loading for large files with Ctrl+G goto navigation and F4 wrap toggle.
A status bar shows the progress and the result of a save; a key driven bar asks before overwriting or discarding.
A background poll notices when the file changed on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from rich.console import RenderableType
from rich.segment import Segment
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.message import Message
from textual.strip import Strip
from textual.widgets import Footer, Header, Input, Static

from nova_editor.core import ByteSource
from nova_editor.core.byte_source import ChangeKind
from nova_editor.core.save import check_path
from nova_editor.document._cursor_anchor import CursorState
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.search_bar import SearchBar, SearchStatus
from nova_editor.status_line import StatusLine, StatusState
from nova_editor.widget import ExternalCheck, NovaTextArea


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


class PathBar(Input):
    """Input field for the save-as path (F2). Enter saves, Escape closes it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__(id="path_bar", placeholder="Save as: path")

    def open(self, path: Path | None) -> None:
        """Show the bar prefilled with `path` and focus it."""
        self.value = "" if path is None else str(path)
        self.display = True
        self.focus()
        self.cursor_position = len(self.value)

    def action_close(self) -> None:
        """Hide the bar, clear it and give the focus back to the editor."""
        self.display = False
        self.value = ""
        editors = self.app.query(NovaTextArea)
        if editors:
            editors.first().focus()


QUIT_POLL_SECONDS = 0.02
NEEDLE_SHOWN = 40
"""Most characters of a needle that the status line shows."""
_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")
_KIB = 1024


def format_sizes(done: int, total: int) -> str:
    """Format `done / total` in the binary unit that suits `total` (`1.2 / 5.0 GiB`)."""
    unit = 0
    while unit < len(_UNITS) - 1 and total >= _KIB ** (unit + 1):
        unit += 1
    scale = _KIB**unit
    if unit == 0:
        return f"{done} / {total} B"
    return f"{done / scale:.1f} / {total / scale:.1f} {_UNITS[unit]}"


class SaveBar(Static):
    """Status line of a save: progress while it runs, then a result line for 4 s; a failure stays until a key is pressed. Hidden when idle."""

    RESULT_SECONDS: ClassVar[float] = 4.0

    def __init__(self) -> None:
        super().__init__("", id="save_bar", markup=False)
        self.line = ""
        """The text shown (empty when idle)."""
        self.clock: Callable[[], float] = time.monotonic
        """Time source of the result timeout (tests replace it)."""
        self.failed = False
        """Whether the line is a failure, which stays until a key is pressed."""
        self._expires: float | None = None

    def on_mount(self) -> None:
        self.set_interval(0.1, self.tick)

    def _show(self, line: str) -> None:
        self.line = line
        self.update(line)
        self.display = True

    def show_progress(self, phase: str, done: int, total: int) -> None:
        """Show the state of a running save."""
        self.failed = False
        self._expires = None
        if phase == "flushing":
            self._show("Flushing")
        elif phase == "history":
            self._show("Preserving undo history")
        elif phase == "finishing":
            self._show("Finishing")
        else:
            percent = 100 if total <= 0 else round(done * 100 / total)
            self._show(f"Saving  {format_sizes(done, total)}  {percent} %  Esc cancels")

    def show_result(self, line: str) -> None:
        """Show a result line that disappears after `RESULT_SECONDS`."""
        self.failed = False
        self._expires = self.clock() + self.RESULT_SECONDS
        self._show(line)

    def show_failure(self, line: str) -> None:
        """Show a failure; it stays until `clear`."""
        self.failed = True
        self._expires = None
        self._show(line)

    def clear(self) -> None:
        """Hide the bar."""
        self.failed = False
        self._expires = None
        self.line = ""
        self.update("")
        self.display = False

    def tick(self) -> None:
        """Hide a result line whose time is over."""
        if self._expires is not None and self.clock() >= self._expires:
            self.clear()


_CHANGE_TEXT: dict[ChangeKind, str] = {
    ChangeKind.MODIFIED: "The file changed on disk",
    ChangeKind.TRUNCATED: "The file was truncated on disk",
    ChangeKind.REPLACED: "The file was replaced on disk",
    ChangeKind.DELETED: "The file was deleted on disk",
    ChangeKind.CREATED: "The file was created since the editor started",
    ChangeKind.EXISTS: "The file already exists",
    ChangeKind.UNREADABLE: "The file cannot be read",
}


class ConfirmBar(Static):
    """Key driven question for overwrite, reload and external change: O overwrite, A save as, R reload, Esc keep."""

    can_focus = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("o,O", "choose('overwrite')", "Overwrite", show=False),
        Binding("a,A", "choose('save_as')", "Save as", show=False),
        Binding("r,R", "choose('reload')", "Reload", show=False),
        Binding("q,Q", "choose('quit')", "Quit", show=False),
        Binding("escape", "choose('keep')", "Keep", show=False),
    ]

    @dataclass
    class Chosen(Message):
        """The user answered the question."""

        choice: str
        """`overwrite`, `save_as`, `reload`, `quit` or `keep`."""
        path: Path | None
        """The file the question was about."""

    def __init__(self) -> None:
        super().__init__("", id="confirm_bar", markup=False)
        self.kind: ChangeKind | None = None
        """What the question is about (`None` for a plain reload)."""
        self.path: Path | None = None
        """The file the question is about."""
        self._allowed: set[str] = set()

    def ask(self, kind: ChangeKind | None, path: Path | None, *, overwrite: bool, save_as: bool, reload: bool) -> None:
        """Show the question and take the focus.

        Args:
            kind: What differs; `None` asks whether to discard the edits and reload.
            path: The file the question is about.
            overwrite: Offer `O`.
            save_as: Offer `A`.
            reload: Offer `R`.
        """
        self.kind = kind
        self.path = path
        self._allowed = {"keep"}
        offers = []
        if overwrite:
            self._allowed.add("overwrite")
            offers.append("O overwrite")
        if save_as:
            self._allowed.add("save_as")
            offers.append("A save as")
        if reload:
            self._allowed.add("reload")
            offers.append("R reload")
        offers.append("Esc keep")
        head = "Discard the edits and reload?" if kind is None else _CHANGE_TEXT.get(kind, "The file changed")
        self.update(f"{head}  {'  '.join(offers)}")
        self.display = True
        self.focus()

    def ask_quit(self, *, save_running: bool) -> None:
        """Ask whether to quit: while a save still runs, or with unsaved changes (replaces any open question)."""
        self.kind = None
        self.path = None
        self._allowed = {"quit", "keep"}
        head = "A save is still running." if save_running else "Discard the unsaved changes and quit?"
        tail = "Q quit anyway  Esc stay" if save_running else "Q quit  Esc stay"
        self.update(f"{head}  {tail}")
        self.display = True
        self.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer only the keys that the question lists."""
        if action == "choose":
            return str(parameters[0]) in self._allowed
        return super().check_action(action, parameters)

    def action_choose(self, choice: str) -> None:
        """Hide the bar and report the answer."""
        self.display = False
        self.post_message(self.Chosen(choice, self.path))


def not_regular_reason(path: Path) -> str | None:
    """Return why `path` cannot be opened (it exists but is a FIFO, a device, a directory, ...), or `None`."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if stat.S_ISREG(mode):
        return None
    return f"{path}: not a regular file"


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


def _column_kind(state: CursorState) -> Literal["exact", "provisional", "pending"]:
    if state is CursorState.RESOLVED:
        return "exact"
    if state is CursorState.PROVISIONAL:
        return "provisional"
    return "pending"


class NovaEditApp(App[None]):
    """A minimal text editor application using NovaTextArea."""

    TITLE: ClassVar[str] = "nova_edit"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "save", "Save"),
        Binding("f2", "show_path_bar", "SaveAs"),
        Binding("f5", "reload", "Reload"),
        Binding("ctrl+g", "show_goto", "Goto"),
        Binding("f7", "show_search", "Find"),
        Binding("f3", "search_next", "Next"),
        Binding("shift+f3", "search_prev", "Prev", key_display="S-F3"),
        Binding("f4", "toggle_wrap", "Wrap"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "cancel_save", "Cancel save", show=False),
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

    #status_line {
        width: 100%;
        height: 1;
        background: $panel;
        color: $text;
    }

    #goto_bar {
        width: 100%;
        height: 1;
        border: solid $primary;
        display: none;
    }

    #path_bar {
        width: 100%;
        height: 1;
        border: none;
        display: none;
    }

    #search_bar {
        width: 100%;
        height: 1;
        border: none;
        display: none;
    }

    #save_bar, #confirm_bar, #search_status {
        width: 100%;
        height: 1;
        display: none;
    }

    #confirm_bar {
        background: $warning;
        color: $text;
    }
    """

    POLL_SECONDS: float = 2.0
    """Interval of the check for a change of the file on disk."""

    QUIT_WAIT_SECONDS: ClassVar[float] = 2.0
    """Longest wait for a cancelled save before quitting."""

    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.file_path = file_path
        self.editor: NovaTextArea | None = None
        # "loaded": the editor shows the file; "new": the file did not exist at start; "failed": it exists but could not be read
        self._load_state: Literal["loaded", "new", "failed"] = "loaded"
        self.goto_bar: GotoBar | None = None
        self._timing_file: str | None = os.environ.get("NOVA_EDIT_TIMING_FILE")
        self._timing_first_content_written = False
        self._polling = False
        self._needle: str | None = None
        """The last needle searched, repeated by F3 and Shift+F3."""
        self._last_backward = False
        """Direction of the search in progress or last started (it decides the wrap text)."""
        self._deferred_change: ChangeKind | None = None
        """A change that `SourceChanged` reported while a save ran: announced after the save when it did not rebase the document."""

    def _empty_editor(self) -> TimedNovaTextArea:
        return TimedNovaTextArea(id="editor", text="", soft_wrap=False, timing_file=self._timing_file)

    def _open_editor(self, path: Path) -> TimedNovaTextArea:
        """Open `path` in an editor; when it cannot be opened, notify, set the load state and return an empty editor."""
        reason = not_regular_reason(path)  # opening a FIFO would block
        if reason is not None:
            self._load_state = "failed"
            self.notify(f"Error loading file: {reason}", severity="error")
            return self._empty_editor()
        try:
            return TimedNovaTextArea.open(path, id="editor", soft_wrap=False, timing_file=self._timing_file)
        except OSError as e:
            self._load_state = "new" if isinstance(e, FileNotFoundError) else "failed"
            self.notify(f"Error loading file: {e}", severity="error")
            return self._empty_editor()

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header(show_clock=False)

        self.editor = self._open_editor(self.file_path) if self.file_path else self._empty_editor()
        yield self.editor

        # Add goto bar (initially hidden)
        self.goto_bar = GotoBar()
        yield self.goto_bar
        yield PathBar()
        yield SaveBar()
        yield ConfirmBar()
        yield SearchBar()
        yield SearchStatus()
        yield StatusLine(self._status_state)
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        """Show the path in the header and start the poll for external changes."""
        if self.file_path is not None:
            self.sub_title = str(self.file_path)
        self.set_interval(self.POLL_SECONDS, self._poll)
        if self.editor is not None:
            self.watch(self.editor, "pending_progress", self._on_editor_state, init=False)
            self.watch(self.editor, "soft_wrap", self._on_editor_state, init=False)

    def _on_editor_state(self, _value: object) -> None:
        self._request_status()

    def _request_status(self) -> None:
        for status in self.query(StatusLine):
            status.request()

    def _status_state(self) -> StatusState | None:
        editor = self.editor
        if editor is None:
            return None
        row, column = editor.cursor_location
        cursor_state, _ = editor.peek_cursor_state()
        exact = editor.line_count_exact
        progress = editor.pending_progress
        return StatusState(
            line=row + 1,
            column=column + 1,
            column_kind=_column_kind(cursor_state),
            byte_offset=editor.cursor_byte_offset,
            line_count=editor.line_count,
            line_count_exact=exact,
            indexing_percent=100 if exact else min(99, int(editor.indexing_progress * 100)),
            line_ending=editor.line_ending,
            modified=editor.modified,
            new_file=self._load_state == "new",
            wrap=editor.soft_wrap,
            goto_percent=None if progress is None else int(progress * 100),
        )

    def on_nova_text_area_selection_changed(self, message: NovaTextArea.SelectionChanged) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_changed(self, message: NovaTextArea.Changed) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_index_progress(self, message: NovaTextArea.IndexProgress) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_indexing_complete(self, message: NovaTextArea.IndexingComplete) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_jump_completed(self, message: NovaTextArea.JumpCompleted) -> None:
        """Refresh the status line."""
        self._request_status()

    def on_nova_text_area_jump_rejected(self, message: NovaTextArea.JumpRejected) -> None:
        """Show why a goto was rejected; the cursor did not move."""
        self.notify(message.reason, severity="warning")

    @property
    def _save_bar(self) -> SaveBar:
        return self.query_one(SaveBar)

    @property
    def _confirm_bar(self) -> ConfirmBar:
        return self.query_one(ConfirmBar)

    @property
    def _path_bar(self) -> PathBar:
        return self.query_one(PathBar)

    @property
    def _search_bar(self) -> SearchBar:
        return self.query_one(SearchBar)

    @property
    def _search_status(self) -> SearchStatus:
        return self.query_one(SearchStatus)

    def _refocus_editor(self) -> None:
        if self.editor:
            self.editor.focus()

    # Polling

    def _poll(self) -> None:
        """Timer or app focus: start one check of the file on a thread, unless one still runs or a save is under way."""
        editor = self.editor
        if editor is None or self._polling:
            return
        check = editor.begin_external_check()
        if check is None:
            return
        self._polling = True
        self.run_worker(partial(self._poll_check, check), thread=True, group="poll", exit_on_error=False)

    def _poll_check(self, check: ExternalCheck) -> None:
        """Worker thread: only the `stat` calls of the check (they may block on a network file system); the UI thread applies the result."""
        kind = ChangeKind.UNCHANGED
        try:
            kind = check.run()
        finally:
            try:
                posted = self.post_message(events.Callback(partial(self._poll_done, check, kind)))
            except RuntimeError:  # the app is closing
                posted = False
            if not posted:
                self._polling = False

    def _poll_done(self, check: ExternalCheck, kind: ChangeKind) -> None:
        """UI thread: the poll ended; apply its result (the widget ignores one that a save made out of date)."""
        self._polling = False
        editor = self.editor
        if editor is not None:
            editor.apply_external_check(check, kind)

    def on_app_focus(self, event: events.AppFocus) -> None:
        """The terminal got the focus back: the file may have changed meanwhile, so run the same check as the poll."""
        self._poll()

    # Keys

    def action_save(self) -> None:
        """Save the document (Ctrl+S); without a file the path bar asks for one."""
        editor = self.editor
        if editor is None or editor.saving:
            return
        if self._load_state == "failed":
            self.notify("Not saved: the file could not be loaded, saving would replace it with an empty document. Use save as.", severity="error")
            self._path_bar.open(self.file_path)
            return
        if editor.file_path is None:
            if self.file_path is None:
                self._path_bar.open(None)
            else:
                self._save_to(self.file_path)
            return
        if not editor.modified:
            self._save_bar.show_result("No changes to save")
            return
        editor.save()

    def action_show_path_bar(self) -> None:
        """Open the save-as bar (F2), prefilled with the current path."""
        editor = self.editor
        current = editor.file_path if editor is not None and editor.file_path is not None else self.file_path
        self._path_bar.open(current)

    def action_reload(self) -> None:
        """Reload the file (F5); a modified document asks first."""
        editor = self.editor
        if editor is None:
            return
        if editor.saving:
            self.notify("A save is running", severity="warning")
            return
        if editor.file_path is None:
            self.notify("Nothing to reload", severity="warning")
            return
        if editor.modified:
            self._confirm_bar.ask(None, editor.file_path, overwrite=False, save_as=False, reload=True)
            return
        editor.reload()

    def action_cancel_save(self) -> None:
        """Cancel a running save (Esc)."""
        if self.editor:
            self.editor.cancel_save()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keep Esc for the widget and the bars unless a save runs."""
        if action == "cancel_save":
            return self.editor is not None and self.editor.saving
        return super().check_action(action, parameters)

    async def on_event(self, event: events.Event) -> None:
        """A key press dismisses a failure shown by the save bar."""
        if isinstance(event, events.Key):
            bars = self.query(SaveBar)
            if bars and bars.first().failed:
                bars.first().clear()
        await super().on_event(event)

    async def action_quit(self) -> None:
        """Quit (Ctrl+Q): cancel a search and a save, then ask when edits would be lost or a save still runs."""
        editor = self.editor
        if editor is None:
            self.exit()
            return
        if editor.searching:
            editor.cancel_search()
        editor.cancel_pending()
        if editor.saving:
            editor.cancel_save()
            for _ in range(round(self.QUIT_WAIT_SECONDS / QUIT_POLL_SECONDS)):
                if not editor.saving:
                    break
                await asyncio.sleep(QUIT_POLL_SECONDS)
            if editor.saving:
                self._confirm_bar.ask_quit(save_running=True)
                return
        if editor.modified:
            self._confirm_bar.ask_quit(save_running=False)
            return
        self.exit()

    # Saving

    def _save_to(self, path: Path, *, confirmed: bool = False) -> None:
        """Save to `path` (a save as, or the first save of a new file)."""
        editor = self.editor
        if editor is None:
            return
        path = path.expanduser().absolute()
        first_save = self._load_state == "new" and self.file_path is not None and os.path.realpath(path) == os.path.realpath(self.file_path)
        if first_save and not confirmed and check_path(path, None) is ChangeKind.CREATED:
            self._confirm_bar.ask(ChangeKind.CREATED, path, overwrite=True, save_as=True, reload=False)
            return
        editor.save(path, overwrite=confirmed or first_save)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission from the goto bar and the path bar."""
        if event.input.id == "path_bar":
            text = event.value.strip()
            if not text:
                self.notify("No path given", severity="warning")
                return
            self._path_bar.action_close()
            self._save_to(Path(text))
            return
        if event.input.id == "search_bar":
            needle = event.value
            if not needle:
                return
            self._search_bar.action_close()
            self.search(needle, case_sensitive=self._search_bar.case_sensitive)
            return
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

    def on_confirm_bar_chosen(self, message: ConfirmBar.Chosen) -> None:
        """Carry out the answer of the confirm bar."""
        if message.choice == "quit":
            self.exit()
            return
        self._refocus_editor()
        editor = self.editor
        if editor is None:
            return
        if message.choice == "overwrite" and message.path is not None:
            if editor.file_path is None:
                self._save_to(message.path, confirmed=True)
            else:
                editor.save(message.path, overwrite=True)
        elif message.choice == "save_as":
            self._path_bar.open(message.path)
        elif message.choice == "reload" and not editor.saving:
            editor.reload()

    def on_nova_text_area_save_progress(self, message: NovaTextArea.SaveProgress) -> None:
        """Show the progress of the save."""
        self._save_bar.show_progress(message.phase, message.done, message.total)

    def on_nova_text_area_saved(self, message: NovaTextArea.Saved) -> None:
        """Show the result and follow the file the document is bound to now."""
        self._load_state = "loaded"
        self.file_path = message.path
        self.sub_title = str(message.path)
        self._request_status()
        self._save_bar.show_result(f"Saved  {message.path.name}  {format_sizes(message.length, message.length)}")
        self._deferred_change = None

    def on_nova_text_area_save_failed(self, message: NovaTextArea.SaveFailed) -> None:
        """Show the failure with its stage; it stays until a key is pressed."""
        reason = message.error.strerror or str(message.error)
        written = "  The file was written; press F5 to reload." if message.committed else ""
        self._save_bar.show_failure(f"Save failed ({message.stage}): {reason}{written}  Press a key")
        self._recheck_deferred_change(committed=message.committed)

    def on_nova_text_area_save_cancelled(self, message: NovaTextArea.SaveCancelled) -> None:
        """Show that the save was cancelled."""
        self._save_bar.show_result("Save cancelled")
        self._recheck_deferred_change()

    def on_nova_text_area_save_needs_confirmation(self, message: NovaTextArea.SaveNeedsConfirmation) -> None:
        """Ask before overwriting."""
        exists = message.kind in {ChangeKind.EXISTS, ChangeKind.CREATED}
        reload = not exists and self.editor is not None and self.editor.file_path is not None
        self._confirm_bar.ask(message.kind, message.path, overwrite=True, save_as=True, reload=reload)

    def on_nova_text_area_source_changed(self, message: NovaTextArea.SourceChanged) -> None:
        """Ask what to do about a file that changed on disk."""
        editor = self.editor
        if editor is None:
            return
        if editor.saving:  # asking now would compete with the save: remember it, the end of the save decides (a commit lifts the state)
            self._deferred_change = message.kind
            return
        self._announce_change(message.kind)

    def _announce_change(self, kind: ChangeKind) -> None:
        """Ask what to do about a file that changed on disk, unless the view is not stale any more.

        A `SourceChanged` posted during a save can be handled after the save ended; when the save rebased the document onto its own file the view
        is no longer stale and the check finds nothing, so there is nothing to ask.
        """
        editor = self.editor
        if editor is None:
            return
        current = editor.check_external_change()  # the stale kind while the view is stale, else a fresh check
        if current is ChangeKind.UNCHANGED:
            return
        self._confirm_bar.ask(kind, editor.file_path, overwrite=editor.modified, save_as=True, reload=editor.file_path is not None)

    def _recheck_deferred_change(self, *, committed: bool = False) -> None:
        """After a save: announce the change that was seen while it ran unless the save rebased the document (the file is then the app's own)."""
        deferred, self._deferred_change = self._deferred_change, None
        editor = self.editor
        if deferred is None or committed or editor is None or editor.saving:
            return
        kind = editor.check_external_change()  # still stale, or changed again: either way the current kind
        if kind is not ChangeKind.UNCHANGED:
            self._announce_change(kind)

    def on_nova_text_area_reloaded(self, message: NovaTextArea.Reloaded) -> None:
        """Show that the file was reloaded."""
        self._request_status()
        self._save_bar.show_result("Reloaded")

    def on_nova_text_area_reload_failed(self, message: NovaTextArea.ReloadFailed) -> None:
        """Show why the file could not be reloaded."""
        self._save_bar.show_failure(f"Reload failed: {message.error.strerror or message.error}  Press a key")

    # Searching

    def search(self, needle: str, *, backward: bool = False, case_sensitive: bool | None = None) -> None:
        """Search the editor for `needle` and remember it for F3 and Shift+F3.

        Args:
            needle: The text to find (it may hold line breaks, which a bar cannot take).
            backward: Search towards the start of the document.
            case_sensitive: Distinguish case; the case state of the search bar when `None`.
        """
        editor = self.editor
        if editor is None or not needle:
            return
        if case_sensitive is None:
            case_sensitive = self._search_bar.case_sensitive
        self._needle = needle
        self._last_backward = backward
        editor.search(needle, backward=backward, case_sensitive=case_sensitive)

    def _repeat_search(self, *, backward: bool) -> None:
        if self._needle is None:
            self.action_show_search()
            return
        self.search(self._needle, backward=backward)

    def action_show_search(self) -> None:
        """Open the search bar (F7)."""
        self._search_bar.open()

    def action_search_next(self) -> None:
        """Repeat the last search forward (F3)."""
        self._repeat_search(backward=False)

    def action_search_prev(self) -> None:
        """Repeat the last search backward (Shift+F3)."""
        self._repeat_search(backward=True)

    def on_nova_text_area_search_progress(self, message: NovaTextArea.SearchProgress) -> None:
        """Show the progress of the search."""
        editor = self.editor
        if editor is None or not editor.searching:
            return
        percent = 100 if message.total <= 0 else round(message.done * 100 / message.total)
        sizes = format_sizes(message.done, message.total).replace(" / ", " of ")
        self._search_status.show_progress(f"Searching {percent}% ({sizes}), Esc cancels")

    def on_nova_text_area_search_found(self, message: NovaTextArea.SearchFound) -> None:
        """Show the result of a search that found a match."""
        if not message.wrapped:
            text = "Found"
        else:
            text = "Wrapped to the bottom" if self._last_backward else "Wrapped to the top"
        self._search_status.show_result(text)

    def on_nova_text_area_search_not_found(self, message: NovaTextArea.SearchNotFound) -> None:
        """Show that nothing was found."""
        needle = message.needle
        if len(needle) > NEEDLE_SHOWN:
            needle = needle[:NEEDLE_SHOWN] + chr(0x2026)
        self._search_status.show_result(f"Not found: {needle}")

    def on_nova_text_area_search_cancelled(self, message: NovaTextArea.SearchCancelled) -> None:
        """Show why a search ended without a result; a replaced one is followed by its successor."""
        if message.reason == "replaced":
            return
        suffix = "" if message.reason == "cancelled" else f": {message.reason}"
        self._search_status.show_result(f"Search cancelled{suffix}")

    def on_nova_text_area_search_failed(self, message: NovaTextArea.SearchFailed) -> None:
        """Show why a search failed."""
        self._search_status.show_result(f"Search failed: {message.error}")

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

    app = NovaEditApp(file_path=file_path)
    app.run()


if __name__ == "__main__":
    main()
