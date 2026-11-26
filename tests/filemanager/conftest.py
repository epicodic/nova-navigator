from collections.abc import Generator

import paramiko
import pytest

from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN = ("testuser", "testpass")


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def ssh_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("filemanager_ssh_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture(params=["local", "ssh"])
def link_fs(request: pytest.FixtureRequest) -> Generator[Filesystem, None, None]:
    if request.param == "local":
        yield LocalFilesystem()
        return
    server: StubSSHServer = request.getfixturevalue("ssh_server")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)
    yield filesystem
    client.close()


@pytest.fixture(
    params=[("local", "local"), ("local", "ssh"), ("ssh", "local"), ("ssh", "ssh")],
    ids=lambda pair: f"{pair[0]}-to-{pair[1]}",
)
def fs_pair(request: pytest.FixtureRequest) -> Generator[tuple[Filesystem, Filesystem], None, None]:
    """(source, destination) filesystems; SSH ones are distinct SSHFilesystem objects against the same stub server."""
    clients: list[paramiko.SSHClient] = []

    def make(kind: str) -> Filesystem:
        if kind == "local":
            return LocalFilesystem()
        server: StubSSHServer = request.getfixturevalue("ssh_server")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_AcceptAnyHostKey())
        clients.append(client)
        return SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)

    yield make(request.param[0]), make(request.param[1])
    for client in clients:
        client.close()
