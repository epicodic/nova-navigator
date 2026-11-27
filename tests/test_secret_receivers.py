"""Every place that receives a password registers it (REQ-26, ADR-5)."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.app import App

from nova_navigator.config import loader
from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.config.remotes import RemoteConfig
from nova_navigator.dialogs.credentials_dialog import CredentialsDialog
from nova_navigator.dialogs.edit_bookmarks_dialog import EditBookmarksDialog
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


def test_loading_bookmarks_registers_uri_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "bookmarks.toml").write_text(f'[[groups]]\nname = "g"\n[[groups.bookmarks]]\nname = "b"\npath = "ssh://user:{TYPED}@host/p"\n')
    config = BookmarkConfig.load()
    assert config.groups[0].bookmarks[0].path.startswith("ssh://")
    _assert_registered()


def test_loading_remotes_registers_passwords(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)
    (tmp_path / "remotes.toml").write_text('[a.ssh]\nhost = "h"\nuser = "u:zq9user7k"\n[b.ssh]\nhost = "u:zq9host7k@h"\n[c.azure]\naccount_url = "https://u:zq9azure7k@acc"\ncontainer = "c"\n')
    RemoteConfig.load()
    for secret in ("zq9user7k", "zq9host7k", "zq9azure7k"):
        _assert_registered(secret)


@pytest.mark.asyncio
async def test_edit_bookmarks_dialog_registers_uri_password() -> None:
    config = BookmarkConfig(groups=[Group(name="g", bookmarks=[Bookmark(name="b", path="/p")])])
    app = _DialogApp()
    dialog = EditBookmarksDialog(config)
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(dialog)
        await pilot.pause()
        config.groups[0].bookmarks[0].path = f"ssh://user:{TYPED}@host/p"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not dialog
    _assert_registered()


def test_only_known_password_inputs_exist() -> None:
    """A new password field must register its value; extend this list only together with a registration test."""
    source = Path(__file__).parent.parent / "src"
    found = {
        path.relative_to(source).as_posix()
        for package in ("nova_navigator", "nova_widgets", "nova_editor")
        for path in (source / package).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.keyword) and node.arg == "password" and isinstance(node.value, ast.Constant) and node.value.value is True
    }
    assert found == {"nova_navigator/dialogs/credentials_dialog.py"}
