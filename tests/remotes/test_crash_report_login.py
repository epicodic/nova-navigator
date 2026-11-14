"""A password typed during login never reaches ``crash.txt`` or ``app.log`` (REQ-30)."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Generator
from typing import Any

import pytest
from rich.logging import RichHandler
from textual.app import App, ComposeResult

from nova_navigator import debug_analytics
from nova_navigator.config.remotes import SshSettings
from nova_navigator.dialogs import CredentialsDialog
from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import KnownHostsLocations
from nova_navigator.remotes.ssh import _Attempt
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems import HopAuthenticationError
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests.remotes.conftest import FlowEnv

TYPED_TEXT = "hunter2zq9"
REDACTED = "<redacted>"


class _Reports:
    def __init__(self, handler: RichHandler) -> None:
        self._handler = handler

    def read(self) -> tuple[str, str]:
        """Return ``(crash.txt, app.log)`` after flushing the log file."""
        self._handler.console.file.flush()
        log_dir = debug_analytics._log_dir
        assert log_dir is not None
        crash = log_dir / "crash.txt"
        crash_text = crash.read_text(encoding="utf-8") if crash.exists() else ""
        return crash_text, (log_dir / "app.log").read_text(encoding="utf-8")


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> Generator[_Reports, None, None]:
    """Turn debug analytics on under the isolated ``$HOME`` and restore all global state afterwards."""
    root = logging.getLogger()
    monkeypatch.setattr(debug_analytics, "DEBUG_ANALYTICS", True)
    monkeypatch.setattr(debug_analytics, "_log_dir", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(root, "level", root.level)
    before = list(root.handlers)
    debug_analytics.install()
    added = [h for h in root.handlers if h not in before]
    handler = next(h for h in added if isinstance(h, RichHandler))
    try:
        yield _Reports(handler)
    finally:
        for h in added:
            root.removeHandler(h)
        handler.console.file.close()


class _LoginApp(App[None]):
    def compose(self) -> ComposeResult:
        return iter([])


def _attempt() -> _Attempt:
    return _Attempt(
        ssh=SshSettings(host="h.example"),
        route=[SshHop("h", "h.example", 22, "u", ())],
        username="u",
        timeout=5.0,
        locations=KnownHostsLocations.default(),
        config=SshConfig.load([]),
    )


@pytest.mark.asyncio
async def test_password_typed_in_dialog_is_absent_from_crash_and_log(reports: _Reports, monkeypatch: pytest.MonkeyPatch) -> None:
    hop = SshHop("h", "h.example", 22, "u", ())
    calls: list[int] = []

    def fake_filesystem(*_args: Any, **_kwargs: Any) -> SSHFilesystem:
        calls.append(1)
        if len(calls) == 1:
            raise HopAuthenticationError([hop], 0)
        raise RuntimeError("backend exploded")

    async def crashing_report(_name: str, _exc: Exception) -> None:
        raise RuntimeError("crash during login")

    monkeypatch.setattr(ssh_module, "SSHFilesystem", fake_filesystem)
    monkeypatch.setattr(ssh_module, "_report_failure", crashing_report)

    app = _LoginApp()
    outcome: asyncio.Future[RuntimeError] = asyncio.get_running_loop().create_future()

    async def login() -> None:
        try:
            await ssh_module._connect("stub", _attempt())
        except RuntimeError as exc:
            outcome.set_result(exc)

    async with app.run_test(size=(100, 30)) as pilot:
        app.run_worker(login())
        await pilot.pause()
        assert isinstance(app.screen, CredentialsDialog)
        await pilot.click("#input_password")
        await pilot.press(*TYPED_TEXT)
        await pilot.click("#OK")
        await pilot.pause()
        exc = await asyncio.wait_for(outcome, timeout=10)

    debug_analytics.write_crash(exc)
    crash_text, app_log_text = reports.read()

    assert TYPED_TEXT not in crash_text
    assert TYPED_TEXT not in app_log_text
    assert REDACTED in crash_text
    assert "attempt" in crash_text
    assert "backend exploded" in app_log_text


def test_password_given_to_ssh_filesystem_is_absent_from_crash_and_log(reports: _Reports, flow_env: FlowEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash_after_login(_sftp: object) -> str:
        raise RuntimeError("crash after login")

    monkeypatch.setattr("nova_navigator.vfs.filesystems.ssh._login_directory", crash_after_login)
    server = flow_env.server

    with pytest.raises(RuntimeError, match="crash after login") as info:
        SSHFilesystem(server.host, server.port, "testuser", None, TYPED_TEXT, None, server.host_key, flow_env.locations)

    debug_analytics.write_crash(info.value)
    crash_text, app_log_text = reports.read()

    assert TYPED_TEXT not in crash_text
    assert TYPED_TEXT not in app_log_text
    assert REDACTED in crash_text
    assert "context" in crash_text
