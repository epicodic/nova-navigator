"""PTY-backed terminal emulator widget for Textual.

This module contains the ``Terminal`` widget, which embeds a shell inside a
Textual application.  It delegates OS-level PTY management to a ``PtyBackend``
and shell-specific hook/quoting logic to a ``ShellDriver``.

The widget owns:
- The pyte virtual screen and ANSI parser.
- The Rich text rendering pipeline (``TerminalDisplay``).
- The draining state machine for silent directory navigation.
- Keyboard and mouse event handling.
- The recv_queue processing loop.

It does NOT own:
- Process lifecycle (start/stop/signal) — that's ``PtyBackend``.
- Shell init code, quoting, precmd parsing — that's ``ShellDriver``.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py

Related modules:
- ``pty_backend.py`` — ``PtyBackend`` ABC and ``LocalPtyBackend``.
- ``shell_driver.py`` — ``ShellDriver`` ABC and concrete drivers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from asyncio import Future, Task, TimerHandle
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import PurePath
from typing import Any, Literal, cast

import pyte
from pyte.screens import Char, Margins
from rich.cells import cell_len
from rich.color import ColorParseError
from rich.console import Console, ConsoleOptions, ConsoleRenderable, RenderableType
from rich.console import RenderResult as RichRenderResult
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import log
from textual.geometry import Offset, Size
from textual.message import Message
from textual.screen import Screen
from textual.scroll_view import ScrollView
from textual.selection import Selection
from textual.strip import Strip

from nova_navigator.commands.errors import TerminalBusyError
from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import RESTORE_SEQUENCE, STASH_SEQUENCE, ShellDriver, detect_driver

_logger = logging.getLogger(__name__)

__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
]


_PRE_CMD_FROM_NN_IDX = 2  # index of the from_nn flag in a pre_cmd message
_PRE_CMD_LINE_EDITING_IDX = 3  # index of the optional line_editing flag in a pre_cmd message


_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})
_BRACKETED_PASTE_MODE = "2004"
_RECV_DRAIN_LIMIT: int = 100
_DISPLAY_FPS: float = 60.0
_ED_ERASE_SCROLLBACK = 3  # ED (erase in display) parameter for "erase saved lines"
_NAV_WATCHDOG_TIMEOUT = 2.0
_NAV_MAX_SAME_TARGET_ATTEMPTS = 3
_STARTUP_TIMEOUT = 5.0  # seconds a new shell may take to report its hook before its output is shown
_EARLY_EXIT_SECONDS = 2.0  # a shell that exits sooner than this, without a hook report, is a quick exit
_MAX_EARLY_EXITS = 3  # quick exits in a row after which the shell is not restarted
_STARTUP_BUFFER_CHARS = 65536

_re_ansi_sequence = re.compile(r"(\x1b\[\??[\d;]*[a-zA-Z])")
_DECSET_PREFIX = "\x1b[?"


class TerminalPyteScreen(pyte.Screen):
    """pyte.Screen subclass that drops the unsupported ``private`` keyword from ``set_margins``.

    Workaround for a pyte compatibility issue triggered by certain escape sequences.

    Also reports lines scrolled off the top of the full screen (for scrollback capture)
    and full-screen scrollback clears (``ED 3``, e.g. ``clear -x`` / "erase saved lines")
    via optional callbacks.
    """

    def __init__(
        self,
        columns: int,
        lines: int,
        on_scroll_off: Callable[[Mapping[int, Char]], None] | None = None,
        on_clear_scrollback: Callable[[], None] | None = None,
    ) -> None:
        # Rows the screen content has moved up so far.  Added to a row number it names a line
        # that stays the same when the screen scrolls.
        self.scrolled = 0
        super().__init__(columns, lines)
        self._on_scroll_off = on_scroll_off
        self._on_clear_scrollback = on_clear_scrollback

    def set_margins(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        """Keep the cursor line visible and retain rows scrolled off by a height reduction."""
        new_lines = lines or self.lines
        new_columns = columns or self.columns
        old_columns = self.columns
        if new_lines >= self.lines:
            super().resize(lines, columns)
            return

        scroll_rows = max(0, self.cursor.y - new_lines + 1)
        if self._on_scroll_off is not None:
            for y in range(scroll_rows):
                self._on_scroll_off(self.buffer[y])
        visible = {y - scroll_rows: row for y, row in self.buffer.items() if scroll_rows <= y < scroll_rows + new_lines}
        cursor_y = self.cursor.y - scroll_rows
        self.scrolled += scroll_rows

        super().resize(new_lines, new_columns)
        self.buffer.clear()
        self.buffer.update(visible)
        if new_columns < old_columns:
            for row in self.buffer.values():
                for x in range(new_columns, old_columns):
                    row.pop(x, None)
        self.cursor.y = cursor_y
        self.dirty.update(range(new_lines))

    def index(self) -> None:
        """Report the row about to scroll off the top of the full screen, then scroll as normal."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            self.scrolled += 1
            if self._on_scroll_off is not None:
                self._on_scroll_off(self.buffer[top])
        super().index()

    def erase_in_display(self, how: int = 0, *args: Any, **kwargs: Any) -> None:
        super().erase_in_display(how, *args, **kwargs)
        if how == _ED_ERASE_SCROLLBACK and self._on_clear_scrollback is not None:
            self._on_clear_scrollback()


class TerminalDisplay(ConsoleRenderable):
    """Rich renderable for a single terminal frame."""

    def __init__(self, lines: list[Text], cursor_x: int, cursor_y: int) -> None:
        self.lines = lines
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y
        self.selection: Selection | None = None
        self.selection_style: Style | None = None

    def render_row(self, y: int, doc_row: int | None = None) -> Text:
        """Return the styled line for live row ``y``, with cursor and selection highlighting applied.

        ``doc_row`` is the row's position within the full scrollback document and is used to look
        up the selection span; it defaults to ``y`` for the non-scrolling render path.
        """
        if doc_row is None:
            doc_row = y
        line = self.lines[y]
        span = self.selection.get_span(doc_row) if self.selection is not None else None
        if y != self.cursor_y and span is None:
            return line
        rendered_line = line.copy()
        if y == self.cursor_y:
            rendered_line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
        if span is not None and self.selection_style is not None:
            start, end = span
            rendered_line.stylize(self.selection_style, start, end if end != -1 else len(rendered_line))
        return rendered_line

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RichRenderResult:
        return [self.render_row(y) for y in range(len(self.lines))]


_WORD_RE = re.compile(r"\w+")

_DOUBLE_CLICK_CHAIN = 2
_TRIPLE_CLICK_CHAIN = 3
_MIDDLE_MOUSE_BUTTON = 2

