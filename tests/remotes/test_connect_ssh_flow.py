"""``connect_ssh``: unknown, changed, revoked and mismatched host keys as seen by the user."""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path

import paramiko
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import fingerprint, read_known_hosts
from nova_navigator.remotes.ssh import changed_host_message, connect_ssh, save_failure_message
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems.ssh import ChangedHostKeyError, SshConnectTimeoutError, UnknownHostKeyError
from nova_widgets import Response
from tests._utils.known_hosts_files import key_line, make_key
from tests._utils.stub_ssh_server import make_host_key
from tests.remotes.conftest import FakeCredentialsDialog, FakeMessageBox, FlowEnv

_UNKNOWN_TITLE = "Unknown Host"


def _stored_keys(env: FlowEnv) -> list[paramiko.PKey]:
    return read_known_hosts([env.known_hosts]).keys_for(env.name)


@pytest.mark.asyncio
async def test_unknown_ok_connects_and_stores_key(flow_env: FlowEnv) -> None:
    fs = await connect_ssh(flow_env.connection)

    assert fs is not None
    try:
        assert [b.title for b in flow_env.boxes] == [_UNKNOWN_TITLE]
        box = flow_env.boxes[0]
        assert box.buttons == [Response.OK, Response.CANCEL]
        assert box.variant == "warning"
        assert fingerprint(flow_env.server.host_key) in box.message
        stored = _stored_keys(flow_env)
        assert [fingerprint(k) for k in stored] == [fingerprint(flow_env.server.host_key)]
        assert FakeCredentialsDialog.runs == 1
    finally:
        fs._ssh_client.close()


