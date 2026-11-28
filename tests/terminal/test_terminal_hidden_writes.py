"""What the terminal writes on its own (``cd``, stash, restore, setup line) against real line editors.

Whatever the editing mode and the typed text, a hidden write runs nothing the user typed and gives the
text back unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nova_navigator.terminal.terminal import Terminal
from tests.terminal.test_terminal_has_input import _add_to_history, _real_shell, _send, _wait_until, home

__all__ = ["home"]  # a fixture used by the tests below

_UP = "\x1b[A"
_LEFT = "\x1b[D"
_ESC = "\x1b"
_DUMP = "\x18\x10"  # Ctrl+X Ctrl+P, bound by the rc files below to write the line editor's buffer
_CTRL_W = "\x17"

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


@pytest.mark.asyncio
async def test_run_command_on_an_empty_line_works_without_bash_line_editing(home: Path) -> None:
    async with _editing_shell(home, "bash", "emacs", _NO_EDITING) as terminal:
        await asyncio.wait_for(terminal.run_command("touch RAN"), timeout=5)
        await asyncio.sleep(0.5)
        assert (home / "RAN").exists()
