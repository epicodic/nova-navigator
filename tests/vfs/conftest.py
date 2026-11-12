"""Stub SSH server fixtures shared by the symlink tests in tests/vfs."""

from __future__ import annotations

from collections.abc import Generator

import paramiko
import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN = ("testuser", "testpass")


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def ssh_stub_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("vfs_symlink_ssh_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def ssh_stub_fs(ssh_stub_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=ssh_stub_server.host, port=ssh_stub_server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)
    yield filesystem
    client.close()


@pytest.fixture
def stub_surrogateescape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the in-process stub SFTP server accept non-UTF-8 bytes like OpenSSH does (same patch as tests/vfs/test_ssh_listing.py)."""

    def get_text(self: paramiko.Message) -> str:
        return self.get_string().decode("utf-8", "surrogateescape")

    monkeypatch.setattr(paramiko.Message, "get_text", get_text)
