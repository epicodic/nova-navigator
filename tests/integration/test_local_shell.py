"""Integration tests: the embedded terminal runs the shell named by ``$SHELL``."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from textual import events
from textual.app import App

from nova_navigator.terminal.shell_driver import BashDriver, ZshDriver
from nova_navigator.vfs import VPath
from tests.integration.conftest import AppCtx, poll_until, set_panels

_PROMPT = "nn-test$ "


@pytest.fixture(params=["/bin/bash", "/usr/bin/zsh"])
def shell(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``$SHELL`` before the application is built by the ``app_ctx`` fixture."""
    value = str(request.param)
    monkeypatch.setenv("SHELL", value)
    return value


@pytest.fixture
def short_prompt() -> None:
    """Give bash and zsh a short fixed prompt; use it through ``usefixtures``, so that it runs before
    ``app_ctx`` starts the shell.

    The terminal here is one row high, and ``has_input()`` has a documented limitation there for a
    prompt about as wide as the terminal (see ``docs/terminal.md``).  The default bash prompt
    contains the temporary directory and whatever the system bashrc adds, so its width varies.
    """
    home = Path(os.environ["HOME"])  # the temporary home of the test
    for rc_file in (".bashrc", ".zshrc"):
        (home / rc_file).write_text(f"PS1='{_PROMPT}'\n")


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
async def test_panel_and_shell_directories_follow_each_other(shell: str, app_ctx: AppCtx) -> None:
    subdir = app_ctx.dst_dir / "sub"
    subdir.mkdir()
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    assert terminal.command == shell
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
@pytest.mark.usefixtures("short_prompt")
async def test_typed_input_and_run_command(shell: str, app_ctx: AppCtx) -> None:
    await set_panels(app_ctx)
    terminal = app_ctx.screen._terminal_pool.active_terminal
    assert terminal.command == shell
    await _wait_for(app_ctx, lambda: terminal._at_prompt, "the first prompt")
    # The panel-driven cd must finish before typing, or run_command would find the terminal busy.
    await _wait_for(app_ctx, lambda: terminal.cwd == app_ctx.src_dir, "the shell in the source directory")
    await _wait_for(app_ctx, lambda: not terminal._nav_busy, "the navigation to finish")

    def prompt_is_drawn() -> bool:
        screen = terminal._screen
        return screen.display[screen.cursor.y][: screen.cursor.x].endswith(_PROMPT)

    # The directory is reported before the prompt is printed.  has_input() compares with the screen
    # as it was when typing began, so typing must not begin before the prompt is there.
    await _wait_for(app_ctx, prompt_is_drawn, "the prompt on the screen")

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
