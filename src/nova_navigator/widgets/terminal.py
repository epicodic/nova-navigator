"""A terminal emulator widget for Textual.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py

"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import re
import shlex
import signal
import struct
import termios
from asyncio import Task
from pathlib import PurePath
from typing import Any

import pyte
from pyte.screens import Char
from rich.color import ColorParseError
from rich.console import Console, ConsoleOptions, ConsoleRenderable
from rich.console import RenderResult as RichRenderResult
from rich.style import Style
from rich.text import Text
from textual import events, log
from textual.app import RenderResult
from textual.message import Message
from textual.widget import Widget

__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
    "shell_clear_prompt",
    "shell_cmd_cd",
    "shell_init_code",
]

_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})

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
        # Cursor reverse is applied here only; we copy the row so the stored line is never mutated.
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
    "default": "default",
}


def shell_init_code(fd: int) -> str:
    """Return zsh init code that hooks precmd to write the current directory to *fd*."""
    return f" _nn_precmd() {{ pwd>&{fd} }} ; precmd_functions+=(_nn_precmd)\n"


def shell_clear_prompt() -> str:
    """Return 200 backspace characters to erase the current shell prompt."""
    return "\b" * 200


def shell_cmd_cd(path: PurePath) -> str:
    """Return a shell command that silently changes directory to *path*."""
    return f"cd {shlex.quote(str(path))} >& /dev/null && printf '\\e[A'"


def _translate_terminal_color(color: str) -> str:
    """Map a pyte color name or 6-digit hex string to a Rich-compatible color string."""
    if re.fullmatch("[0-9a-f]{6}", color, re.IGNORECASE):
        return f"#{color}"
    if color in _TERMINAL_COLORS:
        return _TERMINAL_COLORS[color]
    return color


class Terminal(Widget, can_focus=True):
    """PTY-backed terminal emulator widget for Textual."""

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

    def __init__(
        self,
        command: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.command = command
        self._started = False
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False

        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        # Stored in _run() so stop() can remove readers without a running-loop call
        self._loop: asyncio.AbstractEventLoop | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)

        super().__init__(name=name, id=id, classes=classes)

    # ------------------------------------------------------------------
    # Public interface used by MainScreen
    # ------------------------------------------------------------------

    def get_shell_init_code(self) -> str:
        """Return zsh init code wiring the precmd hook to this terminal's pre-cmd pipe fd."""
        return shell_init_code(self.fd_pre_cmd_child)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        self.ncol = 80
        self.nrow = 24

        self.fd, self.fd_pre_cmd, self.fd_pre_cmd_child = self.open_terminal(command=self.command)
        self._p_out = os.fdopen(self.fd, "w+b", 0)
        self._p_out_pre_cmd = os.fdopen(self.fd_pre_cmd, "w+b", 0)
        self.send_queue = asyncio.Queue()
        self.recv_queue = asyncio.Queue()
        self._run_task = asyncio.create_task(self._run())
        self.recv_task_t = asyncio.create_task(self.recv())
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return

        self._display = self.initial_display()
        self._started = False

        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._p_out)
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._p_out_pre_cmd)

        if self.recv_task_t is not None:
            self.recv_task_t.cancel()
        if self._run_task is not None:
            self._run_task.cancel()

        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            os.waitpid(self.pid, os.WNOHANG)

        with contextlib.suppress(OSError):
            self._p_out.close()
        with contextlib.suppress(OSError):
            self._p_out_pre_cmd.close()

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

    async def send(self, data: str) -> None:
        if not self._started:
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["stdin", data])

    async def on_resize(self, _event: events.Resize) -> None:
        if not self._started:
            return
        self.ncol = self.size.width
        self.nrow = self.size.height
        assert self.send_queue is not None
        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    async def on_click(self, event: events.Click) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "up", event.x, event.y])

    # ------------------------------------------------------------------
    # recv loop
    # ------------------------------------------------------------------

    async def recv(self) -> None:
        assert self.recv_queue is not None
        try:
            while True:
                message = await self.recv_queue.get()
                cmd = message[0]
                if cmd == "setup":
                    assert self.send_queue is not None
                    self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])

                elif cmd == "pre_cmd":
                    cwd = PurePath(str(message[1]).strip())
                    self.post_message(Terminal.PreCmd(self, cwd))

                elif cmd == "stdout":
                    self._process_stdout(str(message[1]))

                elif cmd == "disconnect":
                    self.stop()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Internal: screen rendering
    # ------------------------------------------------------------------

    def _process_stdout(self, chars: str) -> None:
        """Parse ANSI output, update the pyte screen, and refresh the display."""
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

    def char_style_cmp(self, given: Char, other: Char) -> bool:
        """Return True if two pyte Chars have identical visual style."""
        return (
            given.fg,
            given.bg,
            given.bold,
            given.italics,
            given.underscore,
            given.strikethrough,
            given.reverse,
            given.blink,
        ) == (
            other.fg,
            other.bg,
            other.bold,
            other.italics,
            other.underscore,
            other.strikethrough,
            other.reverse,
            other.blink,
        )

    def initial_display(self) -> TerminalDisplay:
        """Return the initial (empty single-line) display state."""
        return TerminalDisplay([Text()], 0, 0)

    # ------------------------------------------------------------------
    # Internal: PTY setup
    # ------------------------------------------------------------------

    def open_terminal(self, command: str) -> tuple[int, int, int]:
        """Fork a PTY and exec *command*.

        Returns ``(master_fd, pre_cmd_read_fd, pre_cmd_child_fd_number)``.

        ``pre_cmd_child_fd_number`` is the numeric fd value of the write end of the
        pre-cmd pipe.  It is already closed in the parent process but its numeric value
        is retained so it can be embedded in the zsh init script that runs inside the
        child process (where the fd is still open).
        """
        fd_pre_cmd_parent, fd_pre_cmd_child = os.pipe()

        self.pid, fd = pty.fork()
        if self.pid == 0:
            # Child process
            os.close(fd_pre_cmd_parent)
            os.set_inheritable(fd_pre_cmd_child, True)  # noqa: FBT003
            argv = shlex.split(command)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["LC_ALL"] = "en_US.UTF-8"
            os.execvpe(argv[0], argv, env)  # noqa: S606
            raise RuntimeError("execvpe failed")

        # Parent process: close write end of the pre-cmd pipe.
        # Its numeric value is kept as self.fd_pre_cmd_child and embedded in the shell
        # init script where the fd is still open inside the child.
        os.close(fd_pre_cmd_child)
        return fd, fd_pre_cmd_parent, fd_pre_cmd_child

    def _dispatch_send_message(self, msg: list[Any]) -> None:
        """Write a single send-queue message to the PTY master."""
        if msg[0] == "stdin":
            self._p_out.write(str(msg[1]).encode())
        elif msg[0] == "set_size":
            winsize = struct.pack("HH", int(msg[1]), int(msg[2]))
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
        elif msg[0] == "click":
            x = int(msg[1]) + 1
            y = int(msg[2]) + 1
            button = int(msg[3])
            if button == 1:
                self._p_out.write(f"\x1b[<0;{x};{y}M".encode())
                self._p_out.write(f"\x1b[<0;{x};{y}m".encode())
        elif msg[0] == "scroll":
            x = int(msg[2]) + 1
            y = int(msg[3]) + 1
            if msg[1] == "up":
                self._p_out.write(f"\x1b[<64;{x};{y}M".encode())
            if msg[1] == "down":
                self._p_out.write(f"\x1b[<65;{x};{y}M".encode())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        # Store loop reference so stop() can call remove_reader from sync context
        self._loop = loop

        assert self.recv_queue is not None

        def on_output() -> None:
            assert self.recv_queue is not None
            try:
                read = self._p_out.read(65536).decode()
                self.recv_queue.put_nowait(["stdout", read])
            except UnicodeDecodeError as error:
                log.warning("decode error:", error)
            except Exception:  # noqa: BLE001
                loop.remove_reader(self._p_out)
                self.recv_queue.put_nowait(["disconnect", 1])

        def on_pre_cmd() -> None:
            assert self.recv_queue is not None
            try:
                self.recv_queue.put_nowait(["pre_cmd", self._p_out_pre_cmd.read(65536).decode()])
            except UnicodeDecodeError:
                pass
            except Exception:  # noqa: BLE001
                loop.remove_reader(self._p_out_pre_cmd)

        loop.add_reader(self._p_out, on_output)
        loop.add_reader(self._p_out_pre_cmd, on_pre_cmd)
        self.recv_queue.put_nowait(["setup", {}])

        try:
            assert self.send_queue is not None
            while True:
                self._dispatch_send_message(list(await self.send_queue.get()))
        except asyncio.CancelledError:
            pass
