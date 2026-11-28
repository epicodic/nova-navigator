"""``has_input()`` with text elsewhere on the screen than the cursor's row.

A hidden ``cd`` written into a line that only looks empty runs that line, so every case here has the
cursor where the prompt left it while the input is on another row.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import BashDriver, FallbackDriver, ShellDriver, ZshDriver
from nova_navigator.terminal.terminal import Terminal

_UP = "\x1b[A"
_CTRL_A = "\x01"
_CTRL_E = "\x05"

_SHELLS: dict[str, tuple[str, Callable[[], ShellDriver]]] = {
    "bash": ("/bin/bash", BashDriver),
    "zsh": ("zsh", ZshDriver),
    "sh": ("/bin/sh", FallbackDriver),
}

# The command that puts a line into the history without running it.
_ADD_TO_HISTORY = {"bash": "history -s", "zsh": "print -s --"}


class _HostApp(App[None]):
    def __init__(self, terminal: Terminal, height: int) -> None:
        super().__init__()
        self._terminal = terminal
        self._terminal.styles.height = height

    def compose(self) -> ComposeResult:
        self._terminal.start()
        yield self._terminal


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home, so that no test reads or writes the developer's shell setup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("PS1", "PROMPT_COMMAND", "HISTFILE", "ENV", "BASH_ENV", "INPUTRC", "ZDOTDIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    return tmp_path


async def _wait_until(predicate: Callable[[], bool], what: str, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


@contextlib.asynccontextmanager
async def _real_shell(home: Path, shell: str, height: int, prompt: str = "PS1='pp$ '", *, at_bottom: bool = False, zsh_keys: str = "bindkey -e") -> AsyncIterator[tuple[Terminal, Pilot[None]]]:
    """Run *shell* in a terminal widget of *height* rows; the shell is killed at the end in any case."""
    (home / ".bashrc").write_text(f"{prompt}\n")
    (home / ".zshrc").write_text(f"{prompt}\n{zsh_keys}\nHISTSIZE=100\n")
    (home / "shrc").write_text(f"{prompt}\n")
    os.environ["ENV"] = str(home / "shrc")  # restored by the monkeypatch of the home fixture
    name, driver = _SHELLS[shell]
    command = shutil.which(name)
    assert command is not None
    backend = LocalPtyBackend()
    terminal = Terminal(command, backend=backend, driver=driver(), keep_alive=True, scrollback_lines=100)
    async with _HostApp(terminal, height).run_test(size=(80, 30)) as pilot:
        pid = backend._pid
        try:
            await pilot.pause()
            await _wait_until(lambda: terminal._at_prompt, "the first prompt")
            # The prompt is printed after the hook has reported.
            await asyncio.sleep(0.5)
            if at_bottom:
                await asyncio.wait_for(terminal.run_command("seq 40"), timeout=5)
                await asyncio.sleep(0.3)
            yield terminal, pilot
        finally:
            # Nothing reaps the child before teardown(), so the pid is still the shell's.
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            terminal.stop()


async def _send(terminal: Terminal, *data: str) -> None:
    for item in data:
        await terminal.send(item)
        await asyncio.sleep(0.3)


async def _add_to_history(terminal: Terminal, shell: str, line: str) -> None:
    quoted = line.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    await asyncio.wait_for(terminal.run_command(f"{_ADD_TO_HISTORY[shell]} $'{quoted}'"), timeout=5)
    await asyncio.sleep(0.3)


async def _assert_cd_runs_nothing_typed(terminal: Terminal, home: Path) -> None:
    assert terminal.has_input()
    await asyncio.wait_for(terminal.set_terminal_directory(home / "sub"), timeout=10)
    await asyncio.sleep(0.5)
    assert list(home.rglob("EXECUTED")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("at_bottom", [False, True], ids=["top", "bottom"])
@pytest.mark.parametrize("height", [1, 20])
async def test_recalled_command_below_the_cursor_is_not_run_by_a_cd(home: Path, height: int, at_bottom: bool) -> None:
    async with _real_shell(home, "bash", height, at_bottom=at_bottom) as (terminal, _pilot):
        await _add_to_history(terminal, "bash", "\ntouch EXECUTED")
        await _send(terminal, _UP, _CTRL_A)
        await _assert_cd_runs_nothing_typed(terminal, home)


@pytest.mark.asyncio
@pytest.mark.parametrize("at_bottom", [False, True], ids=["top", "bottom"])
@pytest.mark.parametrize("height", [1, 20])
async def test_bash_command_after_a_line_of_spaces_is_not_run_by_a_cd(home: Path, height: int, at_bottom: bool) -> None:
    async with _real_shell(home, "bash", height, at_bottom=at_bottom) as (terminal, _pilot):
        await _send(terminal, " " * 90, "touch EXECUTED", _CTRL_A)
        await _assert_cd_runs_nothing_typed(terminal, home)


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["zsh", "sh"])
async def test_command_after_a_line_of_spaces_counts_as_input(home: Path, shell: str) -> None:
    async with _real_shell(home, shell, 20) as (terminal, _pilot):
        await _send(terminal, " " * 90, "touch EXECUTED", _CTRL_A)
        assert terminal.has_input()


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("prompt", ["PS1=''", "PS1=$'pp\\n'"], ids=["empty", "ends-in-newline"])
async def test_text_above_the_cursor_counts_as_input(home: Path, shell: str, prompt: str) -> None:
    # The cursor ends on the bottom row, blank and in the column the prompt left it.
    async with _real_shell(home, shell, 20, prompt, at_bottom=True) as (terminal, _pilot):
        await _add_to_history(terminal, shell, "touch EXECUTED\n")
        await _send(terminal, _UP)
        assert terminal.has_input()


@pytest.mark.asyncio
async def test_text_above_the_cursor_after_clearing_the_screen_counts_as_input(home: Path) -> None:
    # After Ctrl+L the cursor is on the line the prompt had, with the command above it.
    async with _real_shell(home, "zsh", 20, "PS1=$'pp\\n'") as (terminal, _pilot):
        await _add_to_history(terminal, "zsh", "touch EXECUTED\n\n")
        await asyncio.wait_for(terminal.run_command("clear"), timeout=5)
        await asyncio.sleep(0.3)
        await _send(terminal, "\r")
        await _send(terminal, _UP, _UP, "\x0c")
        assert "touch EXECUTED" in "".join(terminal._screen.display)
        assert terminal.has_input()


@pytest.mark.asyncio
@pytest.mark.parametrize("line", ["\n\n\n\n\n\ntouch EXECUTED", " " * 400 + "touch EXECUTED"], ids=["newlines", "spaces"])
async def test_input_below_the_cursor_cut_off_by_shrinking_still_counts(home: Path, line: str) -> None:
    async with _real_shell(home, "bash", 20) as (terminal, pilot):
        await _add_to_history(terminal, "bash", line)
        await _send(terminal, _UP, _CTRL_A)
        terminal.styles.height = 1
        await pilot.pause(0.5)
        await _assert_cd_runs_nothing_typed(terminal, home)


# Draws two lines further down and moves the cursor back, so they stay below the next prompt.
_LEAVE_TEXT_BELOW = "printf '\\n\\n\\n\\n" + "s" * 70 + "\\nstale\\033[5A\\r'"


# Not zsh: it clears the screen below its prompt.
@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["bash", "sh"])
async def test_text_left_below_the_prompt_by_a_program_is_not_input(home: Path, shell: str) -> None:
    async with _real_shell(home, shell, 20) as (terminal, _pilot):
        await asyncio.wait_for(terminal.send(f"{_LEAVE_TEXT_BELOW}\r"), timeout=5)
        await asyncio.sleep(0.8)
        assert "stale" in "".join(terminal._screen.display[terminal._screen.cursor.y + 1 :])
        # dash's line discipline erases with ^H, the line editors take DEL.
        erase = "\x08" if shell == "sh" else "\x7f"
        await _send(terminal, "xxx")
        assert terminal.has_input()
        await _send(terminal, erase * 3)
        assert not terminal.has_input()


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["bash", "zsh"])
async def test_deleting_a_wrapped_line_at_the_bottom_leaves_no_input(home: Path, shell: str) -> None:
    async with _real_shell(home, shell, 20, at_bottom=True) as (terminal, _pilot):
        await _send(terminal, "x" * 90, _CTRL_E)
        assert terminal.has_input()
        await _send(terminal, "\x7f" * 90)
        assert not terminal.has_input()


# ---------------------------------------------------------------------------
# The screen comparison on its own
# ---------------------------------------------------------------------------


class _SilentBackend(PtyBackend):
    def open(self, command: str, rows: int, cols: int) -> int | None:
        return None

    def write(self, data: bytes) -> None:
        pass

    def resize(self, rows: int, cols: int) -> None:
        pass

    def resume(self) -> None:
        pass

    def attach_readers(self, loop: asyncio.AbstractEventLoop, recv_queue: asyncio.Queue[list[object]]) -> None:
        pass

    def detach_readers(self) -> None:
        pass

    def teardown(self) -> None:
        pass


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("", False),
        ("\x1b[5;1H\x1b[K", False),
        ("\x1b[5;1Hnew", True),
        ("\x1b[9;1Hnew", True),
        ("\x1b[1;1H\x1b[K", False),
        ("\x1b[1;1Hnew", True),
    ],
    ids=["unchanged", "erased-below", "overwritten-below", "added-below", "erased-above", "overwritten-above"],
)
@pytest.mark.parametrize("driver", [BashDriver, ZshDriver, FallbackDriver])
def test_only_new_text_around_the_cursor_counts(driver: Callable[[], ShellDriver], change: str, expected: bool) -> None:
    terminal = Terminal("/bin/sh", backend=_SilentBackend(), driver=driver())
    terminal._feed_stdout("output\x1b[3;1Hpp$ \x1b[5;1Hstale\x1b[3;5H")
    terminal._refresh_prompt_snapshot()
    terminal._note_user_input("x")
    terminal._feed_stdout(f"{change}\x1b[3;5H")
    assert terminal.has_input() is expected