_CTRL_KEYS: dict[str, str] = {
    "up": "\x1bOA",
    "down": "\x1bOB",
    "right": "\x1bOC",
    "left": "\x1bOD",
    "ctrl+right": "\x1b[1;5C",
    "ctrl+left": "\x1b[1;5D",
    "home": "\x1bOH",
    "end": "\x1b[F",
    "delete": "\x1b[3~",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "shift+tab": "\x1b[Z",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
    "f13": "\x1b[25~",
    "f14": "\x1b[26~",
    "f15": "\x1b[28~",
    "f16": "\x1b[29~",
    "f17": "\x1b[31~",
    "f18": "\x1b[32~",
    "f19": "\x1b[33~",
    "f20": "\x1b[34~",
}

_TERMINAL_COLORS: dict[str, str] = {
    "black": "#000000",
    "red": "#AB4642",
    "green": "#A1B56C",
    "yellow": "#FEA62B",
    "blue": "#2871C5",
    "magenta": "#BA8BAF",
    "cyan": "#86C1B9",
    "brown": "#FEA62B",
    "white": "#FFFFFF",
    "brightblack": "#444444",
    "default": "default",
}


def _translate_terminal_color(color: str) -> str:
    """Map a pyte color name or 6-digit hex string to a Rich-compatible color string."""
    if re.fullmatch("[0-9a-f]{6}", color, re.IGNORECASE):
        return f"#{color}"
    if color in _TERMINAL_COLORS:
        return _TERMINAL_COLORS[color]
    return color


def _encode_mouse(msg: list[Any]) -> bytes:
    """Encode a mouse event message as SGR escape bytes for the PTY."""
    if msg[0] == "click":
        x = int(msg[1]) + 1
        y = int(msg[2]) + 1
        button = int(msg[3])
        if button == 1:
            return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()
        return b""
    elif msg[0] == "scroll":
        x = int(msg[2]) + 1
        y = int(msg[3]) + 1
        if msg[1] == "up":
            return f"\x1b[<64;{x};{y}M".encode()
        if msg[1] == "down":
            return f"\x1b[<65;{x};{y}M".encode()
    return b""


