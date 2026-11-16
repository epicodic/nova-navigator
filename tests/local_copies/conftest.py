"""Stub SSH server fixtures for the reopen tests in tests/local_copies.

The known-hosts isolation is applied by the SSH fixtures only (not autouse), so the other
local-copy tests are unaffected.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import KnownHostsLocations
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN_WORD = "testpass"


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


def _isolate_ssh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an SSH test away from the real ``~/.ssh``, ``/etc/ssh`` and the user's ssh-agent."""
    locations = KnownHostsLocations.under(tmp_path / "isolated-home", tmp_path / "isolated-etc")
    monkeypatch.setattr(KnownHostsLocations, "default", classmethod(lambda _cls: locations))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture(scope="module")
def gnu_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("local_copies_gnu_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def sftp_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("local_copies_sftp_root"), refuse_exec=True)
    server.start()
    yield server
    server.stop()


def _connect(server: StubSSHServer) -> tuple[paramiko.SSHClient, SSHFilesystem]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    return client, SSHFilesystem(hostname=server.host, port=server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)


@pytest.fixture(params=["gnu", "sftp"], ids=["gnu", "sftp"])
def mode(request: pytest.FixtureRequest) -> str:
    """The listing mode of the stub server behind ``ssh_fs``: ``gnu`` or ``sftp``."""
    return str(request.param)


@pytest.fixture
def ssh_fs(mode: str, gnu_server: StubSSHServer, sftp_server: StubSSHServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SSHFilesystem, None, None]:
    """An SSHFilesystem against the GNU server or the SFTP-only (``refuse_exec``) server."""
    _isolate_ssh_state(tmp_path, monkeypatch)
    client, filesystem = _connect(gnu_server if mode == "gnu" else sftp_server)
    yield filesystem
    client.close()


@pytest.fixture
def gnu_fs(gnu_server: StubSSHServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SSHFilesystem, None, None]:
    """An SSHFilesystem against the GNU server only."""
    _isolate_ssh_state(tmp_path, monkeypatch)
    client, filesystem = _connect(gnu_server)
    yield filesystem
    client.close()
