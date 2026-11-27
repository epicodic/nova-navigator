"""Every place that receives a password registers it (REQ-26, ADR-5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.app import App

from nova_navigator.dialogs.credentials_dialog import CredentialsDialog
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import _parse_jump
from nova_navigator.secret_registry import REDACTED, scrub
from nova_navigator.vfs.filesystems import ssh as ssh_filesystem
from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.widgets.directory_browser import GoToPathWidget
from nova_widgets import Input

TYPED = "zq9typed7k"


def _assert_registered(secret: str = TYPED) -> None:
    assert scrub(secret) == REDACTED


class _DialogApp(App[None]):
    pass


async def _close_credentials_dialog(*, accept: bool) -> None:
    app = _DialogApp()
    dialog = CredentialsDialog("host", "user")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(dialog)
        await pilot.pause()
        dialog.query_one("#input_password", Input).value = TYPED
        if accept:
            await pilot.click("#OK")
        else:
            await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not dialog


@pytest.mark.asyncio
async def test_credentials_dialog_registers_on_ok() -> None:
    await _close_credentials_dialog(accept=True)
    _assert_registered()


@pytest.mark.asyncio
async def test_credentials_dialog_registers_on_cancel() -> None:
    await _close_credentials_dialog(accept=False)
    _assert_registered()


def test_ssh_filesystem_registers_password_and_hop_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no connection")

    monkeypatch.setattr(ssh_filesystem, "_connect_route", fail)
    with pytest.raises(RuntimeError, match="no connection"):
        ssh_filesystem.SSHFilesystem(
            "h.example",
            password=TYPED,
            credentials={0: ("u", "zq9hop7k")},
            ssh_config=SshConfig.load([]),
        )
    _assert_registered()
    _assert_registered("zq9hop7k")


def test_parse_uri_registers_uri_password() -> None:
    parse_uri(f"ssh://user:{TYPED}@host/path")
    _assert_registered()


def test_go_to_path_registers_uri_password() -> None:
    widget = MagicMock()
    widget.input.value = f"  ssh://user:{TYPED}@host/path  "
    GoToPathWidget.action_accept(widget)
    _assert_registered()


def test_proxy_jump_registers_password() -> None:
    _parse_jump(f"user:{TYPED}@jump.example:22")
    _assert_registered()


def test_ssh_config_user_registers_password(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text(f"Host h\n  User user:{TYPED}\n")
    SshConfig.load([path]).lookup("h")
    _assert_registered()
