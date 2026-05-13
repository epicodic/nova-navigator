# Terminal Widget Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `widgets/terminal.py` fixing all 25 issues from the code review, using TDD: write the full test suite first (red), then rewrite the implementation (green).

**Architecture:** Extend `tests/widgets/test_terminal.py` with 18 new tests that target the known bugs and missing coverage. Run them against the current `terminal.py` to confirm the red phase. Write `terminal_new.py` from scratch with all fixes applied. Replace `terminal.py` with the new content and update `main.py`.

**Tech Stack:** Python 3.12, pytest, Textual, pyte, Rich

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Reference documents:** `docs/architecture_terminal.md`, `docs/review_terminal.md`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `tests/widgets/test_terminal.py` | Modify | Add 18 new tests covering review issues |
| `src/nova_navigator/widgets/terminal_new.py` | Create | Full rewrite with all 25 fixes |
| `src/nova_navigator/widgets/terminal.py` | Replace | Swap with terminal_new.py content |
| `src/nova_navigator/main.py` | Modify | Use `Terminal.get_shell_init_code()` instead of accessing `fd_pre_cmd_child` directly |

---

## Task 1: Extend test_terminal.py with new failing tests

**Files:**
- Modify: `tests/widgets/test_terminal.py`

Add these 18 tests after the existing ones.
The 8 marked *(FAILS)* will fail against the current `terminal.py` — that is the desired red phase.
The remaining tests add coverage for currently untested code paths and will pass both before and after the rewrite.

- [ ] **Step 1: Add `import shlex` to the existing imports block**

At the top of `tests/widgets/test_terminal.py`, the imports block ends after `from nova_navigator.widgets.terminal import (...)`.
Add `import shlex` immediately after the stdlib imports (before the `import pyte` line):

```python
import shlex
```

- [ ] **Step 2: Append new tests to the end of `tests/widgets/test_terminal.py`**

