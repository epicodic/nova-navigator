"""Fixtures of the real-server tests: the containers, an isolated home, the connect factory and work directories."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from nova_navigator.config import conf_
from nova_navigator.config.remotes import RemoteConnection, SshSettings
from nova_navigator.config.settings import Settings
from nova_navigator.dialogs.credentials_dialog import Credentials
from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import KnownHostsLocations
from nova_navigator.remotes.ssh import connect_ssh
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests.real_servers.docker import ServerUnavailable, unavailable
from tests.real_servers.fakes import ConnectFactory, FakeCredentialsDialog, FakeMessageBox
from tests.real_servers.ssh import LOOPBACK, SshServer, ssh_servers

SERVER_USER = "nova"


def _servers() -> dict[str, SshServer]:
    try:
        return ssh_servers()
    except ServerUnavailable as exc:
        unavailable(str(exc), os.environ)


@pytest.fixture(params=["gnu", "busybox"])
def ssh_server(request: pytest.FixtureRequest) -> SshServer:
    """The OpenSSH server of each flavour, one test run per flavour."""
    return _servers()[request.param]


@pytest.fixture
def ssh_pair() -> dict[str, SshServer]:
    """Both OpenSSH servers, keyed by flavour."""
    return _servers()


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``$HOME`` at an empty directory, drop the SSH agent and replace the dialogs of the connect flow."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.setattr(conf_, "settings", Settings(), raising=False)
    locations = KnownHostsLocations.under(home, tmp_path / "etc")
    monkeypatch.setattr(KnownHostsLocations, "default", classmethod(lambda _cls: locations))
    FakeMessageBox.shown = []
    FakeMessageBox.answers = []
    FakeCredentialsDialog.credentials_to_give = None
    FakeCredentialsDialog.runs = 0
    monkeypatch.setattr(ssh_module, "MessageBox", FakeMessageBox)
    monkeypatch.setattr(ssh_module, "CredentialsDialog", FakeCredentialsDialog)
    return home


@pytest.fixture
def connect(isolated: Path) -> Generator[ConnectFactory, None, None]:
    """Connect through `connect_ssh`; every filesystem opened by a test is closed at teardown."""
    del isolated  # requested for its isolation side effects only
    opened: list[SSHFilesystem] = []

    async def _connect(
        server: SshServer,
        *,
        key: bool = True,
        host: str | None = None,
        port: int | None = None,
    ) -> SSHFilesystem:
        FakeCredentialsDialog.credentials_to_give = Credentials(SERVER_USER, server.password)
        ssh = SshSettings(
            host=LOOPBACK if host is None else host,
            port=server.port if port is None else port,
            user=SERVER_USER,
            identity_file=str(server.identity) if key else None,
        )
        fs = await connect_ssh(RemoteConnection(name=server.flavour, ssh=ssh))
        assert fs is not None
        opened.append(fs)
        return fs

    yield _connect
    for fs in opened:
        fs.close()


@pytest.fixture
def workdir() -> Callable[[SshServer], str]:
    """Return a factory that creates a new empty directory under the server home and returns its path."""

    def _make(server: SshServer) -> str:
        path = f"{server.home}/work-{secrets.token_hex(6)}"
        server.run('mkdir -p "$1"', path)
        return path

    return _make
