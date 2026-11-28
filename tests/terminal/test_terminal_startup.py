"""Startup of a local shell: hook timeout, quick-exit limit and restarts of the embedded terminal."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path, PurePath

import pytest
from textual.app import App, ComposeResult

import nova_navigator.terminal.terminal as terminal_module
from nova_navigator.commands.errors import TerminalBusyError
from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import FallbackDriver
from nova_navigator.terminal.terminal import Terminal


class _HostApp(App[None]):
    def __init__(self, terminal: Terminal) -> None:
        super().__init__()
        self._terminal = terminal

    def compose(self) -> ComposeResult:
        yield self._terminal


def _write_shell(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return str(path)


def _screen_text(terminal: Terminal) -> str:
    return "\n".join(terminal._screen.display)


async def _wait_until(predicate: Callable[[], bool], what: str, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(terminal_module, "_STARTUP_TIMEOUT", 0.5)
    return tmp_path


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    opens = [0]
    original = LocalPtyBackend.open

    def counting_open(self: LocalPtyBackend, command: str, rows: int, cols: int) -> int | None:
        opens[0] += 1
        return original(self, command, rows, cols)

    monkeypatch.setattr(LocalPtyBackend, "open", counting_open)
    return opens


@pytest.mark.asyncio
async def test_shell_without_hook_is_shown_after_timeout(home: Path) -> None:
    command = _write_shell(home, "nohook", "echo NOHOOK-BANNER\nexec cat")
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: "NOHOOK-BANNER" in _screen_text(terminal), "the banner")
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_shell_without_hook_gets_no_cd_and_is_never_at_a_prompt(home: Path) -> None:
    command = _write_shell(home, "nohook", "echo NOHOOK-BANNER\nexec cat")
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: "NOHOOK-BANNER" in _screen_text(terminal), "the banner")
            terminal.request_cd(PurePath("/usr"))
            await pilot.pause(delay=0.2)
            assert "cd " not in _screen_text(terminal)
            with pytest.raises(TerminalBusyError):
                await terminal.run_command("true")
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_quickly_exiting_shell_is_not_restarted_forever(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opens = _count_opens(monkeypatch)
    command = _write_shell(home, "gone", "echo GONE\nexit 1")
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: "not restarted" in _screen_text(terminal), "the give-up message")
            assert "GONE" in _screen_text(terminal)
            await pilot.pause(delay=0.3)
            assert opens[0] == 3
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_exit_after_a_prompt_still_restarts(home: Path) -> None:
    class CountingBackend(PtyBackend):
        def __init__(self) -> None:
            super().__init__()
            self.opens = 0

        def open(self, command: str, rows: int, cols: int) -> int | None:
            self.opens += 1
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

    backend = CountingBackend()
    terminal = Terminal("/bin/sh", backend=backend, driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            assert terminal.recv_queue is not None
            for expected in range(2, 6):
                terminal.recv_queue.put_nowait(["pre_cmd", str(home), True])
                terminal.recv_queue.put_nowait(["disconnect", 1])
                await _wait_until(lambda n=expected: backend.opens == n, f"restart {expected}")
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_navigation_waiting_during_startup_is_resolved_by_the_timeout(home: Path) -> None:
    command = _write_shell(home, "nohook", "echo NOHOOK-BANNER\nexec cat")
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            waiting = asyncio.create_task(terminal.set_terminal_directory(PurePath("/usr")))
            result = await asyncio.wait_for(waiting, timeout=3.0)
            assert result == PurePath("/usr")
            assert terminal._at_prompt is False
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_late_hook_report_after_the_timeout_enables_the_integration(home: Path) -> None:
    command = _write_shell(home, "nohook", "echo NOHOOK-BANNER\nexec cat")
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: "NOHOOK-BANNER" in _screen_text(terminal), "the banner")
            assert terminal.recv_queue is not None
            terminal.recv_queue.put_nowait(["pre_cmd", str(home), True])
            await _wait_until(lambda: terminal._at_prompt, "the late hook report")
            assert terminal.cwd == PurePath(home)
        finally:
            terminal.stop()


_SLOW_START = "echo SLOW-START\nsleep 1.5\nexec sh"


@pytest.mark.asyncio
async def test_cd_requested_before_a_slow_start_is_written_by_the_late_hook(home: Path) -> None:
    dest = home / "dest"
    dest.mkdir()
    command = _write_shell(home, "slow", _SLOW_START)
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            terminal.request_cd(dest)
            await _wait_until(lambda: terminal._no_integration, "the startup timeout")
            assert terminal.cwd != PurePath(dest)
            await _wait_until(lambda: terminal.cwd == PurePath(dest), "the late hook to apply the cd")
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_cd_requested_after_the_timeout_is_written_by_the_late_hook(home: Path) -> None:
    dest = home / "dest"
    dest.mkdir()
    command = _write_shell(home, "slow", _SLOW_START)
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: terminal._no_integration, "the startup timeout")
            terminal.request_cd(dest)
            assert terminal._nav_future is None
            assert terminal.cwd != PurePath(dest)
            await _wait_until(lambda: terminal.cwd == PurePath(dest), "the late hook to apply the cd")
        finally:
            terminal.stop()


_EXIT_AFTER_A_WHILE = "sleep 1.5\nexit 1"


@pytest.mark.asyncio
async def test_restart_drops_a_kept_navigation_target(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opens = _count_opens(monkeypatch)
    dest = home / "dest"
    dest.mkdir()
    command = _write_shell(home, "exits", _EXIT_AFTER_A_WHILE)
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            terminal.request_cd(dest)
            assert terminal._nav_target == PurePath(dest)
            await _wait_until(lambda: opens[0] == 2, "the restart")
            assert terminal._nav_target is None
        finally:
            terminal.stop()


@pytest.mark.asyncio
async def test_giving_up_drops_a_kept_navigation_target(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opens = _count_opens(monkeypatch)
    dest = home / "dest"
    dest.mkdir()
    command = _write_shell(home, "exits", _EXIT_AFTER_A_WHILE)
    terminal = Terminal(command, backend=LocalPtyBackend(), driver=FallbackDriver(), keep_alive=True)
    app = _HostApp(terminal)
    async with app.run_test() as pilot:
        await pilot.pause()
        terminal.start()
        try:
            await _wait_until(lambda: opens[0] == 3, "the third start")
            terminal.request_cd(dest)
            assert terminal._nav_target == PurePath(dest)
            await _wait_until(lambda: "not restarted" in _screen_text(terminal), "the give-up message")
            assert terminal._nav_target is None
            assert terminal.cwd != PurePath(dest)
        finally:
            terminal.stop()
