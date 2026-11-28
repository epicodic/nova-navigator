"""The one-row view of the embedded terminal: bash and plain shells keep two rows in the PTY.

bash 5.2 can loop forever, using a whole CPU and ignoring SIGTERM, SIGHUP and SIGINT, when its prompt
has invisible characters, the typed line is wider than the terminal, and the PTY has one row (or
readline scrolls horizontally) when the size changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import BashDriver, FallbackDriver, ShellDriver, ZshDriver
from nova_navigator.terminal.terminal import Terminal
from nova_navigator.terminal.vfs_shell.virtual_pty_backend import VfsShellDriver

_BASH = "/bin/bash"

# A colour reset marked as invisible at the start, as some distributions write their prompt.
_INVISIBLE = r"\[\e[31m\]\[\e(B\e[m\]"
_LONG_PROMPT = _INVISIBLE + "p" * 58 + "$ "
_SHORT_PROMPT = _INVISIBLE + "pp$ "
# Visibly a little wider than the test terminal, whose scrollbar leaves 78 columns.
_FULL_WIDTH_PROMPT = _INVISIBLE + "p" * 77 + "$ "


class _HostApp(App[None]):
    """Starts the terminal while composing, as the navigator does, so that every layout size reaches the shell."""

    def __init__(self, terminal: Terminal, height: int) -> None:
        super().__init__()
        self._terminal = terminal
        self._terminal.styles.height = height

    def compose(self) -> ComposeResult:
        self._terminal.start()
        yield self._terminal


class _RecordingBackend(PtyBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sizes: list[tuple[int, int]] = []
        self.writes: list[bytes] = []

    def open(self, command: str, rows: int, cols: int) -> int | None:
        self.sizes.append((rows, cols))
        return None

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def resume(self) -> None:
        pass

    def attach_readers(self, loop: asyncio.AbstractEventLoop, recv_queue: asyncio.Queue[list[object]]) -> None:
        pass

    def detach_readers(self) -> None:
        pass

    def teardown(self) -> None:
        pass


async def _wait_until(predicate: Callable[[], bool], what: str, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# Real shells
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home, so that no test reads or writes the developer's shell setup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("PS1", "PROMPT_COMMAND", "HISTFILE", "ENV", "BASH_ENV", "INPUTRC", "ZDOTDIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@contextlib.asynccontextmanager
async def _real_bash(home: Path, prompt: str, height: int, width: int = 80) -> AsyncIterator[tuple[Terminal, Pilot[None]]]:
    """Run bash in a terminal widget of *height* rows and *width* columns; the shell is killed at the end in any case."""
    (home / ".bashrc").write_text(f"PS1='{prompt}'\n")
    backend = LocalPtyBackend()
    terminal = Terminal(_BASH, backend=backend, driver=BashDriver(), keep_alive=True, scrollback_lines=100)
    app = _HostApp(terminal, height)
    async with app.run_test(size=(width, 24)) as pilot:
        pid = backend._pid
        try:
            await pilot.pause()
            await _wait_until(lambda: terminal._at_prompt and "$" in _screen_text(terminal), "the first prompt")
            await asyncio.sleep(0.3)
            yield terminal, pilot
        finally:
            # A shell that loops ignores everything else.  Nothing reaps it before teardown(), so
            # the pid is still the shell's.
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            terminal.stop()


def _screen_text(terminal: Terminal) -> str:
    return "\n".join(terminal._screen.display)


async def _press(terminal: Terminal, key: str, character: str, count: int = 1) -> None:
    for _ in range(count):
        await terminal.on_key(events.Key(key, character))
    await asyncio.sleep(0.5)


async def _assert_answers(terminal: Terminal, home: Path) -> None:
    try:
        await asyncio.wait_for(terminal.run_command("touch answered"), timeout=5)
    except TimeoutError:
        pytest.fail("the shell stopped answering")
    assert (home / "answered").exists()


@pytest.mark.asyncio
async def test_bash_survives_minimizing_with_a_long_typed_line(home: Path) -> None:
    async with _real_bash(home, _LONG_PROMPT, height=20) as (terminal, pilot):
        await _press(terminal, "x", "x", 100)
        terminal.styles.height = 1
        await pilot.pause(0.5)
        await _assert_answers(terminal, home)


@pytest.mark.asyncio
async def test_bash_survives_narrowing_a_one_row_terminal_with_a_long_typed_line(home: Path) -> None:
    async with _real_bash(home, _LONG_PROMPT, height=1, width=120) as (terminal, pilot):
        await _press(terminal, "x", "x", 100)
        await pilot.resize_terminal(100, 24)
        await pilot.pause(0.5)
        await _assert_answers(terminal, home)


@pytest.mark.asyncio
async def test_bash_with_horizontal_scroll_mode_in_inputrc_survives_a_width_change(home: Path) -> None:
    (home / ".inputrc").write_text("set horizontal-scroll-mode on\n")
    async with _real_bash(home, _LONG_PROMPT, height=20) as (terminal, pilot):
        await _press(terminal, "x", "x", 160)
        await pilot.resize_terminal(98, 24)
        await pilot.pause(0.5)
        await _assert_answers(terminal, home)
        await asyncio.wait_for(terminal.run_command("bind -v > variables"), timeout=5)
        assert "set horizontal-scroll-mode off" in (home / "variables").read_text()


def _bash_version() -> tuple[int, int]:
    command = [_BASH, "--norc", "--noprofile", "-c", "echo ${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"]
    major, minor = subprocess.run(command, capture_output=True, text=True, check=True).stdout.split()
    return int(major), int(minor)


async def _type_and_delete(home: Path, prompt: str, count: int, still_input: bool) -> None:
    """Type *count* characters after a command in a one-row terminal, delete them and check ``has_input()``."""
    async with _real_bash(home, prompt, height=1) as (terminal, _pilot):
        # After some output the prompt is on the bottom row, so a line that wraps scrolls the screen.
        await asyncio.wait_for(terminal.run_command("echo done"), timeout=5)
        await asyncio.sleep(0.5)
        assert not terminal.has_input()
        await _press(terminal, "x", "x", count)
        assert terminal.has_input()
        await _press(terminal, "backspace", "\x7f", count)
        assert terminal.has_input() == still_input


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "count"),
    # The full-width prompt takes two rows itself: 70 characters still fit on the two-row screen.
    [(_SHORT_PROMPT, 90), (_LONG_PROMPT, 90), (_FULL_WIDTH_PROMPT, 70)],
    ids=["short", "long", "full-width"],
)
async def test_deleting_a_wrapped_line_in_one_row_leaves_no_input(home: Path, prompt: str, count: int) -> None:
    await _type_and_delete(home, prompt, count, still_input=False)


@pytest.mark.asyncio
async def test_deleting_a_line_taller_than_the_screen(home: Path) -> None:
    # Three rows on a two-row screen.  Since 5.3, bash redraws such a line on a cleared screen, so the
    # line then still counts as occupied.
    await _type_and_delete(home, _FULL_WIDTH_PROMPT, 90, still_input=_bash_version() >= (5, 3))


@pytest.mark.asyncio
async def test_deleting_a_line_after_maximizing_and_minimizing_leaves_no_input(home: Path) -> None:
    async with _real_bash(home, _SHORT_PROMPT, height=1) as (terminal, pilot):
        await _press(terminal, "x", "x", 5)
        terminal.styles.height = 20
        await pilot.pause(0.5)
        terminal.styles.height = 1
        await pilot.pause(0.5)
        assert terminal.has_input()
        await _press(terminal, "backspace", "\x7f", 5)
        assert not terminal.has_input()


@pytest.mark.asyncio
async def test_zsh_keeps_a_one_row_pty(home: Path) -> None:
    (home / ".zshrc").write_text("PS1='pp$ '\n")
    zsh = shutil.which("zsh")
    assert zsh is not None
    terminal = Terminal(zsh, backend=LocalPtyBackend(), driver=ZshDriver(), keep_alive=True)
    app = _HostApp(terminal, 1)
    async with app.run_test(size=(80, 24)) as pilot:
        try:
            await pilot.pause()
            await _wait_until(lambda: terminal._at_prompt, "the first prompt")
            await asyncio.wait_for(terminal.run_command("stty size > size"), timeout=5)
            assert (home / "size").read_text().split() == ["1", "80"]
        finally:
            terminal.stop()


# ---------------------------------------------------------------------------
# The two-row floor and the one-row view
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _widget(driver: ShellDriver, height: int, scrollback_lines: int = 100) -> AsyncIterator[tuple[Terminal, _RecordingBackend, Pilot[None]]]:
    backend = _RecordingBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=driver, scrollback_lines=scrollback_lines)
    app = _HostApp(terminal, height)
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        try:
            yield terminal, backend, pilot
        finally:
            terminal.stop()


async def _output(terminal: Terminal, pilot: Pilot[None], text: str) -> None:
    assert terminal.recv_queue is not None
    # The held-back startup output ends with the first hook report.
    terminal.recv_queue.put_nowait(["pre_cmd", "/", True])
    terminal.recv_queue.put_nowait(["stdout", text])
    await pilot.pause(0.1)


def _visible(terminal: Terminal) -> list[str]:
    return [terminal.render_line(y).text.rstrip() for y in range(terminal.size.height)]


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", [BashDriver, FallbackDriver])
@pytest.mark.parametrize("height", [0, 1])
async def test_pty_and_screen_keep_two_rows(driver: Callable[[], ShellDriver], height: int) -> None:
    async with _widget(driver(), height) as (terminal, backend, pilot):
        await _output(terminal, pilot, "")  # bash gets no size change before its first hook report
        assert terminal.size.height == height
        assert terminal.nrow == 2
        assert terminal._screen.lines == 2
        assert backend.sizes[-1][0] == 2


async def _set_height(terminal: Terminal, pilot: Pilot[None], height: int) -> None:
    terminal.styles.height = height
    await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_bash_pty_is_resized_only_after_the_first_hook_report() -> None:
    async with _widget(BashDriver(), 10) as (terminal, backend, pilot):
        await _set_height(terminal, pilot, 7)
        await _set_height(terminal, pilot, 5)
        assert backend.sizes == [(24, 80)]  # only the size the PTY was opened with
        assert terminal._screen.lines == 5
        await _output(terminal, pilot, "")
        assert backend.sizes[1:] == [(5, terminal.ncol)]
        assert (terminal._screen.lines, terminal._screen.columns) == backend.sizes[-1]
        await _set_height(terminal, pilot, 1)
        assert backend.sizes[-1] == (2, terminal.ncol)


@pytest.mark.asyncio
async def test_bash_pty_gets_the_held_size_when_the_startup_times_out() -> None:
    async with _widget(BashDriver(), 10) as (terminal, backend, pilot):
        await _set_height(terminal, pilot, 5)
        assert backend.sizes == [(24, 80)]
        terminal._on_startup_timeout(terminal._shell_generation)
        await pilot.pause(0.1)
        assert backend.sizes[1:] == [(5, terminal.ncol)]
        await _set_height(terminal, pilot, 8)
        assert backend.sizes[-1] == (8, terminal.ncol)


@pytest.mark.asyncio
async def test_restarted_bash_pty_is_resized_only_after_its_first_hook_report() -> None:
    async with _widget(BashDriver(), 10) as (terminal, backend, pilot):
        await _output(terminal, pilot, "")
        terminal.respawn()
        await pilot.pause(0.1)
        opened = list(backend.sizes)
        assert opened[-1] == (10, terminal.ncol)
        await _set_height(terminal, pilot, 5)
        assert backend.sizes == opened
        await _output(terminal, pilot, "")
        assert backend.sizes == [*opened, (5, terminal.ncol)]


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", [ZshDriver, VfsShellDriver])
async def test_zsh_and_the_virtual_shell_get_the_widget_height(driver: Callable[[], ShellDriver]) -> None:
    async with _widget(driver(), 1) as (terminal, backend, pilot):
        assert terminal.nrow == 1
        assert terminal._screen.lines == 1
        assert backend.sizes[-1][0] == 1
        terminal.styles.height = 0
        await pilot.pause()
        assert backend.sizes[-1][0] == 0


@pytest.mark.asyncio
async def test_one_row_view_draws_the_cursor_row() -> None:
    async with _widget(BashDriver(), 1) as (terminal, _backend, pilot):
        await _output(terminal, pilot, "first\r\nsecond")
        assert _visible(terminal) == ["second"]
        await _output(terminal, pilot, "\x1b[1;3H")  # the cursor moves to the first row
        assert _visible(terminal) == ["first"]
        await _output(terminal, pilot, "\x1b[2;3H")
        assert _visible(terminal) == ["second"]


@pytest.mark.asyncio
async def test_one_row_view_scrolls_back_through_the_rows_above_the_cursor() -> None:
    async with _widget(BashDriver(), 1) as (terminal, _backend, pilot):
        await _output(terminal, pilot, "one\r\ntwo\r\nthree\r\nfour")
        seen = [_visible(terminal)[0]]
        for _ in range(3):
            terminal.scroll_to(y=terminal.scroll_offset.y - 1, animate=False, force=True, immediate=True)
            await pilot.pause()
            seen.append(_visible(terminal)[0])
        assert seen == ["four", "three", "two", "one"]


@pytest.mark.asyncio
async def test_one_row_view_without_scrollback_draws_the_cursor_row() -> None:
    async with _widget(FallbackDriver(), 1, scrollback_lines=0) as (terminal, _backend, pilot):
        await _output(terminal, pilot, "first\r\nsecond")
        assert _visible(terminal) == ["second"]


@pytest.mark.asyncio
async def test_resizing_between_one_row_and_many_keeps_the_cursor_row_visible() -> None:
    async with _widget(BashDriver(), 1) as (terminal, _backend, pilot):
        await _output(terminal, pilot, "".join(f"line{i}\r\n" for i in range(30)) + "prompt")
        for height in (2, 10, 1, 10, 1, 2):
            terminal.styles.height = height
            await pilot.pause(0.1)
            visible = _visible(terminal)
            assert visible[-1] == "prompt", (height, visible)
            if height > 1:
                assert visible[-2] == "line29", (height, visible)


@pytest.mark.asyncio
async def test_respawn_in_one_row_keeps_two_rows() -> None:
    async with _widget(BashDriver(), 1) as (terminal, backend, pilot):
        await _output(terminal, pilot, "first\r\nsecond")
        terminal.respawn()
        await pilot.pause()
        assert backend.sizes[-1][0] == 2
        assert terminal._screen.lines == 2
        await _output(terminal, pilot, "again")
        assert _visible(terminal) == ["again"]


@pytest.mark.asyncio
async def test_word_selection_in_one_row_view_selects_from_the_cursor_row() -> None:
    async with _widget(BashDriver(), 1) as (terminal, _backend, pilot):
        await _output(terminal, pilot, "first\r\nsecond")
        terminal._select_word_at(1, 0)
        selection = terminal.screen.selections[terminal]
        assert terminal.get_selection(selection) == ("second", "\n")


@pytest.mark.asyncio
async def test_mouse_reports_in_one_row_view_name_the_cursor_row() -> None:
    async with _widget(BashDriver(), 1) as (terminal, backend, pilot):
        await _output(terminal, pilot, "\x1b[?1000h\x1b[?1006hfirst\r\nsecond")
        await pilot.click(terminal, offset=(3, 0))
        await pilot.pause()
        assert b"\x1b[<0;4;2M\x1b[<0;4;2m" in backend.writes


def test_unmounted_terminal_keeps_its_virtual_size() -> None:
    terminal = Terminal("/bin/bash", driver=BashDriver(), scrollback_lines=100)
    terminal._history.append(Text("a"))
    terminal._update_virtual_size()
    assert terminal.virtual_size.height == terminal.nrow + 1


@pytest.mark.asyncio
async def test_hidden_terminal_shows_the_cursor_row_when_shown_again() -> None:
    # The pool hides the terminal of the other filesystem; its shell keeps printing.
    async with _widget(BashDriver(), 1) as (terminal, backend, pilot):
        await _output(terminal, pilot, "one\r\ntwo")
        terminal.display = False
        await pilot.pause(0.1)
        await _output(terminal, pilot, "\r\nthree\r\nfour")
        terminal.display = True
        await pilot.pause(0.1)
        assert _visible(terminal) == ["four"]
        assert backend.sizes[-1][0] == 2

        terminal.display = False
        await pilot.pause(0.1)
        await _output(terminal, pilot, "\x1b[1;1H")  # the cursor moves to the first screen row
        terminal.display = True
        await pilot.pause(0.1)
        assert _visible(terminal) == ["three"]