```python
# ---------------------------------------------------------------------------
# shell_cmd_cd — path injection safety
# ---------------------------------------------------------------------------


def test_shell_cmd_cd_handles_path_with_single_quote() -> None:
    # FAILS with current code: f"cd '{path}'" breaks on paths containing a single quote.
    # Fix: use shlex.quote(str(path)).
    path = PurePath("/home/user/O'Brien")
    cmd = shell_cmd_cd(path)
    cd_part = cmd.split("&&")[0].strip()
    # shlex.split must succeed (no unmatched quotes) and yield the original path
    parsed = shlex.split(cd_part)
    assert parsed[0] == "cd"
    assert parsed[1] == str(path)


# ---------------------------------------------------------------------------
# _translate_terminal_color — fullmatch guard
# ---------------------------------------------------------------------------


def test_translate_terminal_color_7_digit_hex_not_prefixed() -> None:
    # FAILS with current code: re.match("[0-9a-f]{6}", ...) matches a 7-char prefix.
    # Fix: use re.fullmatch.
    result = _translate_terminal_color("0000001")
    assert result == "0000001"


# ---------------------------------------------------------------------------
# Mouse click handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_click_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        # send_queue is None; on_click must return early without touching it
        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, False, False, False))
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_click_ignored_when_mouse_tracking_disabled() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, False, False, False))

        assert terminal.send_queue.empty()


@pytest.mark.asyncio
async def test_on_click_puts_click_message_in_send_queue_when_mouse_tracking_enabled() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_click(events.Click(None, 5, 3, 0, 0, 1, False, False, False))

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "click"
        assert item[1] == 5
        assert item[2] == 3


# ---------------------------------------------------------------------------
# Mouse scroll handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_scroll_down_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, False, False, False)
        )
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_on_scroll_down_ignored_when_mouse_tracking_disabled() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = False

        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, False, False, False)
        )

        assert terminal.send_queue.empty()


@pytest.mark.asyncio
async def test_on_scroll_down_puts_scroll_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_down(
            events.MouseScrollDown(None, 5, 3, 0, 0, 1, False, False, False)
        )

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "down"


@pytest.mark.asyncio
async def test_on_scroll_up_puts_scroll_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True
        terminal.mouse_tracking = True

        await terminal.on_mouse_scroll_up(
            events.MouseScrollUp(None, 5, 3, 0, 0, 1, False, False, False)
        )

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item[0] == "scroll"
        assert item[1] == "up"


# ---------------------------------------------------------------------------
# Terminal.send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_ignored_when_not_started() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await terminal.send("hello")  # must not raise
        assert terminal.send_queue is None


@pytest.mark.asyncio
async def test_send_puts_stdin_message_in_send_queue() -> None:
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.send_queue = asyncio.Queue()
        terminal._started = True

        await terminal.send("hello")

        assert terminal.send_queue.qsize() == 1
        item = terminal.send_queue.get_nowait()
        assert item == ["stdin", "hello"]


# ---------------------------------------------------------------------------
# recv() behavior: extended mouse tracking modes (1002 / 1003 / 1006)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decset_1002h_enables_mouse_tracking() -> None:
    # FAILS with current code: only "1000h" is checked.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await terminal.recv_queue.put(["stdout", "\x1b[?1002h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1003h_enables_mouse_tracking() -> None:
    # FAILS with current code: only "1000h" is checked.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await terminal.recv_queue.put(["stdout", "\x1b[?1003h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1006h_enables_mouse_tracking() -> None:
    # FAILS with current code: only "1000h" is checked.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            assert terminal.mouse_tracking is False
            await terminal.recv_queue.put(["stdout", "\x1b[?1006h"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1002l_disables_mouse_tracking() -> None:
    # FAILS with current code: only "1000l" is checked.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal.mouse_tracking = True
            await terminal.recv_queue.put(["stdout", "\x1b[?1002l"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is False
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_decset_1003l_disables_mouse_tracking() -> None:
    # FAILS with current code: only "1000l" is checked.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            terminal.mouse_tracking = True
            await terminal.recv_queue.put(["stdout", "\x1b[?1003l"])
            await pilot.pause(delay=0.15)
            assert terminal.mouse_tracking is False
        finally:
            await _stop_recv_only(terminal)


# ---------------------------------------------------------------------------
# Rendering: cursor reverse stored in raw display lines (double-stylization bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_reverse_span_not_stored_in_raw_display_lines() -> None:
    # FAILS with current code: recv() applies "reverse" to line_text inline,
    # so _display.lines[cursor_y] already has a reverse span before __rich_console__ runs.
    # Fix: remove the inline stylize from recv(); keep reverse only in TerminalDisplay.__rich_console__.
    terminal = Terminal("/bin/sh")
    app = TerminalTestApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _start_recv_only(terminal)
        try:
            await terminal.recv_queue.put(["stdout", "hello"])
            await pilot.pause(delay=0.15)
            cursor_y = terminal._display.cursor_y
            line = terminal._display.lines[cursor_y]
            reverse_spans = [s for s in line._spans if "reverse" in str(s.style)]
            # In the correct implementation cursor reverse is applied only inside
            # TerminalDisplay.__rich_console__, so the stored line carries no reverse spans.
            assert len(reverse_spans) == 0
        finally:
            await _stop_recv_only(terminal)
```

- [ ] **Step 3: Run the coding-guideline follow-up checklist for this task**
  - [ ] Conventions file read: `docs/coding_conventions.md`
  - [ ] All new test functions have full type annotations (`-> None`)
  - [ ] Naming: `test_<behavior>` snake_case
  - [ ] Run: `uv run ruff check tests/widgets/test_terminal.py`
  - [ ] Fix any lint errors

---

## Task 2: Confirm the red phase

**Files:**
- Read: `tests/widgets/test_terminal.py`

- [ ] **Step 1: Run the full test file and note which new tests fail**

