"""Integration tests: the embedded terminal runs the shell named by ``$SHELL``."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from typing import Any

import pytest
from textual import events
from textual.app import App

from nova_navigator.terminal.shell_driver import BashDriver, ZshDriver
from nova_navigator.vfs import VPath
from tests.integration.conftest import AppCtx, poll_until, set_panels


@pytest.fixture(params=["/bin/bash", "/usr/bin/zsh"])
def shell(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``$SHELL`` before the application is built by the ``app_ctx`` fixture."""
    value = str(request.param)
    monkeypatch.setenv("SHELL", value)
    return value


@pytest.fixture
def unusable_shell(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``$SHELL`` at a path that does not exist."""
    monkeypatch.setenv("SHELL", "/nonexistent/shell")
    return "/nonexistent/shell"


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the messages of every notification the application posts."""
    messages: list[str] = []
    original = App.notify

    def recording_notify(self: App[None], message: str, **kwargs: Any) -> None:
        messages.append(message)
        original(self, message, **kwargs)

    monkeypatch.setattr(App, "notify", recording_notify)
    return messages


async def _wait_for(app_ctx: AppCtx, predicate: Callable[[], bool], what: str) -> None:
    await poll_until(app_ctx.pilot, predicate, max_wait=10.0)
    assert predicate(), f"timed out waiting for {what}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_terminal_runs_the_shell_from_environment(shell: str, app_ctx: AppCtx) -> None:
    terminal = app_ctx.screen._terminal_pool.active_terminal
    assert terminal.command == shell
    expected_driver = ZshDriver if shell.endswith("zsh") else BashDriver
    assert isinstance(terminal._driver, expected_driver)
    await _wait_for(app_ctx, lambda: terminal.cwd is not None, "the first working directory")


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("shell")
async def test_panel_and_shell_directories_follow_each_other(app_ctx: AppCtx) -> None:
    subdir = app_ctx.dst_dir / "sub"
    subdir.mkdir()
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await _wait_for(app_ctx, lambda: terminal.cwd == app_ctx.src_dir, "the shell in the source directory")

    app_ctx.screen._left_panel.set_path(VPath(subdir, app_ctx.fs))
    await _wait_for(app_ctx, lambda: terminal.cwd == subdir, "the shell following the panel")

    await asyncio.wait_for(terminal.run_command(f"cd {shlex.quote(str(app_ctx.src_dir))}"), timeout=10)
    await _wait_for(
        app_ctx,
        lambda: app_ctx.screen.active_panel().path.path == app_ctx.src_dir,
        "the panel following the shell",
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("shell")
async def test_typed_input_and_run_command(app_ctx: AppCtx) -> None:
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    await _wait_for(app_ctx, lambda: terminal._at_prompt, "the first prompt")

    for char in "typed":
        await terminal.on_key(events.Key(char, char))
    await _wait_for(app_ctx, terminal.has_input, "typed input to be detected")
    await terminal.on_key(events.Key("ctrl+u", "\x15"))  # Ctrl-U clears the line in bash and zsh
    await _wait_for(app_ctx, lambda: not terminal.has_input(), "the cleared line")

    await asyncio.wait_for(terminal.run_command("touch made"), timeout=10)
    assert (app_ctx.src_dir / "made").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unusable_shell_warns_and_uses_a_working_one(unusable_shell: str, notifications: list[str], app_ctx: AppCtx) -> None:
    terminal = app_ctx.screen._terminal_pool.active_terminal
    assert terminal.command != unusable_shell
    await _wait_for(app_ctx, lambda: terminal.cwd is not None, "a working directory")
    assert any(unusable_shell in message for message in notifications)