class Terminal(ScrollView, can_focus=True):
    """PTY-backed terminal emulator widget for Textual.

    Embeds a shell process and renders its output via pyte and Rich.
    Delegates process management to a ``PtyBackend`` and shell-specific
    logic to a ``ShellDriver``.

    Programmatic directory changes run as one serialised transaction per
    terminal: the shell sets its line aside, a history-excluded ``cd`` is sent under
    output draining, and the shell's precmd hook (OSC 7) signals completion.
    Newer requests replace the pending target, so the terminal always converges
    on the last requested directory without showing intermediate prompts.
    """

    DEFAULT_CSS = """
    Terminal {
        background: $background;
    }
    """

    class PreCmd(Message):
        """Posted after each command completes in the embedded shell."""

        def __init__(self, terminal_widget: Terminal, cwd: PurePath) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            super().__init__()

    class PathChanged(Message):
        """Posted when the user changes the shell's working directory.

        Programmatic navigations started by ``request_cd`` never post this
        message.  ``owner`` is the value of ``Terminal.owner`` at the moment
        the user submitted the command, so handlers can update the pane that
        submitted it rather than whichever pane is active on arrival.
        """

        def __init__(
            self,
            terminal_widget: Terminal,
            cwd: PurePath,
            *,
            owner: object | None,
        ) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            self.owner = owner
            super().__init__()

    class Closed(Message):
        """Posted when the underlying shell process exits and ``keep_alive`` is False."""

        def __init__(self, terminal_widget: Terminal) -> None:
            self.terminal_widget = terminal_widget
            super().__init__()

    def __init__(
        self,
        command: str,
        backend: PtyBackend | None = None,
        driver: ShellDriver | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        keep_alive: bool = False,
        scrollback_lines: int = 0,
    ) -> None:
        self.command = command
        self.keep_alive = keep_alive
        self._backend = backend or LocalPtyBackend()
        self._driver = driver or detect_driver(command)
        self._started = False
        self._draining = False
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False
        self.bracketed_paste = False
        # The widget showed only the cursor's row at the last resize (see _one_row_view).
        self._was_one_row: bool = False
        self._scrollback_lines = scrollback_lines
        # Lines scrolled off the top of the live screen, oldest first; bounded by scrollback_lines.
        self._history: deque[Text] = deque(maxlen=max(0, scrollback_lines))
        self._follow_offset: int | None = None

        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        self._rebuild_handle: TimerHandle | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(
            self.ncol,
            self.nrow,
            on_scroll_off=self._on_history_line if scrollback_lines > 0 else None,
            on_clear_scrollback=self._clear_scrollback,
        )
        self._stream = pyte.Stream(self._screen)
        # True once any user-originated byte was forwarded to the shell since the
        # last precmd.  Internal writes (set aside, cd, put back, init code) never set it.
        self._input_since_precmd: bool = False
        # User input was forwarded before this shell's first hook report and has not been
        # followed by a later Enter, so a prompt line may hold text nobody can see as typed.
        self._startup_input: bool = False
        # (cursor line, cursor column, text of the cursor row) captured after every
        # stdout chunk until the first user byte, i.e. the fully drawn prompt (see _cursor_line).
        self._prompt_snapshot: tuple[int, int, str] | None = None
        # The other rows at the time of _prompt_snapshot, see _rows_around_cursor.
        self._rows_snapshot: dict[int, str] = {}
        # True while the shell waits for a command line.  Set on precmd, cleared
        # when user input containing a newline is forwarded or an internal cd is sent.
        self._at_prompt: bool = False
        # Opaque token set by the host (the active pane).  Copied into
        # _command_owner when the user submits a command.
        self.owner: object | None = None
        self._command_owner: object | None = None
        self._init_navigation_state()
        # Last known cwd reported by the shell via precmd.
        self._cwd: PurePath | None = None
        # Output of a new shell held back while startup draining is on; None once shown or dropped.
        self._startup_output: str | None = None
        self._startup_timer: TimerHandle | None = None
        # Counts shell processes so a startup timer of an old shell can never act on a new one.
        self._shell_generation: int = 0
        self._started_at: float = 0.0
        # True once this shell process reported through the NN hook.
        self._shell_hooked: bool = False
        self._early_exits: int = 0
        # The shell gave up on or never reported its hook: it is shown as a plain terminal.
        self._no_integration: bool = False
        self._gave_up: bool = False
        # Resolved by the precmd that follows a run_command() line.
        self._run_future: Future[None] | None = None
        # The line was set aside before run_command() and must be restored.
        self._run_stashed: bool = False

        super().__init__(name=name, id=id, classes=classes)
        # Permanently reserve a gutter column for the scrollback scrollbar so it never
        # overlays terminal content; disabled entirely (no gutter) when scrollback is off.
        self.styles.overflow_y = "scroll" if scrollback_lines > 0 else "hidden"
        self.styles.overflow_x = "hidden"

    def _init_navigation_state(self) -> None:
        """Set the state of the directory navigation transaction; ``_reset_shell_state`` clears it again."""
        # Newest requested directory not yet confirmed by the shell.
        self._nav_target: PurePath | None = None
        # A cd has been written and its precmd is awaited.
        self._nav_busy: bool = False
        # The target most recently sent to the shell, and how many consecutive
        # times it has been (re)sent without the shell ever reporting it as cwd.
        # Distinguishes "a newer target replaced this one" (reset, keep chaining)
        # from "the shell can't reach this target" (bounded, then give up).
        self._nav_last_sent_target: PurePath | None = None
        self._nav_same_target_attempts: int = 0
        # The shell's line was set aside before the cd and must be restored on completion.
        self._nav_stashed: bool = False
        # While a line is set aside: it held input, or the user typed meanwhile (see _restore_input).
        self._stash_held_input: bool = False
        # Resolved when the transaction chain completes; awaited by set_terminal_directory.
        self._nav_future: Future[PurePath] | None = None
        # A plain-shell cd was written; the precmd that follows is not a user navigation.
        self._plain_cd_sent: bool = False
        # Fires when no precmd follows a cd (hook lost); see _on_nav_timeout.
        self._nav_watchdog: TimerHandle | None = None
        # The precmd hook was re-installed once this shell session.
        self._hook_repaired: bool = False
        # False while the hook reports that the shell cannot set its line aside.
        self._shell_edits: bool = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def cwd(self) -> PurePath | None:
        """The shell's working directory as last reported by precmd, or None before the first prompt."""
        return self._cwd

    def start(self) -> None:
        if self._started:
            return

        self.ncol = 80
        self.nrow = 24
        self._update_virtual_size()

        self.recv_queue = asyncio.Queue()
        self._start_backend()
        self.recv_task_t = asyncio.create_task(self.recv())
        self._started = True

    def _start_backend(self) -> None:
        """Open the backend, start the send loop, and inject shell init code.

        Draining is enabled immediately so that the init code echo and
        startup prompt are held back.  Draining ends when the first precmd
        fires (the shell's hook emits an OSC 7 CWD sequence), or when the
        startup timeout reveals the held output without a hook report.
        """
        self._reset_shell_state()
        self._shell_generation += 1
        self._gave_up = False
        self._no_integration = False
        loop = asyncio.get_running_loop()
        self._started_at = loop.time()
        self._backend.open(self.command, self.nrow, self.ncol)
        self.send_queue = asyncio.Queue()
        self._run_task = asyncio.create_task(self._run())
        # Hold back the init code echo and startup prompt until the first precmd
        # arrives or the startup timeout reveals them.
        if self._backend.supports_precmd:
            self._draining = True
            self._startup_output = ""
            self._startup_timer = loop.call_later(_STARTUP_TIMEOUT, self._on_startup_timeout, self._shell_generation)
        init = self._driver.init_code()
        if init:
            self._backend.write(init.encode())

    def stop(self) -> None:
        if not self._started:
            return

        self._display = self.initial_display()
        self._clear_scrollback()
        self._started = False
        self._reset_shell_state()

        if self._rebuild_handle is not None:
            self._rebuild_handle.cancel()
            self._rebuild_handle = None

        if self.recv_task_t is not None:
            self.recv_task_t.cancel()
        if self._run_task is not None:
            self._run_task.cancel()

        self._backend.detach_readers()
        self._backend.teardown()

    def on_unmount(self) -> None:
        self.stop()

    def render(self) -> RenderableType:
        return self._display

    def render_line(self, y: int) -> Strip:
        """Render a line of content, tagging it with offset metadata needed for text selection.

        ``Terminal`` renders through a custom ``ConsoleRenderable`` (``TerminalDisplay``) rather
        than Textual's ``Content``/``Text`` visual pipeline, so the generic rendering path never
        embeds the per-character "offset" style metadata that Textual's compositor relies on to
        translate a screen coordinate into a text offset (see ``Screen.get_widget_and_offset_at``).
        Without it, mouse drags never start or extend a selection. Rendering each row explicitly
        here and calling ``Strip.apply_offsets`` restores that metadata, mirroring the approach
        used by Textual's own ``Log`` widget.

        The offset metadata, the selection span lookup, and text extraction all use the same
        document-row coordinate (``self.scroll_offset.y + y``) so that selection works correctly
        across the scrollback. Rows before the live screen (``row < len(self._history)``) come from
        the scrollback buffer and are also selectable (but cursor-less).
        """
        rich_style = self.rich_style
        row = int(self.scroll_offset.y) + y
        history_len = len(self._history)

        selection = self.text_selection
        selection_style = self.screen.get_component_rich_style("screen--selection") if selection is not None else None

        if row < history_len:
            line_text = self._render_history_row(row, selection, selection_style)
            strip = Strip(line_text.render(self.app.console), cell_len(line_text.plain))
            return strip.crop_extend(0, self.size.width, rich_style).apply_offsets(0, row)

        live_y = row - history_len
        if live_y >= len(self._display.lines):
            return Strip.blank(self.size.width, rich_style)

        self._display.selection = selection
        self._display.selection_style = selection_style
        line_text = self._display.render_row(live_y, row)
        strip = Strip(line_text.render(self.app.console), cell_len(line_text.plain))
        strip = strip.crop_extend(0, self.size.width, rich_style)
        return strip.apply_offsets(0, row)

    def _render_history_row(self, row: int, selection: Selection | None, selection_style: Style | None) -> Text:
        """Return the scrollback line at document ``row`` with selection highlighting applied."""
        line = self._history[row]
        span = selection.get_span(row) if selection is not None else None
        if span is None or selection_style is None:
            return line
        rendered_line = line.copy()
        start, end = span
        rendered_line.stylize(selection_style, start, end if end != -1 else len(rendered_line))
        return rendered_line

    @property
    def allow_select(self) -> bool:
        """Disable Textual's automatic text selection while mouse_tracking is active.

        Otherwise click-drag would start a text selection instead of being
        forwarded to a mouse-aware full-screen program running in the shell.
        """
        return self.ALLOW_SELECT and not self.mouse_tracking

    @property
    def allow_vertical_scroll(self) -> bool:
        """Disable Textual's native scrolling while a full-screen child app owns the mouse.

        Otherwise a wheel event would both scroll the scrollback view and be forwarded to
        the app as mouse-report bytes.
        """
        return super().allow_vertical_scroll and not self.mouse_tracking

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return the selected text, trimming trailing padding spaces from each row.

        The document spans the scrollback history followed by the live screen, matching the
        document-row coordinates used by ``render_line`` and ``_select_word_at``.
        """
        lines = list(self._history) + self._display.lines
        text = "\n".join(str(line).rstrip() for line in lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self.refresh()

    def _select_word_at(self, x: int, y: int) -> None:
        """Select the word under screen position ``(x, y)``, used for double-click selection.

        Works in both the scrollback history and the live buffer; ``row`` is the document-row
        coordinate shared with ``render_line`` and ``get_selection``.
        """
        row = int(self.scroll_offset.y) + y
        history_len = len(self._history)
        if row < history_len:
            line = self._history[row].plain
        else:
            live_y = row - history_len
            if live_y >= len(self._display.lines):
                return
            line = self._display.lines[live_y].plain
        for match in _WORD_RE.finditer(line):
            if match.start() <= x < match.end():
                self.screen.selections.clear()
                self.screen.selections[self] = Selection.from_offsets(Offset(match.start(), row), Offset(match.end(), row))
                self.screen.mutate_reactive(Screen.selections)
                return

    def _copy_selection(self) -> None:
        """Copy the current selection (if any belongs to this widget) to the clipboard."""
        if self in self.screen.selections:
            text = self.screen.get_selected_text()
            if text:
                self.app.copy_to_clipboard(text)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        if not self._started:
            return

        if event.key == "ctrl+f1":
            self.app.set_focus(None)
            return

        if event.key == "ctrl+shift+c":
            self._copy_selection()
            event.stop()
            return

        if event.key == "shift+pageup":
            event.stop()
            self.scroll_page_up(animate=False)
            return

        if event.key == "shift+pagedown":
            event.stop()
            self.scroll_page_down(animate=False)
            return

        event.stop()
        char = _CTRL_KEYS.get(event.key) or event.character
        if char:
            self._note_user_input(char, submits=event.key == "enter")
            self.scroll_end(animate=False, force=True, immediate=True)
            assert self.send_queue is not None
            self.send_queue.put_nowait(["stdin", char])

    async def on_paste(self, event: events.Paste) -> None:
        if not self._started:
            return

        event.stop()
        await self._paste_text(event.text)

    async def _paste_text(self, text: str) -> None:
        """Send *text* to the shell as a paste, wrapping it in bracketed-paste markers if requested."""
        if not self._started or not text:
            return
        # Wrap in bracketed-paste markers only if the shell requested that mode, so it
        # treats the whole paste as literal text instead of executing embedded newlines.
        if self.bracketed_paste:
            text = f"\x1b[200~{text}\x1b[201~"
        self._note_user_input(text)
        self.scroll_end(animate=False, force=True, immediate=True)
        assert self.send_queue is not None
        self.send_queue.put_nowait(["stdin", text])

    def _note_user_input(self, data: str, *, submits: bool = False) -> None:
        """Record that user-originated *data* is about to be forwarded to the shell.

        Data containing a newline submits a command line, so the shell leaves the
        prompt and the current ``owner`` is recorded as the command's owner.
        """
        self._input_since_precmd = True
        if self._nav_stashed or self._run_stashed:
            self._stash_held_input = True
        submitted = submits or "\r" in data or "\n" in data
        # A started shell is either still held back (_startup_output) or revealed without a report.
        if self._startup_output is not None or self._no_integration:
            self._startup_input = True
        elif submitted:
            self._startup_input = False
        if submitted:
            self._at_prompt = False
            self._command_owner = self.owner

    def _row_text(self, row: int) -> str:
        """Return the text of pyte screen *row* including trailing padding."""
        line = self._screen.buffer[row]
        return "".join(line[x].data for x in range(self._screen.columns))

    def _refresh_prompt_snapshot(self) -> None:
        """Capture the cursor and its row while no user byte has been sent since precmd.

        The snapshot therefore always reflects the latest drawn prompt and is
        frozen at the moment the user starts typing.
        """
        if self._input_since_precmd:
            return
        cursor = self._screen.cursor
        self._prompt_snapshot = (self._cursor_line(), cursor.x, self._row_text(cursor.y))
        self._rows_snapshot = self._rows_around_cursor()

    def _rows_around_cursor(self) -> dict[int, str]:
        """Return the screen rows other than the cursor's, keyed by their distance from it.

        Trailing spaces are removed.
        """
        cursor_y = self._screen.cursor.y
        return {y - cursor_y: self._row_text(y).rstrip(" ") for y in range(self._screen.lines) if y != cursor_y}

    def _count_new_rows(self) -> int:
        """Count the non-blank rows around the cursor that differ from the prompt snapshot.

        What was there when the prompt was drawn, such as output above it or text an earlier
        program left below it, does not count.
        """
        before = self._rows_snapshot
        return sum(1 for offset, text in self._rows_around_cursor().items() if text and text != before.get(offset, ""))

    def _cursor_line(self) -> int:
        """Return the line the cursor is on, counting the rows the screen has scrolled.

        Unlike the screen row, this number stays with a line when the screen scrolls: input that
        scrolls the prompt up cannot pass for it, and a wrapped line that was deleted again ends on
        the same number as before.
        """
        return self._screen.scrolled + self._screen.cursor.y

    def _holds_startup_input(self) -> bool:
        """Return True if the shell reports prompts but input typed before its first report may still be on the line.

        No automatic ``cd`` is written then, for any driver.  An Enter forwarded after the first
        report ends this state.
        """
        return self._startup_input and self._shell_hooked

    def has_input(self) -> bool:
        """Return True if the shell line probably holds user input.

        No user byte since the last precmd means the line is empty; this is
        certain.  Otherwise the screen is compared with the snapshot frozen at
        the first user byte, which detects "typed, then deleted everything".
        New text in another row counts as input too, because the cursor can be
        where the prompt left it while the input is further down or up.
        Without a snapshot the answer is conservatively True; this is also the
        case after input sent before the first hook report (``_startup_input``).
        """
        if not self._input_since_precmd:
            return False
        if self._prompt_snapshot is None:
            return True
        line, col, text = self._prompt_snapshot
        cursor = self._screen.cursor
        if self._cursor_line() != line or cursor.x != col or self._row_text(cursor.y) != text:
            return True
        return self._count_new_rows() > 0

    def request_cd(self, path: PurePath) -> None:
        """Ask the shell to change to *path* without waiting for completion.

        Requests are coalesced: only the newest target is kept.  A terminal that
        has given up ignores the request.  Until the first hook report
        (``_no_integration``) nothing is written: the target is kept and the
        first hook report applies it.  While the line may hold input typed before
        the first hook report (``_holds_startup_input``) the target is kept too,
        and the precmd after the user's next Enter applies it.  Otherwise the
        ``cd`` is sent now when the shell is at a prompt and no transaction is
        active, or on the next precmd.
        Line-editing shells run the awaitable, input-preserving transaction (see
        ``_start_nav``); a plain shell gets its ``cd`` on an empty prompt
        (see ``_send_plain_cd``) and nobody waits for it.
        """
        if not self._started or self._gave_up:
            return
        if self._no_integration or self._holds_startup_input():
            # Without the hook the shell is never at a prompt, and input typed before the first report
            # may still be on the line, so nothing may be written yet.  The newest target is kept; a
            # later hook report applies it (see _apply_pending_target).  Nobody waits for it.
            self._nav_target = path
            return
        if not self._nav_busy and self._nav_target is None and path == self._cwd:
            return
        self._nav_target = path
        # Only line-editing shells run the awaitable transaction; a plain shell
        # gets its cd on an empty prompt and nobody waits for it.
        if self._edits_lines() and (self._nav_future is None or self._nav_future.done()):
            self._nav_future = asyncio.get_running_loop().create_future()
        if not self._nav_busy and self._at_prompt:
            self._start_nav()

    async def set_terminal_directory(self, path: PurePath) -> PurePath:
        """Change the shell's working directory to *path*, preserving typed input.

        Returns the directory the transaction chain ends in: the CWD reported by
        the shell, or the fallback of ``_reset_shell_state`` / ``_reveal_startup_output``
        when the chain is cut short.  Returns the last known CWD when no navigation
        was needed.
        """
        if not self._started:
            return path
        self.request_cd(path)
        if self._nav_future is not None and not self._nav_future.done():
            return await self._nav_future
        return self._cwd or path

    async def run_command(self, line: str) -> None:
        """Run *line* in the shell as if the user typed it, and wait until it finishes.

        A line-editing shell first sets aside whatever its line holds, which is
        restored after the command's precmd.
        The command is echoed and recorded in the shell history like a user command.

        Completion relies on the shell's precmd hook and has no timeout, since a
        command may legitimately run arbitrarily long.
        If the command removes the hook (e.g. ``exec zsh``), this call never
        returns and the terminal stays busy until the shell process exits.
        Cancelling the awaiting coroutine does not stop the shell command; the
        terminal stays busy until its precmd arrives.

        Raises:
            TerminalBusyError: The shell is not ready, runs a command, is
                navigating, or holds typed input on a shell without line editing.
        """
        if not self._started or not self._at_prompt or self._nav_busy or self._run_future is not None:
            raise TerminalBusyError("The terminal is busy")
        has_input = self.has_input()
        if has_input and not self._edits_lines():
            raise TerminalBusyError("The terminal line holds typed input")
        future: Future[None] = asyncio.get_running_loop().create_future()
        self._run_future = future
        # has_input cannot see lines the shell put back by itself, so a real shell always stashes.
        self._run_stashed = self._edits_lines() and (has_input or not self._driver.supports_editor_protocol)
        if self._run_stashed:
            self._stash_input()
        self._at_prompt = False
        self._command_owner = self.owner
        self._backend.write((line + "\n").encode())
        await future

    def _finish_run(self) -> None:
        """Complete a pending ``run_command`` once its precmd arrives."""
        future = self._run_future
        if future is None:
            return
        self._run_future = None
        if self._run_stashed:
            self._run_stashed = False
            self._restore_input()
        if not future.done():
            future.set_result(None)

    def _edits_lines(self) -> bool:
        """Return True if the shell can set its line aside now; otherwise it is treated as a plain shell."""
        return self._driver.supports_line_editing and self._shell_edits

    def _stash_input(self) -> None:
        """Make the shell set its line aside so it can be restored later."""
        self._stash_held_input = self._stash_held_input or self.has_input()
        if self._driver.supports_editor_protocol:
            self._backend.write(STASH_SEQUENCE)
        else:
            self._backend.write(self._driver.set_aside_sequence())

    def _restore_input(self) -> None:
        """Put back what ``_stash_input`` set aside.

        Nothing is written when line editing was switched off meanwhile: the keys would stay on the
        line as text.  The shell keeps the saved line for the next restore.
        The line counts as occupied afterwards only if a stash saw input on it or the user typed while
        it was set aside; otherwise every navigation would make Enter on a panel go to the terminal.
        """
        held_input, self._stash_held_input = self._stash_held_input, False
        if not self._edits_lines():
            return
        if self._driver.supports_editor_protocol:
            self._backend.write(RESTORE_SEQUENCE)
        else:
            self._backend.write(self._driver.put_back_sequence())
        # The echo of the restored line arrives after the prompt and would be absorbed into
        # the snapshot, so report input as present for this prompt line.
        if held_input:
            self._input_since_precmd = True

    def _start_nav(self) -> None:
        """Send the hidden ``cd`` for ``_nav_target``, setting the shell's line aside first.

        Drivers with ``supports_editor_protocol`` get an atomic stash request,
        at most once per transaction chain and only with typed input.  Others get
        their driver's ``set_aside_sequence()`` before every ``cd``: the line may
        hold input that ``has_input`` cannot see, and the saved text accumulates
        until the one restore at the end of the chain.
        """
        if not self._edits_lines():
            self._send_plain_cd()
            # A caller may wait since before the shell reported that it cannot edit lines.
            if self._cwd is not None:
                self._resolve_nav_future(self._cwd)
            return
        assert self._nav_target is not None
        if self._nav_target == self._nav_last_sent_target:
            self._nav_same_target_attempts += 1
        else:
            self._nav_last_sent_target = self._nav_target
            self._nav_same_target_attempts = 1
        self._nav_busy = True
        self._at_prompt = False
        self._draining = True
        if not self._driver.supports_editor_protocol or (not self._nav_stashed and self.has_input()):
            self._nav_stashed = True
            self._stash_input()
        self._backend.write((" " + self._driver.cd_command(str(self._nav_target)) + "\n").encode())
        self._arm_watchdog()

    def _send_plain_cd(self) -> None:
        """Write the pending ``cd`` for a shell without line editing, only onto an empty line.

        With typed input the target is kept and tried again at the next precmd.
        """
        if self._nav_target is None or self.has_input():
            return
        target, self._nav_target = self._nav_target, None
        self._at_prompt = False
        self._plain_cd_sent = True
        self._backend.write((" " + self._driver.cd_command(str(target)) + "\n").encode())

    def _finish_nav(self, cwd: PurePath) -> None:
        """Complete the transaction chain: restore stashed text, end draining, resolve."""
        if self._nav_stashed:
            self._nav_stashed = False
            self._restore_input()
        self._nav_target = None
        self._nav_busy = False
        self._nav_last_sent_target = None
        self._nav_same_target_attempts = 0
        self._end_draining()
        self._resolve_nav_future(cwd)

    def _apply_pending_target(self, cwd: PurePath) -> None:
        """Start a transaction for a target stored while the shell was busy.

        While the line may hold startup input the target stays stored and waiting callers
        are resolved with *cwd*.
        """
        if self._nav_target is None:
            return
        if cwd == self._nav_target:
            self._nav_target = None
        elif not self._holds_startup_input():
            self._start_nav()
            return
        self._resolve_nav_future(cwd)

    def _resolve_nav_future(self, cwd: PurePath) -> None:
        if self._nav_future is not None and not self._nav_future.done():
            self._nav_future.set_result(cwd)

    def _end_draining(self) -> None:
        """Stop discarding stdout and redraw the prompt in place.

        The echoed command and its newline were discarded, so the cursor never
        advanced past the old prompt.  Return to column 0 and clear the line so
        the new prompt overwrites the old one, matching zsh's PROMPT_CR redraw.
        """
        self._draining = False
        if self._screen.cursor.x != 0:
            self._feed_stdout("\r\x1b[K")

    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        self._nav_watchdog = asyncio.get_running_loop().call_later(_NAV_WATCHDOG_TIMEOUT, self._on_nav_timeout)

    def _cancel_watchdog(self) -> None:
        if self._nav_watchdog is not None:
            self._nav_watchdog.cancel()
            self._nav_watchdog = None

    def _on_nav_timeout(self) -> None:
        """Handle a ``cd`` that produced no precmd within the watchdog interval.

        The realistic cause is a wiped precmd hook.  The first time this happens
        in a session the init code is re-sent followed by the ``cd``; the init
        line's own precmd reports the old directory, so the normal chaining rule
        re-sends the ``cd`` and the transaction completes.  A second timeout
        gives up so the terminal cannot stay drained.
        """
        self._nav_watchdog = None
        if not self._nav_busy:
            return
        if not self._hook_repaired:
            self._hook_repaired = True
            _logger.warning("No precmd after cd; re-installing the shell hook")
            if not self._driver.supports_editor_protocol:
                # The setup line is a hidden line too; _start_nav marks the line as set aside.
                self._stash_input()
            self._backend.write(self._driver.init_code().encode())
            self._start_nav()
            return
        _logger.warning("Navigation to %s timed out; giving up", self._nav_target)
        self._finish_nav(self._cwd if self._cwd is not None else self._nav_target_or_root())

    def _nav_target_or_root(self) -> PurePath:
        return self._nav_target if self._nav_target is not None else PurePath("/")

    def _reset_shell_state(self) -> None:
        """Forget prompt and navigation state when the shell process goes away."""
        self._cancel_watchdog()
        self._cancel_startup_timer()
        self._startup_output = None
        self._shell_hooked = False
        fallback = self._cwd if self._cwd is not None else self._nav_target_or_root()
        self._nav_target = None
        self._nav_busy = False
        self._nav_stashed = False
        self._stash_held_input = False
        self._plain_cd_sent = False
        self._at_prompt = False
        self._hook_repaired = False
        self._shell_edits = True
        self._input_since_precmd = False
        self._startup_input = False
        self._prompt_snapshot = None
        self._command_owner = None
        self._draining = False
        self._run_stashed = False
        if self._run_future is not None and not self._run_future.done():
            self._run_future.set_result(None)
        self._run_future = None
        self._resolve_nav_future(fallback)

    async def send(self, data: str, mode: Literal["normal", "silent"] = "normal") -> None:
        """Send *data* to the shell.

        When *mode* is ``"silent"`` and the backend supports precmd,
        the echo of *data* is suppressed until the next precmd fires.
        """
        if not self._started or self._gave_up:
            return
        self._note_user_input(data)
        if mode == "silent" and self._backend.supports_precmd:
            self._draining = True
        self._backend.write(data.encode())

    async def on_resize(self, _event: events.Resize) -> None:
        if not self._started:
            return
        old_rows = self._screen.lines
        was_one_row, self._was_one_row = self._was_one_row, self._one_row_view
        was_at_end = self.scroll_offset.y >= len(self._history) or self.is_vertical_scroll_end
        # A view that was following new output may start above the live screen; a one-row view
        # that keeps following has to show the cursor's row, not that first row.
        if self._one_row_view and self._follow_offset is not None and self._follow_offset == self.scroll_offset.y:
            was_at_end = True
        # Exclude the reserved scrollbar gutter column so the shell isn't sized wider
        # than what's actually visible.
        self.ncol = self.size.width - self.scrollbar_size_vertical
        self.nrow = max(self.size.height, self._driver.min_rows)
        assert self.send_queue is not None
        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
        self._resize_screen()
        if self._rebuild_handle is not None:
            self._rebuild_handle.cancel()
            self._rebuild_handle = None
        self._rebuild_display(follow_output=False)
        # With a two-row minimum the widget can change height while the screen does not.
        resized = self.nrow != old_rows or self._one_row_view != was_one_row
        if was_at_end and resized:
            if self._one_row_view:
                self.scroll_end(animate=False, force=True, immediate=True)
                self._follow_offset = None
                return
            # The rows of the old view that stay in sight: after a one-row view, those down to the cursor.
            shown_rows = self._screen.cursor.y + 1 if was_one_row else old_rows
            new_history_rows = max(0, self.nrow - shown_rows)
            target = max(0, len(self._history) - new_history_rows)
            self.scroll_to(y=target, animate=False, force=True, immediate=True)
            self._follow_offset = target if was_one_row or self.nrow > old_rows else None
        elif resized:
            self._follow_offset = None

    def _resize_screen(self) -> None:
        """Resize the screen model to ``nrow`` by ``ncol``.

        A smaller screen drops rows.  When that loses rows that count as input, the prompt
        snapshot is discarded, so the line counts as occupied until the next precmd.
        """
        new_rows = self._count_new_rows() if self._input_since_precmd and self._prompt_snapshot else 0
        self._screen.resize(self.nrow, self.ncol)
        if new_rows and self._count_new_rows() < new_rows:
            self._prompt_snapshot = None

    def _mouse_ready(self) -> bool:
        """Return True if the terminal is started and mouse tracking is active."""
        return self._started and self.mouse_tracking

    @property
    def _one_row_view(self) -> bool:
        """True while a one-row widget shows only the cursor's row of a screen with more rows.

        The document then ends with the cursor's row: the rows above it are reached by scrolling
        back, the rows below it are not shown.
        """
        return self.size.height == 1 and self._driver.min_rows > 1

    def _live_rows(self) -> int:
        """Return how many rows of the screen belong to the document."""
        return self._screen.cursor.y + 1 if self._one_row_view else self._screen.lines

    def _screen_row(self, y: int) -> int:
        """Return the screen row a mouse report names for widget row *y*."""
        return self._screen.cursor.y + y if self._one_row_view else y

    async def on_click(self, event: events.Click) -> None:
        if event.button == _MIDDLE_MOUSE_BUTTON:
            # Middle-click pastes the clipboard, same as ctrl+shift+v.
            await self._paste_text(self.app.clipboard)
            return
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["click", event.x, self._screen_row(event.y), event.button])

    async def _on_click(self, event: events.Click) -> None:
        """Select the word under the pointer on double-click instead of Textual's default select-all.

        Textual's message dispatch invokes ``_on_click`` for every class in the MRO that defines it, so
        without ``prevent_default()`` the base ``Widget._on_click`` would run right after this one and
        overwrite our word selection with its own select-all behaviour for the same double-click.
        This is also why ``on_click`` (middle-click paste, mouse-tracking forwarding) is called explicitly
        here rather than relying on Textual's normal dispatch: a class's own ``_on_click`` shadows its
        ``on_click`` in the same dispatch pass, so ``on_click`` would otherwise never run.

        The preceding ``MouseUp`` already ran (and copied whatever was selected *before* this click)
        because Textual dispatches double/triple-click ``Click`` events only after that ``MouseUp``,
        so the new selection made here must be copied explicitly rather than relying on ``_on_mouse_up``.
        """
        await self.on_click(event)
        if event.widget is self and self.allow_select and self.screen.allow_select and self.app.ALLOW_SELECT:
            if event.chain == _DOUBLE_CLICK_CHAIN:
                self._select_word_at(event.x, event.y)
                self._copy_selection()
                event.prevent_default()
            elif event.chain == _TRIPLE_CLICK_CHAIN and self.parent is not None:
                self.select_container.text_select_all()
                self._copy_selection()
                event.prevent_default()
        await self.broker_event("click", event)

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "down", event.x, self._screen_row(event.y)])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "up", event.x, self._screen_row(event.y)])

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        await super()._on_mouse_up(event)
        self._copy_selection()

    # ------------------------------------------------------------------
    # recv loop
    # ------------------------------------------------------------------

    def _handle_pre_cmd(self, raw: str, from_nn: bool = True, line_editing: bool = True) -> None:
        """Process a precmd notification from Nova Navigator's own shell hook.

        Third-party OSC 7 sequences (``from_nn`` False, e.g. oh-my-zsh) are
        ignored.  A precmd that arrives while a transaction is busy belongs to
        that transaction and either chains to a newer target or completes it.
        A precmd that reports the *same* mismatched target too many times in a
        row means the shell cannot reach it (e.g. the directory was deleted or
        is not accessible), so the transaction gives up rather than resending
        the identical ``cd`` forever.
        A transaction also ends when the shell reports that it cannot set its line
        aside (*line_editing* False): no further ``cd`` of it may be written.
        Any other precmd is a user command cycle: it may report a user-initiated
        directory change and then applies a target stored while the shell was busy.
        """
        if not from_nn:
            return
        self._shell_hooked = True
        self._shell_edits = line_editing
        self._no_integration = False
        self._early_exits = 0
        self._startup_output = None
        self._cancel_startup_timer()
        cwd = PurePath(raw.strip())
        plain_cd_sent, self._plain_cd_sent = self._plain_cd_sent, False
        cwd_changed = cwd != self._cwd and not plain_cd_sent
        is_bootstrap_precmd = self._cwd is None
        self._cwd = cwd
        self._at_prompt = True
        # Input typed before the first report counts as input on this prompt line too.
        self._input_since_precmd = self._startup_input
        self._prompt_snapshot = None
        if self._nav_busy:
            self._cancel_watchdog()
            can_retry = self._nav_same_target_attempts < _NAV_MAX_SAME_TARGET_ATTEMPTS
            if self._nav_target is not None and cwd != self._nav_target and can_retry and self._edits_lines():
                self._start_nav()
            else:
                if self._nav_target is not None and cwd != self._nav_target and not can_retry:
                    _logger.warning("Navigation to %s did not take effect after %d attempts; giving up", self._nav_target, self._nav_same_target_attempts)
                self._finish_nav(cwd)
        else:
            if self._draining:
                self._end_draining()
            owner = self._command_owner
            self._command_owner = None
            # The shell's very first precmd merely reports where it happened to launch, not a
            # real navigation, unless it is itself the result of a user-submitted command.
            if cwd_changed and (not is_bootstrap_precmd or owner is not None):
                self.post_message(Terminal.PathChanged(self, cwd, owner=owner))
            self._finish_run()
            self._apply_pending_target(cwd)
        self.post_message(Terminal.PreCmd(self, cwd))

    async def recv(self) -> None:
        """Process messages from recv_queue: stdout, pre_cmd, setup, disconnect."""
        assert self.recv_queue is not None
        try:
            while True:
                message = await self.recv_queue.get()
                stdout_fed = False
                disconnected = False
                for _ in range(_RECV_DRAIN_LIMIT):
                    cmd = message[0]
                    if cmd == "setup":
                        assert self.send_queue is not None
                        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
                    elif cmd == "pre_cmd":
                        from_nn = bool(message[_PRE_CMD_FROM_NN_IDX]) if len(message) > _PRE_CMD_FROM_NN_IDX else True
                        line_editing = bool(message[_PRE_CMD_LINE_EDITING_IDX]) if len(message) > _PRE_CMD_LINE_EDITING_IDX else True
                        self._handle_pre_cmd(str(message[1]), from_nn, line_editing)
                    elif cmd == "stdout":
                        if not self._draining:
                            self._feed_stdout(str(message[1]))
                            stdout_fed = True
                            self._refresh_prompt_snapshot()
                        elif self._startup_output is not None:
                            self._startup_output = (self._startup_output + str(message[1]))[-_STARTUP_BUFFER_CHARS:]
                    elif cmd == "disconnect":
                        disconnected = True
                        break
                    try:
                        message = self.recv_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if stdout_fed and not self._draining:
                    self._schedule_rebuild()
                if disconnected:
                    _logger.info("Terminal disconnected")
                    self._shell_exited()
        except asyncio.CancelledError:
            pass

    def _on_startup_timeout(self, generation: int) -> None:
        """Show a new shell's held-back output when its hook has not reported in time."""
        if generation == self._shell_generation:
            self._reveal_startup_output()

    def _cancel_startup_timer(self) -> None:
        if self._startup_timer is not None:
            self._startup_timer.cancel()
            self._startup_timer = None

    def _reveal_startup_output(self) -> None:
        """Show what the shell printed before its hook reported and stop draining.

        Without the hook the terminal is a plain terminal: it is never at a prompt and the terminal
        itself writes no ``cd`` (user input from ``send`` still goes to the shell).  A waiting
        navigation is resolved with the last known CWD, or the requested path when none is known,
        not with a CWD reported by the shell, so no caller waits forever.
        The newest navigation target is kept: a hook report that arrives later still enables the
        integration and applies it, but not onto a line that may hold input sent before that report
        (see ``_holds_startup_input``).
        """
        if self._startup_output is None:
            return
        text, self._startup_output = self._startup_output, None
        self._cancel_startup_timer()
        self._draining = False
        self._no_integration = True
        self._feed_stdout(text)
        self._refresh_prompt_snapshot()
        fallback = self._cwd if self._cwd is not None else self._nav_target_or_root()
        self._resolve_nav_future(fallback)
        self._schedule_rebuild()

    def _shell_exited(self) -> None:
        """Handle the end of a shell process: restart it, unless it keeps exiting before any hook report."""
        quick = not self._shell_hooked and asyncio.get_running_loop().time() - self._started_at < _EARLY_EXIT_SECONDS
        self._early_exits = self._early_exits + 1 if quick else 0
        if self.keep_alive and self._early_exits >= _MAX_EARLY_EXITS:
            self._give_up()
            return
        self._reset_shell_state()
        if self.keep_alive:
            self.respawn()
        else:
            self.post_message(Terminal.Closed(self))
            self.stop()

    def _give_up(self) -> None:
        """Stop restarting a shell that keeps exiting; its output and a message stay on screen."""
        self._reveal_startup_output()
        self._reset_shell_state()
        self._early_exits = 0
        self._gave_up = True
        self._no_integration = True
        self._feed_stdout("\r\n[nn] The shell keeps exiting and was not restarted.\r\n")
        self._schedule_rebuild()
        if self._run_task is not None:
            self._run_task.cancel()
            self._run_task = None
        self._backend.detach_readers()
        self._backend.teardown()

    # ------------------------------------------------------------------
    # Internal: screen rendering
    # ------------------------------------------------------------------

    def _schedule_rebuild(self) -> None:
        """Schedule a display rebuild if one is not already pending."""
        if self._rebuild_handle is None:
            self._rebuild_handle = asyncio.get_running_loop().call_later(1.0 / _DISPLAY_FPS, self._on_rebuild_timer)

    def _on_rebuild_timer(self) -> None:
        """Timer callback: clear the handle and rebuild the display."""
        self._rebuild_handle = None
        self._rebuild_display()

    def _feed_stdout(self, chars: str) -> None:
        """Scan for DECSET sequences and feed chars to the pyte stream."""
        for sep_match in re.finditer(_re_ansi_sequence, chars):
            sequence = sep_match.group(0)
            if sequence.startswith(_DECSET_PREFIX):
                body = sequence.removeprefix(_DECSET_PREFIX)
                action = body[-1]
                modes = set(body[:-1].split(";"))
                if _MOUSE_TRACKING_MODES & modes:
                    self.mouse_tracking = action == "h"
                if _BRACKETED_PASTE_MODE in modes:
                    self.bracketed_paste = action == "h"

        try:
            self._stream.feed(chars)
        except TypeError as error:
            log.warning("could not feed:", error)

    def _rebuild_display(self, *, follow_output: bool = True) -> None:
        """Rebuild Rich Text lines from the current pyte screen state and schedule a repaint."""
        was_at_end = self.is_vertical_scroll_end
        follow_recent = follow_output and self._follow_offset is not None and self._follow_offset == self.scroll_offset.y
        lines = [self._row_to_text(self._screen.buffer[y], self._screen.columns) for y in range(self._live_rows())]
        self._display = TerminalDisplay(lines, self._screen.cursor.x, self._screen.cursor.y)
        self._update_virtual_size()
        if follow_recent and self.is_mounted:
            history_room = max(0, self.nrow - self._screen.cursor.y - 1)
            target = max(0, len(self._history) - history_room)
            self.scroll_to(y=target, animate=False, force=True, immediate=True)
            self._follow_offset = target
        elif follow_output and was_at_end and self.is_mounted:
            # Stay pinned to the bottom on new output, but don't yank the view back
            # if the user has deliberately scrolled up to read scrollback.
            self.scroll_end(animate=False, force=True, immediate=True)
        elif follow_output:
            self._follow_offset = None
        self.refresh()

    def _row_to_text(self, line: Mapping[int, Char], columns: int) -> Text:
        """Convert one pyte screen row into a styled ``Text``, run-length-encoding style spans."""
        line_text = Text()
        style_change_pos = 0
        for x in range(columns):
            char: Char = line[x]
            line_text.append(char.data)

            is_last_col = x == columns - 1

            if x > 0:
                last_char: Char = line[x - 1]
                if not self.char_style_cmp(char, last_char):
                    last_style = self.char_rich_style(last_char)
                    line_text.stylize(last_style, style_change_pos, x)
                    style_change_pos = x
            if is_last_col:
                cur_style = self.char_rich_style(char)
                line_text.stylize(cur_style, style_change_pos, x + 1)

        return line_text

    def _on_history_line(self, row: Mapping[int, Char]) -> None:
        """Append a row scrolled off the top of the live screen to the scrollback buffer."""
        self._history.append(self._row_to_text(row, self._screen.columns))

    def _clear_scrollback(self) -> None:
        """Discard all scrollback content and reset the virtual size accordingly."""
        self._history.clear()
        self._update_virtual_size()

    def _update_virtual_size(self) -> None:
        """Sync ``virtual_size`` with the combined scrollback + live screen line count."""
        live_rows = self._live_rows() if self._one_row_view else self.nrow
        self.virtual_size = Size(self.ncol, len(self._history) + live_rows)

    def _process_stdout(self, chars: str) -> None:
        """Parse ANSI output, update the pyte screen, and refresh the display."""
        self._feed_stdout(chars)
        self._rebuild_display()

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    def char_rich_style(self, char: Char) -> Style:
        """Return a Rich Style built from the visual attributes of a pyte Char."""
        fg = _translate_terminal_color(char.fg)
        bg = _translate_terminal_color(char.bg)
        try:
            return Style(
                color=fg,
                bgcolor=bg,
                bold=char.bold,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse,
                blink=char.blink,
            )
        except ColorParseError as error:
            log.warning("color parse error:", error)
            return Style()

    def _char_style_key(self, char: Char) -> tuple[str, str, bool, bool, bool, bool, bool, bool]:
        """Return a tuple of visual style attributes for a pyte Char."""
        return (
            char.fg,
            char.bg,
            char.bold,
            char.italics,
            char.underscore,
            char.strikethrough,
            char.reverse,
            char.blink,
        )

    def char_style_cmp(self, given: Char, other: Char) -> bool:
        """Return True if two pyte Chars have identical visual style."""
        return self._char_style_key(given) == self._char_style_key(other)

    def initial_display(self) -> TerminalDisplay:
        """Return the initial (empty single-line) display state."""
        return TerminalDisplay([Text()], 0, 0)

    # ------------------------------------------------------------------
    # Internal: PTY management via backend
    # ------------------------------------------------------------------

    def respawn(self) -> None:
        """Tear down the current backend and start a fresh shell.

        Keeps ``recv_task_t`` alive.  Can be called from a ``Terminal.Closed``
        handler to restart the terminal on demand.
        """
        if self._run_task is not None:
            self._run_task.cancel()
            self._run_task = None

        self._backend.detach_readers()
        self._backend.teardown()

        self._clear_scrollback()
        self._screen = TerminalPyteScreen(
            self.ncol,
            self.nrow,
            on_scroll_off=self._on_history_line if self._scrollback_lines > 0 else None,
            on_clear_scrollback=self._clear_scrollback,
        )
        self._stream = pyte.Stream(self._screen)

        self._start_backend()

    async def _run(self) -> None:
        """Send loop: reads from send_queue and dispatches to backend."""
        loop = asyncio.get_running_loop()
        assert self.recv_queue is not None
        self._backend.attach_readers(loop, self.recv_queue)
        self.recv_queue.put_nowait(["setup", {}])

        try:
            assert self.send_queue is not None
            while True:
                msg = list(await self.send_queue.get())
                if msg[0] == "stdin":
                    self._backend.write(str(msg[1]).encode())
                elif msg[0] == "set_size":
                    self._backend.resize(cast("int", msg[1]), cast("int", msg[2]))
                elif msg[0] in ("click", "scroll"):
                    encoded = _encode_mouse(msg)
                    if encoded:
                        self._backend.write(encoded)
        except asyncio.CancelledError:
            pass