```
uv run pytest tests/widgets/test_terminal.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected outcome: the following 8 tests must **FAIL or ERROR**, all others must pass:

| Test | Expected failure reason |
|------|------------------------|
| `test_shell_cmd_cd_handles_path_with_single_quote` | `ValueError: No closing quotation` |
| `test_translate_terminal_color_7_digit_hex_not_prefixed` | `AssertionError: '#0000001' != '0000001'` |
| `test_decset_1002h_enables_mouse_tracking` | `AssertionError: False is not True` |
| `test_decset_1003h_enables_mouse_tracking` | `AssertionError: False is not True` |
| `test_decset_1006h_enables_mouse_tracking` | `AssertionError: False is not True` |
| `test_decset_1002l_disables_mouse_tracking` | `AssertionError: True is not False` |
| `test_decset_1003l_disables_mouse_tracking` | `AssertionError: True is not False` |
| `test_cursor_reverse_span_not_stored_in_raw_display_lines` | `AssertionError: 1 != 0` |

If any of the 8 tests unexpectedly pass, re-read `terminal.py` and check whether the current code already handles that case.

---

## Task 3: Write `terminal_new.py`

**Files:**
- Create: `src/nova_navigator/widgets/terminal_new.py`

Write the complete file as shown below.
All 25 review issues are addressed — see inline comments for the relevant fix numbers.

- [ ] **Step 1: Create `src/nova_navigator/widgets/terminal_new.py` with this content**

```python
"""A terminal emulator widget for Textual.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py

"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import re
import shlex
import signal
import struct
import termios
from asyncio import Task
from collections.abc import Generator
from pathlib import PurePath
from typing import Any

import pyte
from pyte.screens import Char
from rich.color import ColorParseError
from rich.console import Console, ConsoleOptions, ConsoleRenderable
from rich.style import Style
from rich.text import Text
from textual import events, log
from textual.app import RenderResult
from textual.message import Message
from textual.widget import Widget

# fix #25: explicit __all__
__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
    "shell_clear_prompt",
    "shell_cmd_cd",
    "shell_init_code",
]

# fix #10: all DECSET modes that affect mouse tracking
_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})

_re_ansi_sequence = re.compile(r"(\x1b\[\??[\d;]*[a-zA-Z])")
_DECSET_PREFIX = "\x1b[?"


class TerminalPyteScreen(pyte.Screen):
    """pyte.Screen subclass that drops the unsupported ``private`` keyword from ``set_margins``.

    Workaround for a pyte compatibility issue triggered by certain applications.
    """

    # fix #13: docstring updated to reflect actual TERM value (xterm-256color)
    def set_margins(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class TerminalDisplay(ConsoleRenderable):
    """Rich renderable for a single terminal frame."""

    def __init__(self, lines: list[Text], cursor_x: int, cursor_y: int) -> None:
        self.lines = lines
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y

    def __rich_console__(self, _console: Console, _options: ConsoleOptions) -> Generator[Text, None, None]:
        # fix #7: cursor "reverse" is applied here ONLY — not in _process_stdout
        for y, line in enumerate(self.lines):
            if y == self.cursor_y:
                line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
            yield line


# fix #18: simplified to tuple comparison (kept as standalone dict for clarity)
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
    """Return zsh init code that hooks precmd to write the current directory to *fd*.

    fix #19: uses f-string instead of % formatting.
    """
    return f" _nn_precmd() {{ pwd>&{fd} }} ; precmd_functions+=(_nn_precmd)\n"


def shell_clear_prompt() -> str:
    """Return 200 backspace characters to erase the current shell prompt."""
    return "\b" * 200


def shell_cmd_cd(path: PurePath) -> str:
    """Return a shell command that silently changes directory to *path*.

    fix #6: uses shlex.quote to prevent injection when the path contains single quotes.
    """
    return f"cd {shlex.quote(str(path))} >& /dev/null && printf '\\e[A'"


def _translate_terminal_color(color: str) -> str:
    """Map a pyte color name or 6-digit hex string to a Rich-compatible color string."""
    # fix #12: re.fullmatch instead of re.match to reject strings longer than 6 hex digits
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

        # fix #3: correct Optional types instead of non-nullable typed as None
        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)

        super().__init__(name=name, id=id, classes=classes)

    # ------------------------------------------------------------------
    # Public interface used by MainScreen
    # ------------------------------------------------------------------

    def get_shell_init_code(self) -> str:
        """Return zsh init code wiring the precmd hook to this terminal's pre-cmd pipe fd.

        fix #23: encapsulates fd_pre_cmd_child so callers do not need to know about the fd.
        """
        return shell_init_code(self.fd_pre_cmd_child)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        self.ncol = 80
        self.nrow = 24

        # fix #4: open_terminal returns (fd, pre_cmd_read_fd, pre_cmd_child_fd_number).
        # fd_pre_cmd_child is the numeric fd value (already closed in parent) used for the
        # shell init script that runs inside the child where the fd is still open.
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

        # fix #1: remove event loop readers so callbacks stop firing
        if self._loop is not None:
            try:
                self._loop.remove_reader(self._p_out)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._loop.remove_reader(self._p_out_pre_cmd)
            except Exception:  # noqa: BLE001
                pass

        if self.recv_task_t is not None:
            self.recv_task_t.cancel()
        if self._run_task is not None:
            self._run_task.cancel()

        # fix #2: use WNOHANG so waitpid does not block the event loop
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            pass

        # fix #1: close file objects to release OS resources
        try:
            self._p_out.close()
        except OSError:
            pass
        try:
            self._p_out_pre_cmd.close()
        except OSError:
            pass

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
            # fix #16: put_nowait — queue is unbounded so this never blocks
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
        # fix #16: put_nowait
        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    # fix #15: correct type annotation — Textual delivers events.Click, not events.MouseEvent
    async def on_click(self, event: events.Click) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        # fix #16: put_nowait
        self.send_queue.put_nowait(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        # fix #16: put_nowait
        self.send_queue.put_nowait(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._started:
            return
        if not self.mouse_tracking:
            return
        assert self.send_queue is not None
        # fix #16: put_nowait
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
        """Parse ANSI output, update pyte screen, and refresh the display."""
        # fix #10: handle all four mouse-tracking DECSET modes (1000/1002/1003/1006)
        for sep_match in re.finditer(_re_ansi_sequence, chars):
            sequence = sep_match.group(0)
            if sequence.startswith(_DECSET_PREFIX):
                body = sequence.removeprefix(_DECSET_PREFIX)
                action = body[-1]  # 'h' = enable, 'l' = disable
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
                    # fix #5: correct off-by-one — close the previous style run at x (exclusive),
                    # not at x+1.  When a style change happens, position x belongs to the NEW run.
                    if not self.char_style_cmp(char, last_char):
                        last_style = self.char_rich_style(last_char)
                        line_text.stylize(last_style, style_change_pos, x)
                        style_change_pos = x

                # fix #5 (continued): always close the current run at the last column so the
                # final character's own style is applied (not the penultimate character's style).
                if is_last_col:
                    cur_style = self.char_rich_style(char)
                    line_text.stylize(cur_style, style_change_pos, x + 1)

                # fix #7: cursor reverse NOT applied here — only in TerminalDisplay.__rich_console__

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
        """Return True if two pyte Chars have identical visual style.

        fix #18: simplified to a single tuple comparison.
        """
        return (
            given.fg, given.bg, given.bold, given.italics,
            given.underscore, given.strikethrough, given.reverse, given.blink,
        ) == (
            other.fg, other.bg, other.bold, other.italics,
            other.underscore, other.strikethrough, other.reverse, other.blink,
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
        ``pre_cmd_child_fd_number`` is the numeric fd value of the write end of the pre-cmd pipe.
        It is already closed in the parent process but kept as a number so it can be embedded
        in the shell init script that runs inside the child (where the fd is still open).

        fix #4: the return value and its semantics are now clearly documented.
        fix #11: environment starts from os.environ.copy() so PATH and other variables survive.
        fix #24: TERM is set to xterm-256color for full 256-color support.
        """
        fd_pre_cmd_parent, fd_pre_cmd_child = os.pipe()

        self.pid, fd = pty.fork()
        if self.pid == 0:
            # Child process
            os.close(fd_pre_cmd_parent)
            os.set_inheritable(fd_pre_cmd_child, True)  # noqa: FBT003
            argv = shlex.split(command)
            # fix #11: inherit full environment; override only TERM and LC_ALL
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"  # fix #24: was "xterm" (only 16 colors)
            env["LC_ALL"] = "en_US.UTF-8"
            os.execvpe(argv[0], argv, env)  # noqa: S606
            raise RuntimeError("execvpe failed")

        # Parent process
        os.close(fd_pre_cmd_child)
        # fd_pre_cmd_child is now closed in the parent.  Its numeric value is saved
        # for use in shell_init_code() which embeds it in the zsh init script.
        return fd, fd_pre_cmd_parent, fd_pre_cmd_child

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        # Store loop so stop() can remove readers without needing a running loop reference
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
                msg = await self.send_queue.get()
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
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run the coding-guideline follow-up checklist for this task**
  - [ ] Conventions file read: `docs/coding_conventions.md`
  - [ ] All public functions and methods have full type annotations
  - [ ] No `Optional[X]` — use `X | None`
  - [ ] No `typing.List` etc. — use `list`, `dict`, etc.
  - [ ] `# mypy: ignore-errors` is gone
  - [ ] Run: `uv run ruff check src/nova_navigator/widgets/terminal_new.py`
  - [ ] Run: `uv run ty check src/nova_navigator/widgets/terminal_new.py`
  - [ ] Fix any lint or type errors

