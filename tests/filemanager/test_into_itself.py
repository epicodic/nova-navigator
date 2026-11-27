"""Copying or moving a folder into itself changes nothing; the folder is skipped as "into itself"."""

import asyncio
import os
from collections.abc import Callable, Generator
from pathlib import Path

import paramiko
import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, copy_files, move_files
from nova_navigator.scheduler import TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_widgets import Response
from tests._utils.stub_ssh_server import StubSSHServer
from tests.filemanager.common import force_cross_device, make_status, run_task
from tests.filemanager.conftest import _LOGIN, _AcceptAnyHostKey

pytestmark = pytest.mark.asyncio

_OPERATIONS = [pytest.param(copy_files, id="copy"), pytest.param(move_files, id="move")]


@pytest.fixture(params=["local", "local-cross-device", "ssh", "two-ssh-connections"])
def pair(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[Filesystem, Filesystem], None, None]:
    """(source, destination) filesystems on one storage."""
    if request.param.startswith("local"):
        if request.param == "local-cross-device":
            force_cross_device(monkeypatch)
        yield LocalFilesystem(), LocalFilesystem()
        return
    server: StubSSHServer = request.getfixturevalue("ssh_server")
    clients: list[paramiko.SSHClient] = []

    def connect() -> SSHFilesystem:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_AcceptAnyHostKey())
        clients.append(client)
        return SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)

    first = connect()
    yield first, first if request.param == "ssh" else connect()
    for client in clients:
        client.close()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """d/f, d/sub/g, ``alias`` -> d/sub and ``link`` -> d."""
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "f").write_bytes(b"data-f")
    (tmp_path / "d" / "sub" / "g").write_bytes(b"data-g")
    (tmp_path / "alias").symlink_to("d/sub")
    (tmp_path / "link").symlink_to("d")
    return tmp_path


def _names(root: Path) -> list[str]:
    return sorted(str(Path(folder, name).relative_to(root)) for folder, folders, files in os.walk(root) for name in (*folders, *files))


async def _run(operation: Callable[..., object], sources: list[object], destination: object, follow_links: bool = False) -> TaskStatus:
    status = make_status()
    options = FileCopyOptions(overwrite="overwrite", follow_links=follow_links)
    await asyncio.wait_for(run_task(lambda ctx: operation(ctx, sources, destination, options), [Response.ALL] * 5, status), timeout=20)
    return status


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("destination", ["d", "d/sub", "d/new", "alias"])
async def test_a_folder_into_itself_is_refused(pair: tuple[Filesystem, Filesystem], tree: Path, operation: Callable[..., object], destination: str) -> None:
    source = pair[0].path(tree / "d")
    before = _names(tree)

    status = await _run(operation, [source], pair[1].path(tree / destination))

    assert _names(tree) == before
    assert [(item.item, item.reason) for item in status.skipped] == [(source.uri, "into itself")]
    assert status.progress.completed == status.progress.total


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_the_rest_of_the_selection_is_still_processed(tree: Path, operation: Callable[..., object]) -> None:
    fs = LocalFilesystem()
    (tree / "other").write_bytes(b"data-o")

    status = await _run(operation, [fs.path(tree / "d"), fs.path(tree / "other")], fs.path(tree / "d" / "sub"))

    assert (tree / "d" / "sub" / "other").read_bytes() == b"data-o"
    assert [item.reason for item in status.skipped] == ["into itself"]


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_a_sibling_whose_name_starts_with_the_source_name_is_not_inside(tree: Path, operation: Callable[..., object]) -> None:
    fs = LocalFilesystem()
    (tree / "d2").mkdir()

    status = await _run(operation, [fs.path(tree / "d")], fs.path(tree / "d2"))

    assert (tree / "d2" / "d" / "f").read_bytes() == b"data-f"
    assert status.skipped == ()


async def test_a_followed_link_whose_target_holds_the_destination_is_refused(tree: Path) -> None:
    fs = LocalFilesystem()
    before = _names(tree)

    status = await _run(copy_files, [fs.path(tree / "link")], fs.path(tree / "d" / "sub"), follow_links=True)

    assert _names(tree) == before
    assert [item.reason for item in status.skipped] == ["into itself"]
