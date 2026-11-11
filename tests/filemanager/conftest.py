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
