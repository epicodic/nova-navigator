"""What the terminal writes on its own (``cd``, stash, restore, setup line) against real line editors.

Whatever the editing mode and the typed text, a hidden write runs nothing the user typed and gives the
text back unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path, PurePath

import pytest

from nova_navigator.commands import TerminalBusyError
from nova_navigator.terminal import shell_driver
from nova_navigator.terminal.shell_driver import BashDriver
from nova_navigator.terminal.terminal import Terminal
from tests.terminal.test_terminal import FakePtyBackend, TerminalTestApp, _start_recv_only, _stop_recv_only
from tests.terminal.test_terminal_has_input import _add_to_history, _real_shell, _send, _wait_until, home

__all__ = ["home"]  # a fixture used by the tests below

_UP = "\x1b[A"
_LEFT = "\x1b[D"
_ESC = "\x1b"
_DUMP = "\x18\x10"  # Ctrl+X Ctrl+P, bound by the rc files below to write the line editor's buffer
_CTRL_W = "\x17"
_CTRL_U = "\x15"
_CTRL_Y = "\x19"

_SHELLS = ["bash", "zsh"]
_MODES = ["emacs", "vi-insert", "vi-command"]
_MULTI_LINE = "touch EXECUTED1\ntouch EXECUTED2\ntouch EXECUTED3"

_BASH_DUMP = """for m in emacs vi-insert vi-command; do bind -m $m -x '"\\C-x\\C-p": printf %s "$READLINE_LINE" >"$HOME/buffer"'; done"""
_ZSH_DUMP = '_dump() { print -rn -- "$BUFFER" >"$HOME/buffer" }; zle -N _dump; for m in emacs viins vicmd; do bindkey -M $m "^X^P" _dump; done'


@contextlib.asynccontextmanager
async def _editing_shell(home: Path, shell: str, mode: str, extra: str = "") -> AsyncIterator[Terminal]:
    vi = mode != "emacs"
    prompt = "PS1='pp$ '"
    zsh_keys = "bindkey -v" if vi else "bindkey -e"
    if shell == "bash":
        prompt += f"\nset -o {'vi' if vi else 'emacs'}\n{_BASH_DUMP}\n{extra}"
    else:
        zsh_keys += f"\n{_ZSH_DUMP}\n{extra}"
    async with _real_shell(home, shell, 20, prompt, zsh_keys=zsh_keys) as (terminal, _pilot):
        yield terminal


async def _enter_mode(terminal: Terminal, mode: str) -> None:
    if mode == "vi-command":
        await _send(terminal, _ESC)


async def _buffer(terminal: Terminal, home: Path) -> str:
    dump = home / "buffer"
    dump.unlink(missing_ok=True)
    await _send(terminal, _DUMP)
    await _wait_until(dump.exists, "the buffer dump")
    return dump.read_text()


async def _change_directory(terminal: Terminal, home: Path) -> None:
    await asyncio.wait_for(terminal.set_terminal_directory(home / "sub"), timeout=10)
    await asyncio.sleep(0.5)
    assert terminal.cwd == home / "sub"


def _executed(home: Path) -> list[str]:
    return sorted(path.name for path in home.rglob("EXECUTED*"))


async def _assert_line_survives(terminal: Terminal, home: Path, line: str) -> None:
    assert _executed(home) == []
    assert await _buffer(terminal, home) == line
    # The cursor is at the end of the restored line, in a mode that takes typed text.
    await _send(terminal, "Z")
    assert await _buffer(terminal, home) == line + "Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_keeps_a_typed_line(home: Path, shell: str, mode: str) -> None:
    async with _editing_shell(home, shell, mode) as terminal:
        await _send(terminal, "touch EXECUTED")
        await _enter_mode(terminal, mode)
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, "touch EXECUTED")


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", [[], [_LEFT] * 100, [_LEFT] * 20], ids=["end", "start", "middle"])
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_runs_no_line_of_a_recalled_multi_line_command(home: Path, shell: str, mode: str, cursor: list[str]) -> None:
    async with _editing_shell(home, shell, mode) as terminal:
        await _add_to_history(terminal, shell, _MULTI_LINE)
        await _send(terminal, _UP)
        if cursor:
            await _send(terminal, "".join(cursor))
        await _enter_mode(terminal, mode)
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, _MULTI_LINE)


@pytest.mark.asyncio
@pytest.mark.parametrize("line", ["touch EXECUTED\n", "\ntouch EXECUTED", "touch EXECUTED1\n\n\ntouch EXECUTED2"], ids=["trailing", "leading", "blank-lines"])
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_keeps_empty_lines_of_a_command(home: Path, shell: str, line: str) -> None:
    async with _editing_shell(home, shell, "emacs") as terminal:
        await _add_to_history(terminal, shell, line)
        await _send(terminal, _UP)
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, line)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_on_an_empty_line(home: Path, shell: str, mode: str) -> None:
    async with _editing_shell(home, shell, mode) as terminal:
        await _enter_mode(terminal, mode)
        assert not terminal.has_input()
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_after_a_word_was_killed_restores_only_the_line(home: Path, shell: str) -> None:
    async with _editing_shell(home, shell, "emacs") as terminal:
        await _send(terminal, "echo aaa bbb", _CTRL_W)
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, "echo aaa ")


@pytest.mark.asyncio
async def test_cd_leaves_the_bash_kill_ring_alone(home: Path) -> None:
    async with _editing_shell(home, "bash", "emacs") as terminal:
        await _send(terminal, "KILLED", _CTRL_U, "echo ")
        await _change_directory(terminal, home)
        await _send(terminal, _CTRL_Y)
        assert await _buffer(terminal, home) == "echo KILLED"


@pytest.mark.asyncio
async def test_cd_runs_nothing_typed_after_zsh_has_reset_its_key_bindings(home: Path) -> None:
    async with _editing_shell(home, "zsh", "emacs") as terminal:
        await asyncio.wait_for(terminal.run_command("bindkey -d; bindkey -e"), timeout=5)
        await asyncio.sleep(0.3)
        await _send(terminal, "touch EXECUTED; echo ")
        await _change_directory(terminal, home)
        assert _executed(home) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["\x16", _ESC], ids=["quoted-insert", "meta-prefix"])
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_runs_nothing_typed_while_a_key_is_pending(home: Path, shell: str, pending: str) -> None:
    async with _editing_shell(home, shell, "emacs") as terminal:
        # Anything appended to this line would belong to the echo, so the touch would run.
        await _send(terminal, "touch EXECUTED; echo ", pending)
        await _change_directory(terminal, home)
        assert _executed(home) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", ["", "touch EXECUTED"], ids=["empty", "typed"])
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("shell", _SHELLS)
async def test_run_command_runs_only_its_own_line(home: Path, shell: str, mode: str, typed: str) -> None:
    async with _editing_shell(home, shell, mode) as terminal:
        if typed:
            await _send(terminal, typed)
        await _enter_mode(terminal, mode)
        await asyncio.wait_for(terminal.run_command("touch RAN"), timeout=5)
        await asyncio.sleep(0.5)
        assert (home / "RAN").exists()
        await _assert_line_survives(terminal, home, typed)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("shell", _SHELLS)
async def test_setup_line_sent_again_runs_nothing_typed(home: Path, shell: str, mode: str) -> None:
    async with _editing_shell(home, shell, mode) as terminal:
        await _enter_mode(terminal, mode)
        # As if the cd to "sub" had brought no hook report.
        terminal._nav_target = home / "sub"
        terminal._nav_busy = True
        terminal._at_prompt = False
        terminal._draining = True
        terminal._on_nav_timeout()
        await _wait_until(lambda: not terminal._nav_busy, "the navigation to end")
        await asyncio.sleep(0.5)
        assert terminal.cwd == home / "sub"
        await _assert_line_survives(terminal, home, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("start", ["for x in 1\r", "echo 'abc\r"], ids=["for", "quote"])
@pytest.mark.parametrize("shell", _SHELLS)
async def test_cd_waits_while_a_command_continues_on_the_next_line(home: Path, shell: str, start: str) -> None:
    finish = "do touch DONE; done\r" if start.startswith("for") else "' >DONE\r"
    async with _editing_shell(home, shell, "emacs") as terminal:
        await _send(terminal, start)
        terminal.request_cd(home / "sub")
        await asyncio.sleep(0.5)
        await _send(terminal, finish)
        await _wait_until(lambda: terminal.cwd == home / "sub", "the cd after the command")
        assert (home / "DONE").exists()


# A main keymap of the user's own, instead of emacs or viins.  The copy of .safe has none of our keys.
_OWN_ZSH_KEYMAP = "bindkey -N mine .safe; bindkey -A mine main"


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["rc", "later"])
async def test_cd_runs_nothing_typed_with_an_own_zsh_main_keymap(home: Path, when: str) -> None:
    extra = "bindkey -N mine emacs; bindkey -A mine main" if when == "rc" else ""
    async with _editing_shell(home, "zsh", "emacs", extra) as terminal:
        if when == "later":
            await asyncio.wait_for(terminal.run_command(_OWN_ZSH_KEYMAP), timeout=5)
            await asyncio.sleep(0.3)
        await _send(terminal, "touch EXECUTED; echo ")
        await _change_directory(terminal, home)
        assert _executed(home) == []
        # The restored line is the typed one: running it now creates exactly that file.
        await _send(terminal, "\r")
        await _wait_until(lambda: _executed(home) == ["EXECUTED"], "the restored line to run")
        assert "4~" not in "".join(terminal._screen.display)


# bash without line editing: the line is edited by the PTY's line discipline alone.
_NO_EDITING = "set +o emacs"


@pytest.mark.asyncio
async def test_cd_on_an_empty_line_works_without_bash_line_editing(home: Path) -> None:
    async with _editing_shell(home, "bash", "emacs", _NO_EDITING) as terminal:
        await _change_directory(terminal, home)
        await asyncio.wait_for(terminal.set_terminal_directory(home), timeout=10)
        await asyncio.sleep(0.5)
        assert terminal.cwd == home
        # Nothing of the hidden writes was run as a command.
        assert "command not found" not in "".join(terminal._screen.display)


async def _assert_followed_only_after_enter(terminal: Terminal, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The typed "touch EXECUTED" stays unrun and alone on the line until the user enters it."""
    written: list[bytes] = []
    write = terminal._backend.write

    def record(data: bytes) -> None:
        written.append(data)
        write(data)

    monkeypatch.setattr(terminal._backend, "write", record)
    await _send(terminal, "touch EXECUTED")
    await asyncio.wait_for(terminal.set_terminal_directory(home / "sub"), timeout=10)
    await asyncio.sleep(0.5)
    assert _executed(home) == []
    assert written == [b"touch EXECUTED"]
    assert terminal.cwd == home
    with pytest.raises(TerminalBusyError):
        await terminal.run_command("touch RAN")
    await _send(terminal, "\r")
    await _wait_until(lambda: terminal.cwd == home / "sub", "the cd after Enter")
    await asyncio.sleep(0.5)
    # Exactly the typed command ran, where it was typed, with nothing appended to it.
    assert _executed(home) == ["EXECUTED"]
    assert (home / "EXECUTED").exists()
    assert sorted(path.name for path in home.glob("*[~[]*")) == []
    screen = "".join(terminal._screen.display)
    assert "~" not in screen
    assert "command not found" not in screen


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "switch"), [("emacs", "set +o emacs"), ("vi-insert", "set +o vi")])
@pytest.mark.parametrize("when", ["rc", "prompt"])
async def test_cd_runs_nothing_typed_without_bash_line_editing(home: Path, monkeypatch: pytest.MonkeyPatch, when: str, mode: str, switch: str) -> None:
    async with _editing_shell(home, "bash", mode, switch if when == "rc" else "") as terminal:
        if when == "prompt":
            await _send(terminal, f"{switch}\r")
        await _assert_followed_only_after_enter(terminal, home, monkeypatch)


