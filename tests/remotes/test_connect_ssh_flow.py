"""``connect_ssh``: unknown, changed, revoked and mismatched host keys as seen by the user."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, ClassVar

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.dialogs.credentials_dialog import Credentials
from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import KnownHostsLocations, fingerprint, host_key_name, read_known_hosts
from nova_navigator.remotes.ssh import changed_host_message, connect_ssh, save_failure_message
from nova_navigator.vfs.filesystems.ssh import ChangedHostKeyError, SSHFilesystem
from nova_widgets import Response
from tests._utils.known_hosts_files import key_line, make_key
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_UNKNOWN_TITLE = "Unknown Host"


class _FakeMessageBox:
    """Records every box; ``answers`` are consumed per ``run()``; ``hooks`` run when a titled box is shown."""

    shown: ClassVar[list[_FakeMessageBox]] = []
    answers: ClassVar[list[Response]] = []
    hooks: ClassVar[dict[str, Callable[[], None]]] = {}

    def __init__(
        self,
        message: str,
        title: str = "Error",
        buttons: list[object] | None = None,
        variant: str = "default",
        *,
        width: str | None = None,
    ) -> None:
        self.message, self.title, self.buttons, self.variant = message, title, buttons, variant
        _FakeMessageBox.shown.append(self)

    async def run(self) -> Response:
        hook = _FakeMessageBox.hooks.get(self.title)
        if hook is not None:
            hook()
        return _FakeMessageBox.answers.pop(0) if _FakeMessageBox.answers else Response.OK


class _FakeCredentialsDialog:
    runs: ClassVar[int] = 0
    on_run: ClassVar[Callable[[], None] | None] = None

    def __init__(self, hostname: str, username: str = "") -> None:
        self.credentials = Credentials("testuser", "testpass")

    async def run(self) -> Response:
        _FakeCredentialsDialog.runs += 1
        if _FakeCredentialsDialog.on_run is not None:
            _FakeCredentialsDialog.on_run()
        return Response.OK


class _Env:
    def __init__(self, server: StubSSHServer, locations: KnownHostsLocations, identity: Path) -> None:
        self.server = server
        self.locations = locations
        self.identity = identity
        self.constructions = 0

    @property
    def name(self) -> str:
        return host_key_name(self.server.host, self.server.port)

    @property
    def connection(self) -> RemoteConnection:
        ssh = SshSettings(host=self.server.host, port=self.server.port, user="testuser", identity_file=str(self.identity))
        return RemoteConnection(name="stub", ssh=ssh)

    @property
    def known_hosts(self) -> Path:
        return self.locations.user_known_hosts

    def write_known_hosts(self, text: str) -> None:
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        self.known_hosts.write_text(text)

    @property
    def boxes(self) -> list[_FakeMessageBox]:
        return _FakeMessageBox.shown


@pytest.fixture
def flow_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[_Env, None, None]:
    _FakeMessageBox.shown = []
    _FakeMessageBox.answers = []
    _FakeMessageBox.hooks = {}
    _FakeCredentialsDialog.runs = 0
    _FakeCredentialsDialog.on_run = None
    (tmp_path / "root").mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    locations = KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc")
    monkeypatch.setattr(KnownHostsLocations, "default", classmethod(lambda _cls: locations))
    identity = tmp_path / "id_flow"
    private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    identity.write_bytes(private)
    server = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"), reject_publickey=True)
    server.start()
    env = _Env(server, locations, identity)

    class _Counting(SSHFilesystem):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            env.constructions += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ssh_module, "MessageBox", _FakeMessageBox)
    monkeypatch.setattr(ssh_module, "CredentialsDialog", _FakeCredentialsDialog)
    monkeypatch.setattr(ssh_module, "SSHFilesystem", _Counting)
    yield env
    server.stop()


def _stored_keys(env: _Env) -> list[paramiko.PKey]:
    return read_known_hosts([env.known_hosts]).keys_for(env.name)


@pytest.mark.asyncio
async def test_unknown_ok_connects_and_stores_key(flow_env: _Env) -> None:
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
        assert _FakeCredentialsDialog.runs == 1
    finally:
        fs._ssh_client.close()


@pytest.mark.asyncio
async def test_unknown_cancel_returns_none_and_stores_nothing(flow_env: _Env) -> None:
    _FakeMessageBox.answers = [Response.CANCEL]

    assert await connect_ssh(flow_env.connection) is None

    assert not flow_env.known_hosts.exists()
    assert flow_env.constructions == 1


@pytest.mark.asyncio
async def test_changed_key_shows_error_box_only(flow_env: _Env) -> None:
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
    assert _FakeCredentialsDialog.runs == 0
    assert flow_env.known_hosts.read_bytes() == before


@pytest.mark.asyncio
async def test_revoked_key_is_refused_without_reconnect(flow_env: _Env) -> None:
    flow_env.write_known_hosts(key_line("other.example", flow_env.server.host_key, marker="@revoked") + "\n")
    before = flow_env.known_hosts.read_bytes()

    assert await connect_ssh(flow_env.connection) is None

    assert len(flow_env.boxes) == 1
    box = flow_env.boxes[0]
    assert "revoked" in box.message
    assert box.buttons is None
    assert box.variant == "error"
    assert flow_env.constructions == 1
    assert _FakeCredentialsDialog.runs == 0
    assert flow_env.known_hosts.read_bytes() == before


@pytest.mark.asyncio
async def test_confirmed_key_mismatch_stores_nothing(flow_env: _Env) -> None:
    confirmed = flow_env.server.host_key
    other = make_host_key("ecdsa")
    _FakeMessageBox.hooks[_UNKNOWN_TITLE] = lambda: flow_env.server.set_host_key(other)

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
async def test_conflict_from_another_window_is_shown_as_changed_key(flow_env: _Env) -> None:
    """DEC-19: another window stored a different key of the same type between prompt and reconnect."""
    server_key = flow_env.server.host_key
    foreign = make_host_key("rsa")
    line = key_line(flow_env.name, foreign) + "\n"
    _FakeMessageBox.hooks[_UNKNOWN_TITLE] = lambda: flow_env.write_known_hosts(line)

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
    assert _FakeCredentialsDialog.runs == 0


@pytest.mark.asyncio
async def test_changed_key_on_credentials_retry_shows_error_box_only(flow_env: _Env) -> None:
    original = flow_env.server.host_key
    rotated = make_host_key("rsa")
    stored = key_line(flow_env.name, original) + "\n"
    flow_env.write_known_hosts(stored)
    _FakeCredentialsDialog.on_run = lambda: flow_env.server.set_host_key(rotated)

    assert await connect_ssh(flow_env.connection) is None

    assert _FakeCredentialsDialog.runs == 1
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
async def test_save_failure_warns_after_connect(flow_env: _Env) -> None:
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
