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
from pathlib import PurePath
from typing import Any, Literal, cast

import pyte
from pyte.screens import Char
from rich.color import ColorParseError
from rich.console import Console, ConsoleOptions, ConsoleRenderable
from rich.console import RenderResult as RichRenderResult
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import RenderResult, log
from textual.message import Message
from textual.widget import Widget

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import ShellDriver, detect_driver

_logger = logging.getLogger(__name__)

__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
]

_KILL_LINE = "\x15"  # Ctrl+U — kill whole line to kill ring
_YANK = "\x19"  # Ctrl+Y — yank from kill ring
_END_OF_LINE = "\x05"  # Ctrl+E — move cursor to end of line


_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})
_RECV_DRAIN_LIMIT: int = 100
_DISPLAY_FPS: float = 60.0

_re_ansi_sequence = re.compile(r"(\x1b\[\??[\d;]*[a-zA-Z])")
_DECSET_PREFIX = "\x1b[?"


class TerminalPyteScreen(pyte.Screen):
    """pyte.Screen subclass that drops the unsupported ``private`` keyword from ``set_margins``.

    Workaround for a pyte compatibility issue triggered by certain escape sequences.
    """

    def set_margins(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class TerminalDisplay(ConsoleRenderable):
    """Rich renderable for a single terminal frame."""

    def __init__(self, lines: list[Text], cursor_x: int, cursor_y: int) -> None:
        self.lines = lines
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RichRenderResult:
        result: list[Text] = []
        for y, line in enumerate(self.lines):
            if y == self.cursor_y:
                rendered_line = line.copy()
                rendered_line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
            else:
                rendered_line = line
            result.append(rendered_line)
        return result


_CTRL_KEYS: dict[str, str] = {
    "up": "\x1bOA",
    "down": "\x1bOB",
    "right": "\x1bOC",
    "left": "\x1bOD",
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


class Terminal(Widget, can_focus=True):
    """PTY-backed terminal emulator widget for Textual.

    Embeds a shell process and renders its output via pyte and Rich.
    Delegates process management to a ``PtyBackend`` and shell-specific
    logic to a ``ShellDriver``.

    The SIGSTOP synchronisation model:
    When using a shell that supports it (zsh, bash), the precmd hook sends
    ``kill -STOP $$`` after writing the CWD to the precmd pipe.  This freezes
    the shell until ``Terminal`` calls ``backend.resume()``.  This makes
    directory navigation deterministic — no race between output suppression
    and shell prompt rendering.
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
        """Posted when the shell's working directory changes.

        For user commands this fires on every precmd.  For programmatic
        navigations it fires only once the *last* pending cd completes,
        so intermediate directories are never announced.

        ``user_initiated`` is True when the cd was typed by the user in
        the terminal (not triggered by ``request_cd``).  Handlers should
        only update external state (e.g. directory browser panels) for
        user-initiated changes.
        """

        def __init__(self, terminal_widget: Terminal, cwd: PurePath, *, user_initiated: bool) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            self.user_initiated = user_initiated
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

        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        self._rebuild_handle: TimerHandle | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)
        self._prompt_cursor_x: int = 0
        self._prompt_cursor_y: int = 0
        self._pending_yank: bool = False
        # Counts navigations whose pre_cmd acknowledgement has not yet
        # arrived.  Draining ends only when this reaches zero, preventing
        # a rapid second cd from leaking its echo after the first pre_cmd
        # clears draining.
        self._nav_pending: int = 0
        # Resolved when _nav_pending reaches 0.  Allows callers to await
        # completion of a programmatic directory change.
        self._nav_future: Future[PurePath] | None = None
        # Last known cwd reported by the shell via precmd.
        self._cwd: PurePath | None = None

        super().__init__(name=name, id=id, classes=classes)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        self.ncol = 80
        self.nrow = 24

        self.recv_queue = asyncio.Queue()
        self._start_backend()
        self.recv_task_t = asyncio.create_task(self.recv())
        self._started = True

    def _start_backend(self) -> None:
        """Open the backend, start the send loop, and inject shell init code.

        When the driver supports stop/resume, draining is enabled immediately.
        The shell will freeze after its first precmd (startup); recv() will
        send SIGCONT to resume it and end the startup drain.
        """
        self._backend.open(self.command, self.nrow, self.ncol)
        self.send_queue = asyncio.Queue()
        self._run_task = asyncio.create_task(self._run())
        # The shell will STOP after its first precmd.  Set draining so the
        # startup output (init code echo) is suppressed.
        # Both conditions are required: the driver must support SIGSTOP and
        # the backend must have a precmd signal to indicate when draining can end.
        if self._driver.supports_stop_resume and self._backend.supports_precmd:
            self._draining = True
        init = self._driver.init_code()
        if init:
            self._backend.write(init.encode())

    def stop(self) -> None:
        if not self._started:
            return

        self._display = self.initial_display()
        self._started = False

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

    def render(self) -> RenderResult:
        return self._display

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        if not self._started:
            return

        if event.key == "ctrl+f1":
            self.app.set_focus(None)
            return

        event.stop()
        char = _CTRL_KEYS.get(event.key) or event.character
        if char:
            assert self.send_queue is not None
            self.send_queue.put_nowait(["stdin", char])

    def has_input(self) -> bool:
        """Return True if the user has typed something on the current prompt line.

        Only meaningful when the driver supports prompt tracking
        (supports_prompt_ready).  Returns False for drivers like FallbackDriver
        that cannot reliably detect the prompt position.
        """
        if not self._driver.supports_prompt_ready:
            return False
        if self._screen.cursor.y != self._prompt_cursor_y:
            return self._screen.cursor.y > self._prompt_cursor_y
        return self._screen.cursor.x > self._prompt_cursor_x

    def request_cd(self, path: PurePath) -> None:
        """Issue a cd command to the shell without waiting for completion.

        When the shell is idle (no programmatic navigations pending) and
        already at *path*, the request is skipped.  ``Terminal.PathChanged``
        will be posted once the cd completes and no further navigations are
        pending.
        """
        if not self._started:
            return
        if self._nav_pending == 0 and self._cwd is not None and path == self._cwd:
            return
        if self._driver.supports_stop_resume and self._backend.supports_precmd:
            self._pending_yank = self.has_input()
            if self._pending_yank:
                self._backend.write(_KILL_LINE.encode())
            self._nav_pending += 1
            self._draining = True
            if self._nav_future is None or self._nav_future.done():
                self._nav_future = asyncio.get_running_loop().create_future()
            cmd = " " + self._driver.cd_command(str(path)) + "\n"
            self._backend.write(cmd.encode())
        else:
            cmd = " " + self._driver.cd_command(str(path)) + "\n"
            self._backend.write(cmd.encode())

    async def set_terminal_directory(self, path: PurePath) -> PurePath:
        """Change the shell's working directory to *path*, preserving any typed input.

        Returns the actual CWD reported by the shell once the last in-flight
        navigation completes.  See ``request_cd`` for the fire-and-forget
        variant used by the directory browser sync.
        """
        if not self._started:
            return path
        self.request_cd(path)
        if self._driver.supports_stop_resume and self._nav_future is not None and not self._nav_future.done():
            return await self._nav_future
        return self._cwd or path

    async def send(self, data: str, mode: Literal["normal", "silent"] = "normal") -> None:
        """Send *data* to the shell.

        When *mode* is ``"silent"`` and the driver supports stop/resume,
        the echo of *data* is suppressed until the next precmd fires.
        """
        if not self._started:
            return
        if mode == "silent" and self._driver.supports_stop_resume and self._backend.supports_precmd:
            self._draining = True
        self._backend.write(data.encode())

    async def on_resize(self, _event: events.Resize) -> None:
        if not self._started:
            return
        self.ncol = self.size.width
        self.nrow = self.size.height
        assert self.send_queue is not None
        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    def _mouse_ready(self) -> bool:
        """Return True if the terminal is started and mouse tracking is active."""
        return self._started and self.mouse_tracking

    async def on_click(self, event: events.Click) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "up", event.x, event.y])

    # ------------------------------------------------------------------
    # recv loop
    # ------------------------------------------------------------------

    def _handle_pre_cmd(self, raw: str) -> None:
        """Process a pre_cmd message: update nav state, resume shell, post event."""
        cwd = PurePath(raw.strip())
        cwd_changed = cwd != self._cwd
        self._cwd = cwd
        was_programmatic = self._nav_pending > 0
        if self._nav_pending > 0:
            self._nav_pending -= 1
        if self._draining and self._nav_pending == 0:
            # All in-flight navigations acknowledged.  Write yank bytes
            # before resuming so they arrive at the shell before it
            # prints the new prompt.
            if self._pending_yank:
                self._pending_yank = False
                self._backend.write((_YANK + _END_OF_LINE).encode())
            self._draining = False
            # Resolve the navigation future so callers unblock.
            if self._nav_future is not None and not self._nav_future.done():
                self._nav_future.set_result(cwd)
        # Always resume — the shell STOPs after every precmd,
        # not just after navigations.
        if self._driver.supports_stop_resume:
            self._backend.resume()
        if self._nav_pending == 0 and cwd_changed:
            self.post_message(Terminal.PathChanged(self, cwd, user_initiated=not was_programmatic))
        self.post_message(Terminal.PreCmd(self, cwd))

    def _handle_prompt_ready(self) -> None:
        """Snapshot cursor position as the prompt-end position."""
        self._prompt_cursor_x = self._screen.cursor.x
        self._prompt_cursor_y = self._screen.cursor.y

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
                        self._handle_pre_cmd(str(message[1]))
                    elif cmd == "stdout":
                        if not self._draining:
                            self._feed_stdout(str(message[1]))
                            stdout_fed = True
                    elif cmd == "prompt_ready":
                        self._handle_prompt_ready()
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
                    if self.keep_alive:
                        self.respawn()
                    else:
                        self.post_message(Terminal.Closed(self))
                        self.stop()
        except asyncio.CancelledError:
            pass

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

        try:
            self._stream.feed(chars)
        except TypeError as error:
            log.warning("could not feed:", error)

    def _rebuild_display(self) -> None:
        """Rebuild Rich Text lines from the current pyte screen state and schedule a repaint."""
        lines: list[Text] = []
        for y in range(self._screen.lines):
            line_text = Text()
            line = self._screen.buffer[y]
            style_change_pos = 0
            for x in range(self._screen.columns):
                char: Char = line[x]
                line_text.append(char.data)

                is_last_col = x == self._screen.columns - 1

                if x > 0:
                    last_char: Char = line[x - 1]
                    if not self.char_style_cmp(char, last_char):
                        last_style = self.char_rich_style(last_char)
                        line_text.stylize(last_style, style_change_pos, x)
                        style_change_pos = x
                if is_last_col:
                    cur_style = self.char_rich_style(char)
                    line_text.stylize(cur_style, style_change_pos, x + 1)

            lines.append(line_text)

        self._display = TerminalDisplay(lines, self._screen.cursor.x, self._screen.cursor.y)
        self.refresh()

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

        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
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