@pytest.mark.asyncio
async def test_cd_runs_nothing_typed_with_a_bash_older_than_the_minimum(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An old bash cannot be run here: the installed one is told that it is older than the minimum.
    monkeypatch.setattr(shell_driver, "_BASH_MIN_EDITING_VERSION", 99, raising=False)
    async with _editing_shell(home, "bash", "emacs") as terminal:
        await _assert_followed_only_after_enter(terminal, home, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["rc", "prompt"])
async def test_bash_line_editing_switched_on_again_keeps_the_line(home: Path, switch: str) -> None:
    async with _editing_shell(home, "bash", "emacs", _NO_EDITING if switch == "rc" else "") as terminal:
        if switch == "prompt":
            await _send(terminal, f"{_NO_EDITING}\r")
        await _send(terminal, "set -o emacs\r")
        await _send(terminal, "touch EXECUTED")
        await _change_directory(terminal, home)
        await _assert_line_survives(terminal, home, "touch EXECUTED")


@pytest.mark.asyncio
async def test_a_command_that_switches_bash_line_editing_off_gets_no_restore_keys(home: Path) -> None:
    async with _editing_shell(home, "bash", "emacs") as terminal:
        await _send(terminal, "touch EXECUTED")
        await asyncio.wait_for(terminal.run_command(_NO_EDITING), timeout=5)
        await asyncio.sleep(0.5)
        assert _executed(home) == []
        assert "~" not in "".join(terminal._screen.display)
        # The line set aside comes back with the first restore after editing is on again,
        # in front of what was typed meanwhile, and nothing of it is lost.
        await _send(terminal, "set -o emacs\r")
        await _send(terminal, "echo ")
        await _change_directory(terminal, home)
        assert _executed(home) == []
        assert await _buffer(terminal, home) == "touch EXECUTEDecho "


_NO_EDITING_REPORT = False  # the fourth field of a pre_cmd message


@pytest.mark.asyncio
async def test_a_navigation_requested_before_a_shell_without_editing_reported_does_not_hang() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/bash", backend=backend, driver=BashDriver())
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            navigation = asyncio.ensure_future(terminal.set_terminal_directory(PurePath("/b")))
            await asyncio.sleep(0)
            await recv_q.put(["pre_cmd", "/a", True, _NO_EDITING_REPORT])
            assert await asyncio.wait_for(navigation, timeout=2) == PurePath("/a")
            assert backend.writes == [(" " + BashDriver().cd_command("/b") + "\n").encode()]
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_a_navigation_ends_when_the_shell_stops_editing_during_it() -> None:
    backend = FakePtyBackend()
    terminal = Terminal("/bin/bash", backend=backend, driver=BashDriver())
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            terminal._started = True
            terminal._at_prompt = True
            terminal._cwd = PurePath("/a")
            terminal._note_user_input("l")
            navigation = asyncio.ensure_future(terminal.set_terminal_directory(PurePath("/b")))
            await asyncio.sleep(0)
            writes = len(backend.writes)
            await recv_q.put(["pre_cmd", "/elsewhere", True, _NO_EDITING_REPORT])
            assert await asyncio.wait_for(navigation, timeout=2) == PurePath("/elsewhere")
            assert backend.writes[writes:] == []
            assert terminal._nav_busy is False
            assert terminal._draining is False
        finally:
            terminal._cancel_watchdog()
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_run_command_on_an_empty_line_works_without_bash_line_editing(home: Path) -> None:
    async with _editing_shell(home, "bash", "emacs", _NO_EDITING) as terminal:
        await asyncio.wait_for(terminal.run_command("touch RAN"), timeout=5)
        await asyncio.sleep(0.5)
        assert (home / "RAN").exists()
