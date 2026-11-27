"""Two SSH connections with the same host, port and user are one storage."""

import asyncio
from collections.abc import Callable, Generator
from pathlib import Path

import paramiko
import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files, move_files
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_widgets import Response
from tests._utils.stub_ssh_server import StubSSHServer
from tests.filemanager.common import make_status, run_task
from tests.filemanager.conftest import _LOGIN, _AcceptAnyHostKey

pytestmark = pytest.mark.asyncio

Connect = Callable[..., SSHFilesystem]


@pytest.fixture
def connect(ssh_server: StubSSHServer) -> Generator[Connect, None, None]:
    """Open a new connection to the stub server; every call is a separate one."""
    clients: list[paramiko.SSHClient] = []

    def make(user: str = _LOGIN[0], requested: str | None = None) -> SSHFilesystem:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_AcceptAnyHostKey())
        clients.append(client)
        # With *requested*, as after ~/.ssh/config: one name is typed and the route holds the effective host, port and user.
        route = None if requested is None else [SshHop(requested, ssh_server.host, ssh_server.port, user, ())]
        return SSHFilesystem(hostname=requested or ssh_server.host, port=ssh_server.port, username=user, password=_LOGIN[1], ssh_client=client, route=route)

    yield make
    for client in clients:
        client.close()


def _tree(root: Path) -> Path:
    (root / "d" / "sub").mkdir(parents=True)
    (root / "d" / "f").write_bytes(b"data-f")
    (root / "d" / "sub" / "g").write_bytes(b"data-g")
    return root


@pytest.mark.parametrize("operation", [copy_files, move_files], ids=["copy", "move"])
@pytest.mark.parametrize(("source", "destination"), [("d/f", "d"), ("d/f", "d/f"), ("d/sub", "d")], ids=["into-own-folder", "onto-itself", "folder-into-parent"])
@pytest.mark.parametrize("wrapped", [False, True], ids=["plain", "remote-names"])
async def test_the_same_entry_through_two_connections_is_refused(connect: Connect, tmp_path: Path, operation: Callable[..., object], source: str, destination: str, wrapped: bool) -> None:
    root = _tree(tmp_path)
    first: Filesystem = connect()
    second: Filesystem = connect()
    if wrapped:
        first, second = RemoteFilesystem("a", first), RemoteFilesystem("b", second)
    src = first.path(root / source)
    status = make_status()
    options = FileCopyOptions(overwrite="overwrite")

    await asyncio.wait_for(run_task(lambda ctx: operation(ctx, [src], second.path(root / destination), options), [Response.ALL] * 5, status), timeout=20)

    assert (root / "d" / "f").read_bytes() == b"data-f"
    assert (root / "d" / "sub" / "g").read_bytes() == b"data-g"
    assert [(item.item, item.reason) for item in status.skipped] == [(src.uri, "same file")]


async def test_the_storage_is_the_host_port_and_user(connect: Connect) -> None:
    first = connect()

    assert first.is_same_storage(connect())
    assert first != connect()
    assert not first.is_same_storage(connect(user="other"))
    assert not first.is_same_storage(LocalFilesystem())
    assert not LocalFilesystem().is_same_storage(first)


async def test_the_effective_host_of_the_route_counts_not_the_typed_name(connect: Connect) -> None:
    assert connect(requested="localhost").is_same_storage(connect())