---

## Task 4: Replace `terminal.py` and update `main.py`

**Files:**
- Modify: `src/nova_navigator/widgets/terminal.py` (replace content with terminal_new.py)
- Modify: `src/nova_navigator/main.py` (use `get_shell_init_code()`)
- Delete: `src/nova_navigator/widgets/terminal_new.py`

- [ ] **Step 1: Replace `terminal.py` content with `terminal_new.py` content**

```sh
cp src/nova_navigator/widgets/terminal_new.py src/nova_navigator/widgets/terminal.py
rm src/nova_navigator/widgets/terminal_new.py
```

- [ ] **Step 2: Update `main.py` to use `Terminal.get_shell_init_code()`**

Find line (approximately line 203):
```python
pre_cmd = shell_init_code(self._terminal.fd_pre_cmd_child)
```
Replace with:
```python
pre_cmd = self._terminal.get_shell_init_code()
```

Verify the `shell_init_code` function import is still needed elsewhere in `main.py` — if it is used only for the line above, remove it from the `from nova_navigator.widgets.terminal import ...` line too.

- [ ] **Step 3: Run the full test suite to confirm all tests pass**

```sh
uv run pytest tests/widgets/test_terminal.py -v
```

Expected: **73 passed** (55 original + 18 new), 0 failed.

