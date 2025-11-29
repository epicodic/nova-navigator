"""A terminal emulator for Textual.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py
"""

from __future__ import annotations

import asyncio
import fcntl

# FIXME: when hitting Alt+e, app is waiting for any stdin (output not shown)
# TODO: do not show cursor when widget is not focused
import os
import pty
import re
import shlex
import signal
import struct
import termios
from asyncio import Task
from pathlib import Path, PurePath

import pyte
from pyte.screens import Char
from rich.color import ColorParseError
from rich.console import Console, ConsoleRenderable
from rich.style import Style
from rich.text import Text
from textual import events, log
from textual.app import RenderResult
from textual.message import Message
from textual.widget import Widget


class TerminalPyteScreen(pyte.Screen):
    """Overrides the pyte.Screen class to be used with TERM=linux."""

    def set_margins(self, *args, **kwargs) -> None:
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class TerminalDisplay(ConsoleRenderable):
    """Rich display for the terminal."""

    def __init__(self, lines, cursor_x: int, cursor_y: int):
        self.lines = lines
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y

    def __rich_console__(self, _console: Console, _options):
        for y, line in enumerate(self.lines):
            if y == self.cursor_y:
                line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
            yield line


_re_ansi_sequence = re.compile(r"(\x1b\[\??[\d;]*[a-zA-Z])")
DECSET_PREFIX = "\x1b[?"


def shell_init_code(fd: int) -> str:
    NN_PRECMD = "_nn_precmd() { pwd>&%d }" % fd  # noqa: UP031
    # _nn_precmd() { pwd>&%d; kill -STOP $$; }

    # if shell_type == SHELL_ZSH:
    return f" {NN_PRECMD} ; precmd_functions+=(_nn_precmd)\n"


def shell_clear_prompt() -> str:
    return "\b" * 200


def shell_cmd_cd(path: PurePath) -> str:
    return f"cd '{path}' >& /dev/null && printf '\\e[A'"


