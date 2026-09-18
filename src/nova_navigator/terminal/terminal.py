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
from dataclasses import dataclass
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
from nova_navigator.terminal.shell_editor_protocol import (
    EditorOperation,
    EditorResponse,
    create_editor_nonce,
)

_logger = logging.getLogger(__name__)

__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
]

_PRE_CMD_FROM_NN_IDX = 2  # index of the from_nn flag in a pre_cmd message


_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})
_RECV_DRAIN_LIMIT: int = 100
_DISPLAY_FPS: float = 60.0
_EDITOR_RESPONSE_TIMEOUT: float = 1.0
_NAVIGATION_STAGE_TIMEOUT: float = 1.0
_STARTUP_DRAIN_TIMEOUT: float = (
    10.0  # force-end startup draining if no prompt-ready arrives
)

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

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RichRenderResult:
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


@dataclass
class _NavigationRequest:
    """Internal navigation request state for one serialized transaction."""

    path: PurePath
    owner: object | None
    futures: list[Future[PurePath]]


class Terminal(Widget, can_focus=True):
    """PTY-backed terminal emulator widget for Textual.

    Embeds a shell process and renders its output via pyte and Rich.
    Delegates process management to a ``PtyBackend`` and shell-specific
    logic to a ``ShellDriver``.

    Directory navigation uses precmd-gated draining: when a programmatic
    ``cd`` is issued, stdout output is suppressed until the shell's precmd
    hook fires (emitting an OSC 7 CWD sequence).  This hides the ``cd``
    echo without requiring SIGSTOP/SIGCONT synchronisation, making it
    work identically for local shells and SSH connections.
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

        ``owner`` is the pane that owned the terminal when the user's Enter
        was forwarded, or ``None`` if no owner was recorded for this command.
        """

        def __init__(
            self,
            terminal_widget: Terminal,
            cwd: PurePath,
            *,
            user_initiated: bool,
            owner: object | None = None,
        ) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            self.user_initiated = user_initiated
            self.owner = owner
            super().__init__()

    class Closed(Message):
        """Posted when the underlying shell process exits and ``keep_alive`` is False."""

        def __init__(self, terminal_widget: Terminal) -> None:
            self.terminal_widget = terminal_widget
            super().__init__()

    class NavigationFailed(Message):
        """Posted when a programmatic navigation cannot be completed safely."""

        def __init__(
            self,
            terminal_widget: Terminal,
            path: PurePath,
            reason: str,
            owner: object | None = None,
        ) -> None:
            self.terminal_widget = terminal_widget
            self.path = path
            self.reason = reason
            self.owner = owner
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
        self._startup_awaits_ready: bool = False
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False

        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        self._rebuild_handle: TimerHandle | None = None
        self._startup_drain_handle: TimerHandle | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)

        self._editor_nonce: str = create_editor_nonce()
        self._editor_available: bool = False
        self._editor_ready_received: bool = False
        self._editor_sequence: int = 0
        self._editor_response_future: Future[EditorResponse] | None = None
        self._editor_expected_operation: EditorOperation | None = None
        self._editor_session_failed: bool = False
        self._editor_request_lock: asyncio.Lock = asyncio.Lock()
        self._startup_probe_task: Task[None] | None = None
        self._startup_probe_sent: bool = False
        self._precmd_after_ready_received: bool = False

        self._at_prompt: bool = False
        self._prompt_cursor_x: int = 0
        self._prompt_cursor_y: int = 0
        self._prompt_cursor_known: bool = False
        self._keys_forwarded_since_precmd: bool = False
        self._enter_lock: asyncio.Lock = asyncio.Lock()
        self._command_owner: object | None = None
        # Last known cwd reported by the shell via precmd.
        self._cwd: PurePath | None = None
        self._nav_active: _NavigationRequest | None = None
        self._nav_queued: _NavigationRequest | None = None
        self._nav_task: Task[None] | None = None
        self._nav_wait_pre_cmd_future: Future[PurePath] | None = None
        self._nav_wait_prompt_ready_future: Future[None] | None = None
        self._nav_waiting_for_cwd: bool = False
        self._nav_capture_prompt_output: bool = False
        self._nav_retained_prompt_chunks: list[str] = []

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
        """Open the backend, start the send loop, and inject shell startup code.

        Draining is enabled immediately so that the startup code echo and
        interactive-shell startup redraw are suppressed.  Draining ends on the
        first precmd after the editor integration's READY acknowledgement, or
        on the first precmd when no editor integration is installed.
        """
        self._reset_editor_session_state()
        self._backend.configure_editor_protocol(self._editor_nonce)
        self._backend.open(self.command, self.nrow, self.ncol)
        self.send_queue = asyncio.Queue()
        self._run_task = asyncio.create_task(self._run())
        # Suppress the startup-code echo and interactive-shell startup redraw until
        # the shell reaches its first editable prompt.  A watchdog force-ends
        # draining if that signal never arrives so the terminal cannot stay blank.
        if self._backend.supports_precmd:
            self._draining = True
            self._startup_drain_handle = asyncio.get_running_loop().call_later(
                _STARTUP_DRAIN_TIMEOUT, self._force_end_startup_draining
            )
        startup_code = self._driver.startup_code(self._editor_nonce)
        self._startup_awaits_ready = self._driver.supports_editor_protocol
        if startup_code:
            self._backend.write(startup_code.encode())

    def stop(self) -> None:
        if not self._started:
            return

        self._display = self.initial_display()
        self._started = False

        if self._rebuild_handle is not None:
            self._rebuild_handle.cancel()
            self._rebuild_handle = None

        if self._startup_drain_handle is not None:
            self._startup_drain_handle.cancel()
            self._startup_drain_handle = None

        if self.recv_task_t is not None:
            self.recv_task_t.cancel()
        if self._run_task is not None:
            self._run_task.cancel()

        self._cancel_startup_probe_task()
        self._cancel_pending_editor_request()
        self._cancel_navigation("terminal stopped")
        self._command_owner = None

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
            if event.key == "enter":
                self._at_prompt = False
            self._keys_forwarded_since_precmd = True
            assert self.send_queue is not None
            self.send_queue.put_nowait(["stdin", char])

    def has_input(self) -> bool:
        """Return True if the shell line probably holds input.

        This is the heuristic floor of the Enter decision ladder.  When the
        prompt-end cursor is known, the rendered cursor is compared against it.
        Otherwise the keystroke flag is used.  Neither is authoritative; the
        editor probe is preferred whenever it is available.
        """
        if self._prompt_cursor_known:
            if self._screen.cursor.y != self._prompt_cursor_y:
                return self._screen.cursor.y > self._prompt_cursor_y
            return self._screen.cursor.x > self._prompt_cursor_x
        return self._keys_forwarded_since_precmd

    async def submit_enter(self, owner: object | None = None) -> bool:
        """Handle an Enter key from pane focus.

        Returns True when Enter is consumed by the terminal and False only
        when an authoritative editor probe confirms the shell buffer is empty.
        """
        async with self._enter_lock:
            if not self._at_prompt or not self._editor_available:
                self._send_enter()
                return True

            response = await self._request_editor(EditorOperation.PROBE)
            if response is None:
                self._send_enter()
                return True

            if response.buffer_length == 0:
                return False

            self._command_owner = owner
            self._send_enter()
            return True

    def request_cd(self, path: PurePath, owner: object | None = None) -> None:
        """Queue a fire-and-forget programmatic directory change."""
        if not self._started:
            return
        self._enqueue_navigation_request(path=path, owner=owner, future=None)

    async def set_terminal_directory(
        self, path: PurePath, owner: object | None = None
    ) -> PurePath:
        """Queue a directory change and await the actual shell cwd result."""
        if not self._started:
            return path
        future: Future[PurePath] = asyncio.get_running_loop().create_future()
        self._enqueue_navigation_request(path=path, owner=owner, future=future)
        return await future

    async def send(
        self, data: str, mode: Literal["normal", "silent"] = "normal"
    ) -> None:
        """Send *data* to the shell.

        When *mode* is ``"silent"`` and the backend supports precmd,
        the echo of *data* is suppressed until the next precmd fires.
        """
        if not self._started:
            return
        self._keys_forwarded_since_precmd = True
        if mode == "silent" and self._backend.supports_precmd:
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

    def _handle_pre_cmd(self, raw: str, from_nn: bool = True) -> None:
        """Process a pre_cmd message: update nav state, post event.

        When ``from_nn`` is False the event originated from a third-party chpwd
        hook (e.g. oh-my-zsh) rather than from Nova Navigator's own precmd hook.
        These events are ignored so only NN-originated CWD notifications can
        advance a programmatic navigation transaction.

        This is also the sole driver of ``at_prompt`` and, once the editor
        protocol has signalled READY, the trigger for scheduling the one-shot
        startup probe.
        """
        if not from_nn:
            return
        cwd = PurePath(raw.strip())
        cwd_changed = cwd != self._cwd
        self._cwd = cwd
        self._keys_forwarded_since_precmd = False
        is_programmatic = False

        if (
            self._nav_active is not None
            and self._nav_waiting_for_cwd
            and self._nav_wait_pre_cmd_future is not None
            and not self._nav_wait_pre_cmd_future.done()
        ):
            self._nav_waiting_for_cwd = False
            self._nav_capture_prompt_output = True
            self._nav_wait_pre_cmd_future.set_result(cwd)
            is_programmatic = True

        self._at_prompt = not is_programmatic

        owner: object | None = None
        if not is_programmatic:
            # This command cycle is complete: the recorded owner is consumed here
            # whether or not the cwd actually changed.
            owner = self._command_owner
            self._command_owner = None

        if cwd_changed:
            self.post_message(
                Terminal.PathChanged(
                    self, cwd, user_initiated=not is_programmatic, owner=owner
                )
            )
        self.post_message(Terminal.PreCmd(self, cwd))

        # Startup and silent-send draining end at precmd, which fires before the
        # prompt is drawn.  When editor integration was written, wait for the
        # precmd that follows its READY acknowledgement so the integration echo
        # is hidden as well.
        if (
            not is_programmatic
            and (not self._startup_awaits_ready or self._editor_ready_received)
            and not self._nav_waiting_for_cwd
            and not self._nav_capture_prompt_output
        ):
            self._end_startup_draining()

        if self._editor_ready_received:
            self._precmd_after_ready_received = True
        self._maybe_schedule_startup_probe()

    def _end_startup_draining(self) -> None:
        """End startup/silent-send draining and redraw the prompt in place."""
        if self._startup_drain_handle is not None:
            self._startup_drain_handle.cancel()
            self._startup_drain_handle = None
        if not self._draining:
            return
        self._draining = False
        if self._screen.cursor.x != 0:
            self._feed_stdout("\r\x1b[K")

    def _force_end_startup_draining(self) -> None:
        """Watchdog: end draining if no prompt-ready signal ever arrives."""
        self._startup_drain_handle = None
        if (
            self._draining
            and not self._nav_waiting_for_cwd
            and not self._nav_capture_prompt_output
        ):
            self._draining = False

    def _handle_prompt_ready(self) -> None:
        """Snapshot the prompt-end cursor and advance navigation capture.

        Prompt-ready is a hint from a shared ZLE hook that plugins may replace.
        It never sets ``at_prompt`` or gates editor availability, and it no
        longer ends draining: startup/silent-send draining always ends via
        precmd instead, which fires before the prompt is drawn.
        """
        self._prompt_cursor_x = self._screen.cursor.x
        self._prompt_cursor_y = self._screen.cursor.y
        self._prompt_cursor_known = True
        if (
            self._nav_capture_prompt_output
            and self._nav_wait_prompt_ready_future is not None
            and not self._nav_wait_prompt_ready_future.done()
        ):
            self._nav_wait_prompt_ready_future.set_result(None)

    def _send_enter(self) -> None:
        """Send a carriage return to the backend and mark prompt state busy."""
        self._at_prompt = False
        self._backend.write(b"\r")

    def _settle_navigation_request(
        self, request: _NavigationRequest, result: PurePath
    ) -> None:
        """Resolve all futures attached to a navigation request."""
        for future in request.futures:
            if not future.done():
                future.set_result(result)

    def _cleanup_navigation_wait_state(self) -> None:
        """Reset in-flight navigation capture and wait futures."""
        if (
            self._nav_wait_pre_cmd_future is not None
            and not self._nav_wait_pre_cmd_future.done()
        ):
            self._nav_wait_pre_cmd_future.cancel()
        if (
            self._nav_wait_prompt_ready_future is not None
            and not self._nav_wait_prompt_ready_future.done()
        ):
            self._nav_wait_prompt_ready_future.cancel()
        self._nav_wait_pre_cmd_future = None
        self._nav_wait_prompt_ready_future = None
        self._nav_waiting_for_cwd = False
        self._nav_capture_prompt_output = False
        self._nav_retained_prompt_chunks = []

    def _enqueue_navigation_request(
        self,
        *,
        path: PurePath,
        owner: object | None,
        future: Future[PurePath] | None,
    ) -> None:
        """Queue or coalesce navigation requests while preserving awaiters."""
        if (
            self._nav_active is None
            and self._nav_queued is None
            and self._cwd is not None
            and path == self._cwd
        ):
            if future is not None and not future.done():
                future.set_result(self._cwd)
            return

        if self._nav_active is not None and self._nav_active.path == path:
            if future is not None:
                self._nav_active.futures.append(future)
            return

        if self._nav_queued is not None and self._nav_queued.path == path:
            if future is not None:
                self._nav_queued.futures.append(future)
            self._nav_queued.owner = owner
            return

        request = _NavigationRequest(path=path, owner=owner, futures=[])
        if future is not None:
            request.futures.append(future)

        if self._nav_active is None:
            self._nav_active = request
            if self._nav_task is None or self._nav_task.done():
                self._nav_task = asyncio.create_task(self._run_navigation_coordinator())
            return

        if self._nav_queued is not None:
            request.futures.extend(self._nav_queued.futures)
        self._nav_queued = request

    async def _attempt_best_effort_restore(self) -> None:
        """Attempt one bounded restore after a post-stash navigation failure."""
        try:
            await asyncio.wait_for(
                self._request_editor(EditorOperation.RESTORE),
                timeout=_NAVIGATION_STAGE_TIMEOUT,
            )
        except TimeoutError:
            return

    async def _run_navigation_transaction(self, request: _NavigationRequest) -> None:
        """Run one serialized stash/cd/restore transaction."""
        if not self._at_prompt or not self._editor_available:
            self._draining = False
            self.post_message(
                Terminal.NavigationFailed(
                    self,
                    request.path,
                    "terminal not ready for safe navigation",
                    owner=request.owner,
                )
            )
            self._settle_navigation_request(request, self._cwd or request.path)
            return

        self._at_prompt = False
        self._draining = True
        self._nav_wait_pre_cmd_future = asyncio.get_running_loop().create_future()
        self._nav_wait_prompt_ready_future = asyncio.get_running_loop().create_future()
        self._nav_waiting_for_cwd = False
        self._nav_capture_prompt_output = False
        self._nav_retained_prompt_chunks = []
        stash_succeeded = False

        try:
            if await self._request_editor(EditorOperation.STASH) is None:
                self.post_message(
                    Terminal.NavigationFailed(
                        self,
                        request.path,
                        "stash acknowledgement timeout",
                        owner=request.owner,
                    )
                )
                self._draining = False
                self._settle_navigation_request(request, self._cwd or request.path)
                return

            stash_succeeded = True
            self._nav_waiting_for_cwd = True
            self._backend.write(
                (" " + self._driver.cd_command(str(request.path)) + "\n").encode()
            )

            assert self._nav_wait_pre_cmd_future is not None
            actual_cwd = await asyncio.wait_for(
                self._nav_wait_pre_cmd_future, timeout=_NAVIGATION_STAGE_TIMEOUT
            )

            assert self._nav_wait_prompt_ready_future is not None
            await asyncio.wait_for(
                self._nav_wait_prompt_ready_future, timeout=_NAVIGATION_STAGE_TIMEOUT
            )

            if await self._request_editor(EditorOperation.RESTORE) is None:
                raise RuntimeError("restore acknowledgement timeout")

            retained_prompt = "".join(self._nav_retained_prompt_chunks)
            self._feed_stdout("\r\x1b[K" + retained_prompt)
            self._draining = False
            self._nav_capture_prompt_output = False
            self._schedule_rebuild()
            self._settle_navigation_request(request, actual_cwd)
        except (TimeoutError, RuntimeError, ValueError, OSError):
            if stash_succeeded:
                await self._attempt_best_effort_restore()
            self._draining = False
            self._nav_capture_prompt_output = False
            self.post_message(
                Terminal.NavigationFailed(
                    self,
                    request.path,
                    "navigation transaction failed",
                    owner=request.owner,
                )
            )
            self._settle_navigation_request(request, self._cwd or request.path)
        finally:
            self._cleanup_navigation_wait_state()

    async def _run_navigation_coordinator(self) -> None:
        """Run at most one navigation transaction at a time."""
        try:
            while self._nav_active is not None:
                request = self._nav_active
                await self._run_navigation_transaction(request)
                self._nav_active = self._nav_queued
                self._nav_queued = None
        except asyncio.CancelledError:
            pass
        finally:
            self._nav_task = None

    def _cancel_navigation(self, reason: str) -> None:
        """Cancel coordinator and settle all pending navigation waiters."""
        if self._nav_task is not None:
            self._nav_task.cancel()
            self._nav_task = None

        if self._nav_active is not None:
            self.post_message(
                Terminal.NavigationFailed(
                    self, self._nav_active.path, reason, owner=self._nav_active.owner
                )
            )
            self._settle_navigation_request(
                self._nav_active, self._cwd or self._nav_active.path
            )
        if self._nav_queued is not None:
            self.post_message(
                Terminal.NavigationFailed(
                    self, self._nav_queued.path, reason, owner=self._nav_queued.owner
                )
            )
            self._settle_navigation_request(
                self._nav_queued, self._cwd or self._nav_queued.path
            )

        self._nav_active = None
        self._nav_queued = None
        self._draining = False
        self._cleanup_navigation_wait_state()

    def _reset_editor_session_state(self) -> None:
        """Reset editor protocol state for a newly started shell session."""
        self._cancel_startup_probe_task()
        self._cancel_pending_editor_request()
        self._editor_nonce = create_editor_nonce()
        self._editor_available = False
        self._editor_ready_received = False
        self._editor_sequence = 0
        self._editor_expected_operation = None
        self._editor_session_failed = False
        self._startup_probe_sent = False
        self._precmd_after_ready_received = False
        self._at_prompt = False
        self._command_owner = None
        self._prompt_cursor_known = False
        self._keys_forwarded_since_precmd = False
        self._startup_awaits_ready = False

    def _disable_editor_protocol(self) -> None:
        """Disable editor protocol for the remainder of this shell session."""
        self._editor_available = False
        self._editor_session_failed = True
        self._command_owner = None

    def _cancel_pending_editor_request(self) -> None:
        """Cancel and clear any pending correlated editor request."""
        future = self._editor_response_future
        if future is not None and not future.done():
            future.cancel()
        self._editor_response_future = None
        self._editor_expected_operation = None

    def _cancel_startup_probe_task(self) -> None:
        """Cancel the in-flight startup probe task if present."""
        if self._startup_probe_task is not None:
            self._startup_probe_task.cancel()
            self._startup_probe_task = None

    def _maybe_schedule_startup_probe(self) -> None:
        """Schedule exactly one startup probe after READY and the next precmd."""
        if not self._driver.supports_editor_protocol:
            return
        if (
            self._editor_session_failed
            or self._editor_available
            or self._startup_probe_sent
        ):
            return
        if not self._editor_ready_received or not self._precmd_after_ready_received:
            return
        self._startup_probe_sent = True
        self._startup_probe_task = asyncio.create_task(self._run_startup_probe())

    async def _run_startup_probe(self) -> None:
        """Run one startup probe to verify the editor binding is callable."""
        try:
            response = await self._request_editor(EditorOperation.PROBE)
            if response is not None:
                self._editor_available = True
        except asyncio.CancelledError:
            raise
        finally:
            self._startup_probe_task = None

    async def _request_editor(
        self, operation: EditorOperation
    ) -> EditorResponse | None:
        """Send one correlated editor request and await a matching response."""
        if not self._driver.supports_editor_protocol or self._editor_session_failed:
            return None

        async with self._editor_request_lock:
            self._editor_sequence += 1
            expected_sequence = self._editor_sequence
            self._editor_expected_operation = operation
            future: Future[EditorResponse] = asyncio.get_running_loop().create_future()
            self._editor_response_future = future

            try:
                self._backend.write(self._driver.editor_request(operation))
                response = await asyncio.wait_for(
                    future, timeout=_EDITOR_RESPONSE_TIMEOUT
                )
            except TimeoutError:
                self._cancel_pending_editor_request()
                self._disable_editor_protocol()
                return None
            except (RuntimeError, ValueError, OSError):
                self._cancel_pending_editor_request()
                self._disable_editor_protocol()
                return None
            except asyncio.CancelledError:
                self._cancel_pending_editor_request()
                return None
            finally:
                if self._editor_response_future is future:
                    self._editor_response_future = None
                    self._editor_expected_operation = None

            if (
                response.operation is not operation
                or response.sequence != expected_sequence
            ):
                self._disable_editor_protocol()
                return None
            return response

    def _handle_editor_response(self, payload: object) -> None:
        """Handle one editor_response message from the backend."""
        if not isinstance(payload, EditorResponse):
            if (
                self._editor_response_future is not None
                and not self._editor_response_future.done()
            ):
                self._editor_response_future.set_exception(
                    ValueError("malformed editor response")
                )
            self._disable_editor_protocol()
            return

        if payload.nonce != self._editor_nonce:
            return

        if payload.operation is EditorOperation.READY:
            if payload.sequence == 0:
                self._editor_ready_received = True
                self._maybe_schedule_startup_probe()
            return

        if self._editor_response_future is None or self._editor_response_future.done():
            return

        expected_operation = self._editor_expected_operation
        if expected_operation is None:
            self._editor_response_future.set_exception(
                RuntimeError("missing expected editor operation")
            )
            return

        if (
            payload.operation is not expected_operation
            or payload.sequence != self._editor_sequence
        ):
            self._editor_response_future.set_exception(
                ValueError("stale or mismatched editor response")
            )
            return

        self._editor_response_future.set_result(payload)

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
                        from_nn = (
                            bool(message[_PRE_CMD_FROM_NN_IDX])
                            if len(message) > _PRE_CMD_FROM_NN_IDX
                            else True
                        )
                        self._handle_pre_cmd(str(message[1]), from_nn)
                    elif cmd == "stdout":
                        stdout = str(message[1])
                        if self._draining:
                            if self._nav_capture_prompt_output:
                                self._nav_retained_prompt_chunks.append(stdout)
                        else:
                            self._feed_stdout(stdout)
                            stdout_fed = True
                    elif cmd == "prompt_ready":
                        self._handle_prompt_ready()
                    elif cmd == "editor_response":
                        self._handle_editor_response(
                            message[1] if len(message) > 1 else None
                        )
                    elif cmd == "disconnect":
                        self._cancel_startup_probe_task()
                        self._cancel_pending_editor_request()
                        self._cancel_navigation("terminal disconnected")
                        self._at_prompt = False
                        self._command_owner = None
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
            self._rebuild_handle = asyncio.get_running_loop().call_later(
                1.0 / _DISPLAY_FPS, self._on_rebuild_timer
            )

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

        self._display = TerminalDisplay(
            lines, self._screen.cursor.x, self._screen.cursor.y
        )
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

    def _char_style_key(
        self, char: Char
    ) -> tuple[str, str, bool, bool, bool, bool, bool, bool]:
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