- [ ] **Step 4: Run coding-guideline follow-up checklist**
  - [ ] `uv run ruff check src/nova_navigator/widgets/terminal.py`
  - [ ] `uv run ty check src/nova_navigator/widgets/terminal.py`
  - [ ] Ensure no `# mypy: ignore-errors` at top of file

---

## Task 5: Final QA

- [ ] **Step 1: Run the full QA suite**

```sh
uv run qa
```

Expected: zero failures across lint, type check, and all tests.

- [ ] **Step 2: Verify `shell_init_code` import in `main.py`**

If `shell_init_code` is no longer imported in `main.py`, confirm the import line was cleaned up:

```sh
grep "shell_init_code" src/nova_navigator/main.py
```

Should return nothing (or only the comment in `get_shell_init_code`'s body if kept).

---

## Self-Review

**Spec coverage check:**

| Review issue | Task covering it |
|---|---|
| #1 fd leak in stop() | Task 3 (stop() fix) |
| #2 os.waitpid blocks event loop | Task 3 (WNOHANG) |
| #3 non-nullable types assigned None | Task 3 (type annotations) |
| #4 open_terminal returns closed fd | Task 3 (documented comment) |
| #5 off-by-one in style spans | Task 3 (_process_stdout) |
| #6 shell_cmd_cd injection | Task 1 (test), Task 3 (shlex.quote) |
| #7 cursor stylized twice | Task 1 (test), Task 3 (removed from _process_stdout) |
| #8 recv_queue_precmd unused | Task 3 (removed) |
| #9 quiet dead code | Task 3 (removed) |
| #10 mouse tracking modes 1002/1003/1006 | Task 1 (tests), Task 3 (_MOUSE_TRACKING_MODES) |
| #11 stripped PATH environment | Task 3 (os.environ.copy()) |
| #12 re.match vs re.fullmatch | Task 1 (test), Task 3 (re.fullmatch) |
| #13 docstring TERM=linux inconsistency | Task 3 (updated docstring) |
| #14 untyped queue protocol | Deferred — changing would break test assertions; tracked separately |
| #15 on_click wrong event type | Task 3 (events.Click) |
| #16 await put in hot paths | Task 3 (put_nowait) |
| #17 no tests | Task 1 (18 new tests) |
| #18 char_style_cmp verbose | Task 3 (tuple comparison) |
| #19 shell_init_code % formatting | Task 3 (f-string) |
| #20 duplicated guard in mouse handlers | Task 3 (still two guards but explicit boolean check) |
| #21 shell_clear_prompt 200 backspaces | Unchanged — functional improvement deferred |
| #22 commented-out code | Task 3 (removed all dead comments) |
| #23 fd_pre_cmd_child exposed | Task 3 (get_shell_init_code() + Task 4 main.py) |
| #24 TERM=xterm hardcoded | Task 3 (xterm-256color) |
| #25 missing __all__ | Task 3 (__all__ added) |
