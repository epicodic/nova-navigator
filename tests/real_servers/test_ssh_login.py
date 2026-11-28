"""Login against the real OpenSSH containers: passwords, keys, ssh_config, host keys and the smoke listing checks."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.dialogs.credentials_dialog import Credentials
from nova_navigator.remotes.known_hosts import host_key_name
from nova_navigator.remotes.ssh import CHANGED_TITLE, UNKNOWN_TITLE, connect_ssh
from nova_widgets import Response
from tests._utils.known_hosts_files import key_line, make_key
from tests.real_servers.fakes import ConnectFactory, FakeCredentialsDialog, FakeMessageBox
from tests.real_servers.ssh import LOOPBACK, SshServer

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]


@pytest.mark.asyncio
async def test_listing_mode_matches_server(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server)
    async for _ in fs.home().iterdir():
        pass
    assert fs._listing_via_sftp == (ssh_server.flavour == "busybox")


@pytest.mark.asyncio
async def test_home_directory(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server)
    assert fs.home().path == PurePosixPath("/home/nova")


@pytest.mark.asyncio
async def test_password_login_without_any_key(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server, key=False)
    assert FakeCredentialsDialog.runs == 1
    assert fs.home().path == PurePosixPath("/home/nova")


@pytest.mark.asyncio
async def test_key_login_with_identity_file(ssh_server: SshServer, connect: ConnectFactory) -> None:
    fs = await connect(ssh_server)
    assert FakeCredentialsDialog.runs == 0
    assert fs.home().path == PurePosixPath("/home/nova")


def _fresh_identity(directory: Path) -> Path:
    path = directory / "id_other"
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
    path.chmod(0o600)
    return path


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_password_login_after_rejected_key(ssh_server: SshServer, tmp_path: Path) -> None:
    FakeCredentialsDialog.credentials_to_give = Credentials("nova", ssh_server.password)
    ssh = SshSettings(host=LOOPBACK, port=ssh_server.port, user="nova", identity_file=str(_fresh_identity(tmp_path)))
    fs = await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=ssh))
    assert fs is not None
    try:
        assert FakeCredentialsDialog.runs == 1
        assert fs.home().path == PurePosixPath("/home/nova")
    finally:
        fs.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_wrong_password_is_reported(ssh_server: SshServer, tmp_path: Path) -> None:
    FakeCredentialsDialog.credentials_to_give = Credentials("nova", "not-the-password")
    ssh = SshSettings(host=LOOPBACK, port=ssh_server.port, user="nova", identity_file=str(_fresh_identity(tmp_path)))
    assert await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=ssh)) is None
    assert FakeCredentialsDialog.runs == 1
    assert [box.title for box in FakeMessageBox.shown] == [UNKNOWN_TITLE, "Error"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated")
async def test_wrong_password_without_any_key_is_reported(ssh_server: SshServer) -> None:
    FakeCredentialsDialog.credentials_to_give = Credentials("nova", "not-the-password")
    ssh = SshSettings(host=LOOPBACK, port=ssh_server.port, user="nova")
    assert await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=ssh)) is None
    assert FakeCredentialsDialog.runs == 1
    assert [box.title for box in FakeMessageBox.shown] == [UNKNOWN_TITLE, "Error"]


@pytest.mark.asyncio
async def test_key_login_with_ssh_config(ssh_server: SshServer, isolated: Path) -> None:
    (isolated / ".ssh").mkdir()
    (isolated / ".ssh" / "config").write_text(f"Host nova-alias\n  HostName {LOOPBACK}\n  Port {ssh_server.port}\n  User nova\n  IdentityFile {ssh_server.identity}\n")
    fs = await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=SshSettings(host="nova-alias")))
    assert fs is not None
    try:
        assert FakeCredentialsDialog.runs == 0
    finally:
        fs.close()


@pytest.mark.asyncio
async def test_unknown_host_key_accept_is_stored(ssh_server: SshServer, connect: ConnectFactory, isolated: Path) -> None:
    await connect(ssh_server)
    assert [box.title for box in FakeMessageBox.shown] == [UNKNOWN_TITLE]
    assert host_key_name(LOOPBACK, ssh_server.port) in (isolated / ".ssh" / "known_hosts").read_text()

    FakeMessageBox.shown.clear()
    await connect(ssh_server)
    assert FakeMessageBox.shown == []


@pytest.mark.asyncio
async def test_unknown_host_key_cancel_stores_nothing(ssh_server: SshServer, isolated: Path) -> None:
    FakeMessageBox.answers = [Response.CANCEL]
    FakeCredentialsDialog.credentials_to_give = Credentials("nova", ssh_server.password)
    ssh = SshSettings(host=LOOPBACK, port=ssh_server.port, user="nova")
    assert await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=ssh)) is None
    assert [box.title for box in FakeMessageBox.shown] == [UNKNOWN_TITLE]
    known_hosts = isolated / ".ssh" / "known_hosts"
    assert not known_hosts.exists() or host_key_name(LOOPBACK, ssh_server.port) not in known_hosts.read_text()


@pytest.mark.asyncio
async def test_changed_host_key_is_refused(ssh_server: SshServer, isolated: Path) -> None:
    known_hosts = isolated / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir()
    text = key_line(host_key_name(LOOPBACK, ssh_server.port), make_key("ecdsa")) + "\n"
    known_hosts.write_text(text)
    FakeCredentialsDialog.credentials_to_give = Credentials("nova", ssh_server.password)
    ssh = SshSettings(host=LOOPBACK, port=ssh_server.port, user="nova")
    assert await connect_ssh(RemoteConnection(name=ssh_server.flavour, ssh=ssh)) is None
    assert [box.title for box in FakeMessageBox.shown] == [CHANGED_TITLE]
    assert known_hosts.read_text() == text
