from __future__ import annotations

import asyncio
from pathlib import PurePath

import pytest

from nova_navigator.commands import TerminalBusyError
from nova_navigator.terminal.shell_driver import ZshDriver
from nova_navigator.terminal.terminal import Terminal
from tests.terminal.test_terminal import FakePtyBackend, TerminalTestApp, _start_recv_only, _stop_recv_only

_KILL = b"\x1b[96~ \x15"  # ZshDriver.kill_line_sequence()
_YANK = b"\x19\x08"  # ZshDriver.yank_sequence()


def _terminal() -> tuple[FakePtyBackend, Terminal]:
    backend = FakePtyBackend()
    return backend, Terminal("/bin/zsh", backend=backend, driver=ZshDriver())


def _ready(terminal: Terminal) -> None:
    terminal._started = True
    terminal._at_prompt = True
    terminal._cwd = PurePath("/w")


def test_cwd_is_none_before_first_precmd() -> None:
    _, terminal = _terminal()
    assert terminal.cwd is None


@pytest.mark.asyncio
async def test_run_command_writes_line_and_waits_for_precmd() -> None:
    backend, terminal = _terminal()
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            _ready(terminal)
            task = asyncio.ensure_future(terminal.run_command("make"))
            await asyncio.sleep(0.01)
            assert backend.writes == [b"make\n"]
            assert not task.done()
            await recv_q.put(["pre_cmd", "/w\n"])
            await pilot.pause(delay=0.1)
            assert task.done()
            assert terminal.cwd == PurePath("/w")
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_run_command_raises_when_not_at_prompt() -> None:
    _, terminal = _terminal()
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        _ready(terminal)
        terminal._at_prompt = False
        with pytest.raises(TerminalBusyError):
            await terminal.run_command("make")


@pytest.mark.asyncio
async def test_run_command_raises_while_navigating() -> None:
    _, terminal = _terminal()
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        _ready(terminal)
        terminal._nav_busy = True
        with pytest.raises(TerminalBusyError):
            await terminal.run_command("make")


@pytest.mark.asyncio
async def test_run_command_stashes_and_restores_typed_input() -> None:
    backend, terminal = _terminal()
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        recv_q = await _start_recv_only(terminal)
        try:
            _ready(terminal)
            terminal._note_user_input("l")
            task = asyncio.ensure_future(terminal.run_command("make"))
            await asyncio.sleep(0.01)
            assert backend.writes == [_KILL, b"make\n"]
            await recv_q.put(["pre_cmd", "/w\n"])
            await pilot.pause(delay=0.1)
            assert task.done()
            assert backend.writes[-1] == _YANK
            assert terminal.has_input() is True
        finally:
            await _stop_recv_only(terminal)


@pytest.mark.asyncio
async def test_run_command_completes_when_shell_state_resets() -> None:
    _, terminal = _terminal()
    async with TerminalTestApp(terminal).run_test() as pilot:
        await pilot.pause()
        _ready(terminal)
        task = asyncio.ensure_future(terminal.run_command("exit"))
        await asyncio.sleep(0.01)
        terminal._reset_shell_state()
        await asyncio.sleep(0.01)
        assert task.done()