# OPTIMIZE: check a way to use textual.keys
_CTRL_KEYS = {
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


def _translate_terminal_color(color: str) -> str:
    if re.match("[0-9a-f]{6}", color, re.IGNORECASE):
        return f"#{color}"

    if color in _TERMINAL_COLORS:
        return _TERMINAL_COLORS[color]

    return color


class Terminal(Widget, can_focus=True):
    """Terminal widget."""

    DEFAULT_CSS = """
    Terminal {
        background: $background;
    }
    """

    # messages
    class PreCmd(Message):
        """Posted before running each command in the terminal."""

        def __init__(self, terminal_widget: Terminal, cwd: PurePath) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            super().__init__()

    textual_colors: dict | None

    def __init__(
        self,
        command: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.command = command

        self._started = False

        # default size, will be adapted on_resize
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False

        # variables used when starting the emulator: self.start()
        self.send_queue: asyncio.Queue = None
        self.recv_queue: asyncio.Queue = None
        self.recv_task_t: Task = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self.stream = pyte.Stream(self._screen)

        super().__init__(name=name, id=id, classes=classes)

    def start(self) -> None:
        if self._started:
            return

        # TerminalEmulator.__init__(self, command: str):
        # FIXME: fix ResourceWarning (manually close the fd / p_out broke (blocking)
        # The following error happens on self.fd, when stopping the emulator with stop():

        # ResourceWarning: unclosed file <_io.FileIO name=8 mode='rb+' closefd=True>

        # With the try-except blocks around the while True, the warnings are now
        # appearing immediately. But closing fd or p_out there, still causes a
        # crash/block/hang or the warning is still there...

        # It maybe has to be implemented somewhere at the CancelledError.

        self.ncol = 80
        self.nrow = 24

        self.fd, self.fd_pre_cmd, self.fd_pre_cmd_child = self.open_terminal(command=self.command)
        self.p_out = os.fdopen(self.fd, "w+b", 0)  # 0: buffering off
        self.p_out_pre_cmd = os.fdopen(self.fd_pre_cmd, "w+b", 0)  # 0: buffering off
        self.send_queue = asyncio.Queue()
        self.recv_queue = asyncio.Queue()
        self.recv_queue_precmd = asyncio.Queue()
        self.event = asyncio.Event()

        # TerminalEmulator.start
        # self.emulator.start()
        self.run_task = asyncio.create_task(self._run())

        ## Terminal.start()

        self.recv_task_t = asyncio.create_task(self.recv())

        self._started = True

    def stop(self) -> None:
        if not self._started:
            return

        self._display = self.initial_display()

        self.recv_task_t.cancel()

        # self.emulator.stop()
        # TerminalEmulator.stop
        self.run_task.cancel()

        os.kill(self.pid, signal.SIGTERM)
        os.waitpid(self.pid, 0)

        self._started = False

    def render(self) -> RenderResult:
        return self._display

    async def on_key(self, event: events.Key) -> None:
        if not self._started:
            return

        if event.key == "ctrl+f1":
            # release focus from widget: because event.stop() follows, releasing
            # focus would not be possible without mouse click.
            #
            # OPTIMIZE: make the key to release focus configurable
            self.app.set_focus(None)
            return

        event.stop()
        char = _CTRL_KEYS.get(event.key) or event.character
        if char:
            await self.send_queue.put(["stdin", char])

    async def send(self, data: str) -> None:
        if not self._started:
            return
        await self.send_queue.put(["stdin", data])

    async def on_resize(self, _event: events.Resize) -> None:
        if not self._started:
            return

        self.ncol = self.size.width
        self.nrow = self.size.height
        await self.send_queue.put(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    async def on_click(self, event: events.MouseEvent) -> None:
        if not self._started:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._started:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._started:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["scroll", "up", event.x, event.y])

    async def recv(self) -> None:
        try:
            while True:
                message = await self.recv_queue.get()
                cmd = message[0]
                if cmd == "setup":
                    await self.send_queue.put(["set_size", self.nrow, self.ncol])

                elif cmd == "pre_cmd":
                    cwd = PurePath(message[1].strip())
                    self.post_message(Terminal.PreCmd(self, cwd))

                elif cmd == "stdout":
                    chars = message[1]

                    # log("recv stdout:", chars)

                    for sep_match in re.finditer(_re_ansi_sequence, chars):
                        sequence = sep_match.group(0)
                        if sequence.startswith(DECSET_PREFIX):
                            parameters = sequence.removeprefix(DECSET_PREFIX).split(";")
                            if "1000h" in parameters:
                                self.mouse_tracking = True
                            if "1000l" in parameters:
                                self.mouse_tracking = False

                    try:
                        self.stream.feed(chars)
                    except TypeError as error:
                        # pyte could get into errors here: Screen.cursor_position()
                        # is getting 4 args. Happens when TERM=linux and using
                        # w3m (default options).

                        # This also happened when TERM is not set to "linux" and w3m
                        # is started without the option "-no-mouse".
                        log.warning("could not feed:", error)

                    lines = []
                    last_char: Char
                    last_style: Style
                    for y in range(self._screen.lines):
                        line_text = Text()
                        line = self._screen.buffer[y]
                        style_change_pos: int = 0
                        for x in range(self._screen.columns):
                            char: Char = line[x]

                            line_text.append(char.data)

                            # if style changed, stylize it with rich
                            if x > 0:
                                last_char = line[x - 1]
                                if not self.char_style_cmp(char, last_char) or x == self._screen.columns - 1:
                                    last_style = self.char_rich_style(last_char)
                                    line_text.stylize(last_style, style_change_pos, x + 1)
                                    style_change_pos = x

                            if self._screen.cursor.x == x and self._screen.cursor.y == y:
                                line_text.stylize("reverse", x, x + 1)

                        lines.append(line_text)

                    self._display = TerminalDisplay(lines, self._screen.cursor.x, self._screen.cursor.y)
                    self.refresh()

                elif cmd == "disconnect":
                    self.stop()
        except asyncio.CancelledError:
            # log.warning("Terminal.recv cancelled")
            pass

    def char_rich_style(self, char: Char) -> Style:
        """Returns a rich.Style from the pyte.Char."""
        foreground = _translate_terminal_color(char.fg)
        background = _translate_terminal_color(char.bg)
        style: Style
        try:
            style = Style(
                color=foreground,
                bgcolor=background,
                bold=char.bold,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse,
                blink=char.blink,
            )
        except ColorParseError as error:
            log.warning("color parse error:", error)
            style = Style()

        return style

    def char_style_cmp(self, given: Char, other: Char) -> bool:
        """Compares two pyte.Chars and returns if these are the same.

        Returns:
            True    if char styles are the same
            False   if char styles differ
        """
        return bool(
            given.fg == other.fg
            and given.bg == other.bg
            and given.bold == other.bold
            and given.italics == other.italics
            and given.underscore == other.underscore
            and given.strikethrough == other.strikethrough
            and given.reverse == other.reverse
            and given.blink == other.blink
        )

    def initial_display(self) -> TerminalDisplay:
        """Returns the display when initially creating the terminal or clearing it."""
        return TerminalDisplay([Text()], 0, 0)

    # class TerminalEmulator:
    def open_terminal(self, command: str) -> tuple[int, int, int]:
        fd_pre_cmd_parent, fd_pre_cmd_child = os.pipe()

        self.pid, fd = pty.fork()
        if self.pid == 0:
            os.close(fd_pre_cmd_parent)  # child doesn't read from itself
            # we want to use the descriptor in the child process
            os.set_inheritable(fd_pre_cmd_child, True)  # noqa: FBT003

            argv = shlex.split(command)
            # OPTIMIZE: do not use a fixed LC_ALL
            env = {
                "TERM": "xterm",
                "LC_ALL": "en_US.UTF-8",
                "HOME": str(Path.home()),
            }
            os.execvpe(argv[0], argv, env)
            # we never reach here
            raise RuntimeError("execvpe failed")
        os.close(fd_pre_cmd_child)  # parent doesn't write to child

        return fd, fd_pre_cmd_parent, fd_pre_cmd_child

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()

        # write pre-cmd to terminal
        # os.write(fd, self.get_precmd(fd_pre_cmd_write).encode())

        quiet = False

        def on_output() -> None:
            try:
                nonlocal quiet
                read = self.p_out.read(65536).decode()
                self.recv_queue.put_nowait(["stdout", read])

            except UnicodeDecodeError as error:
                # NOTE: this happens sometimes, eg in w3m browsing wrongly decoded docs
                # OPTIMIZE: here a screen refresh could be needed. some chars are
                #   left in the buffer when scrolling
                log.warning("decode error:", error)
            except Exception:
                # this exception tell's us to end the emulator:
                # throwed when exiting the command
                loop.remove_reader(self.p_out)
                self.recv_queue.put_nowait(["disconnect", 1])

        def on_pre_cmd() -> None:
            try:
                self.recv_queue.put_nowait(["pre_cmd", self.p_out_pre_cmd.read(65536).decode()])
            except UnicodeDecodeError:
                pass
            except Exception:
                loop.remove_reader(self.p_out_pre_cmd)

        loop.add_reader(self.p_out, on_output)
        loop.add_reader(self.p_out_pre_cmd, on_pre_cmd)
        await self.recv_queue.put(["setup", {}])
        try:
            while True:
                msg = await self.send_queue.get()
                if msg[0] == "stdin":
                    self.p_out.write(msg[1].encode())
                elif msg[0] == "set_size":
                    winsize = struct.pack("HH", msg[1], msg[2])
                    fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
                elif msg[0] == "click":
                    x = msg[1] + 1
                    y = msg[2] + 1
                    button = msg[3]

                    if button == 1:
                        self.p_out.write(f"\x1b[<0;{x};{y}M".encode())
                        self.p_out.write(f"\x1b[<0;{x};{y}m".encode())
                elif msg[0] == "scroll":
                    x = msg[2] + 1
                    y = msg[3] + 1

                    if msg[1] == "up":
                        self.p_out.write(f"\x1b[<64;{x};{y}M".encode())
                    if msg[1] == "down":
                        self.p_out.write(f"\x1b[<65;{x};{y}M".encode())
        except asyncio.CancelledError:
            # log.warning("TerminalEmulator._run cancelled")
            pass