@pytest.mark.asyncio
async def test_key_that_stays_unknown_after_confirmation_is_not_asked_again(flow_env: FlowEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    def always_unknown(*_args: object, **_kwargs: object) -> None:
        raise UnknownHostKeyError("h.example", flow_env.server.host_key)

    monkeypatch.setattr(ssh_module, "SSHFilesystem", always_unknown)

    assert await connect_ssh(flow_env.connection) is None

    assert [b.title for b in flow_env.boxes] == [_UNKNOWN_TITLE, "Error"]
    assert flow_env.boxes[1].variant == "error"
    assert "still not accepted" in flow_env.boxes[1].message


@pytest.mark.asyncio
async def test_timeout_is_logged_as_warning_without_traceback(flow_env: FlowEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def times_out(*_args: object, **_kwargs: object) -> None:
        raise SshConnectTimeoutError([SshHop("h", "h.example", 22, None, ())], 0, 5)

    monkeypatch.setattr(ssh_module, "SSHFilesystem", times_out)
    with caplog.at_level(logging.DEBUG, logger=ssh_module.__name__):
        assert await connect_ssh(flow_env.connection) is None

    records = [r for r in caplog.records if r.name == ssh_module.__name__]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].exc_info is None
    assert flow_env.boxes[0].variant == "error"


@pytest.mark.asyncio
async def test_unknown_cancel_returns_none_and_stores_nothing(flow_env: FlowEnv) -> None:
    FakeMessageBox.answers = [Response.CANCEL]

    assert await connect_ssh(flow_env.connection) is None

    assert not flow_env.known_hosts.exists()
    assert flow_env.constructions == 1


@pytest.mark.asyncio
async def test_changed_key_shows_error_box_only(flow_env: FlowEnv) -> None:
    stored = make_host_key("ecdsa")
    flow_env.write_known_hosts(key_line(flow_env.name, stored) + "\n")
    before = flow_env.known_hosts.read_bytes()

    assert await connect_ssh(flow_env.connection) is None

    assert len(flow_env.boxes) == 1
    box = flow_env.boxes[0]
    assert box.variant == "error"
    assert box.buttons is None
    presented = fingerprint(flow_env.server.host_key)
    for text in (flow_env.server.host, "RSA", "SHA256:", "differs", presented, fingerprint(stored)):
        assert text in box.message
    assert f"ssh-keygen -R {shlex.quote(flow_env.name)}" in box.message
    assert "Could not connect" not in box.message
    assert FakeCredentialsDialog.runs == 0
    assert flow_env.known_hosts.read_bytes() == before


@pytest.mark.asyncio
async def test_revoked_key_is_refused_without_reconnect(flow_env: FlowEnv) -> None:
    flow_env.write_known_hosts(key_line("other.example", flow_env.server.host_key, marker="@revoked") + "\n")
    before = flow_env.known_hosts.read_bytes()

    assert await connect_ssh(flow_env.connection) is None

    assert len(flow_env.boxes) == 1
    box = flow_env.boxes[0]
    assert "revoked" in box.message
    assert box.buttons is None
    assert box.variant == "error"
    assert flow_env.constructions == 1
    assert FakeCredentialsDialog.runs == 0
    assert flow_env.known_hosts.read_bytes() == before


@pytest.mark.asyncio
async def test_confirmed_key_mismatch_stores_nothing(flow_env: FlowEnv) -> None:
    confirmed = flow_env.server.host_key
    other = make_host_key("ecdsa")
    FakeMessageBox.hooks[_UNKNOWN_TITLE] = lambda: flow_env.server.set_host_key(other)

    assert await connect_ssh(flow_env.connection) is None

    assert [b.title for b in flow_env.boxes] == [_UNKNOWN_TITLE, "Host Key Mismatch"]
    box = flow_env.boxes[1]
    assert box.buttons is None
    assert box.variant == "error"
    assert "different host key" in box.message
    assert fingerprint(confirmed) in box.message
    assert fingerprint(other) in box.message
    assert not flow_env.known_hosts.exists()


@pytest.mark.asyncio
async def test_conflict_from_another_window_is_shown_as_changed_key(flow_env: FlowEnv) -> None:
    """DEC-19: another window stored a different key of the same type between prompt and reconnect."""
    server_key = flow_env.server.host_key
    foreign = make_host_key("rsa")
    line = key_line(flow_env.name, foreign) + "\n"
    FakeMessageBox.hooks[_UNKNOWN_TITLE] = lambda: flow_env.write_known_hosts(line)

    assert await connect_ssh(flow_env.connection) is None

    assert [b.title for b in flow_env.boxes] == [_UNKNOWN_TITLE, "Host Key Changed"]
    box = flow_env.boxes[1]
    assert box.buttons is None
    assert box.variant == "error"
    assert "differs" in box.message
    assert fingerprint(server_key) in box.message
    assert fingerprint(foreign) in box.message
    assert "Could not connect" not in box.message
    assert flow_env.known_hosts.read_text() == line
    assert FakeCredentialsDialog.runs == 0


@pytest.mark.asyncio
async def test_changed_key_on_credentials_retry_shows_error_box_only(flow_env: FlowEnv) -> None:
    original = flow_env.server.host_key
    rotated = make_host_key("rsa")
    stored = key_line(flow_env.name, original) + "\n"
    flow_env.write_known_hosts(stored)
    FakeCredentialsDialog.on_run = lambda: flow_env.server.set_host_key(rotated)

    assert await connect_ssh(flow_env.connection) is None

    assert FakeCredentialsDialog.runs == 1
    assert len(flow_env.boxes) == 1
    box = flow_env.boxes[0]
    assert box.title == "Host Key Changed"
    assert box.variant == "error"
    assert box.buttons is None
    assert fingerprint(rotated) in box.message
    assert fingerprint(original) in box.message
    assert "Could not connect" not in box.message
    assert flow_env.known_hosts.read_text() == stored


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores directory permissions")
@pytest.mark.asyncio
async def test_save_failure_warns_after_connect(flow_env: FlowEnv) -> None:
    ssh_dir = flow_env.known_hosts.parent
    ssh_dir.mkdir(parents=True)
    ssh_dir.chmod(0o500)
    try:
        fs = await connect_ssh(flow_env.connection)
        assert fs is not None
        fs._ssh_client.close()
    finally:
        ssh_dir.chmod(0o700)

    assert [b.title for b in flow_env.boxes] == [_UNKNOWN_TITLE, "Host Key Not Saved"]
    box = flow_env.boxes[1]
    assert box.variant == "warning"
    assert str(flow_env.known_hosts) in box.message
    assert not flow_env.known_hosts.exists()


def test_message_builders_render_bracketed_names_verbatim() -> None:
    presented, stored = make_key("ecdsa"), make_key("rsa")
    text = changed_host_message(ChangedHostKeyError("[h.example]:2222", presented, stored))
    assert "[h.example]:2222" in text
    assert f"ssh-keygen -R {shlex.quote('[h.example]:2222')}" in text
    assert fingerprint(presented) in text
    assert fingerprint(stored) in text
    assert "differs" in text
    assert "/some/path/known_hosts" in save_failure_message(Path("/some/path/known_hosts"), OSError("denied"))
    assert "denied" in save_failure_message(Path("/some/path/known_hosts"), OSError("denied"))


@pytest.mark.asyncio
async def test_changed_message_is_displayed_with_brackets() -> None:
    from nova_widgets import MessageBox

    text = changed_host_message(ChangedHostKeyError("[h.example]:2222", make_key("ecdsa"), make_key("rsa")))

    class _Host(App[None]):
        def compose(self) -> ComposeResult:
            yield Label("")

        async def on_mount(self) -> None:
            self.push_screen(MessageBox(text, title="Host Key Changed", variant="error"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = [str(label.render()) for label in app.screen.query(Label)]
    assert any("[h.example]:2222" in line for line in rendered)
