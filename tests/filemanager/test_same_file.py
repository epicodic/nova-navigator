"""Copying or moving an entry onto itself removes and truncates nothing; the item is skipped as "same file"."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from nova_navigator.filemanager.tasks import FileCopyOptions, OverwritePolicy, _is_same_entry, copy_files, move_files
from nova_navigator.scheduler import ResponseRequest, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response

from .common import force_cross_device, make_status, run_task

pytestmark = pytest.mark.asyncio

_REASON = "same file"
_POLICIES: list[OverwritePolicy] = ["ask", "overwrite"]
_OPERATIONS = [pytest.param(copy_files, id="copy"), pytest.param(move_files, id="move")]


async def _run(
    operation: object,
    sources: list[VPath],
    destination: VPath,
    policy: OverwritePolicy = "overwrite",
    follow_links: bool = False,
) -> tuple[TaskStatus, list[ResponseRequest]]:
    assert operation is copy_files or operation is move_files
    options = FileCopyOptions(overwrite=policy, follow_links=follow_links)
    status = make_status()
    # "Yes to all" is offered so that an unwanted overwrite question still lets the operation run into the damage.
    requests = await asyncio.wait_for(
        run_task(lambda ctx: operation(ctx, sources, destination, options), [Response.ALL] * 5, status),
        timeout=20,
    )
    return status, requests


def _snapshot(root: Path) -> dict[str, str | bytes | None]:
    """Map every path below *root* to its link text, its bytes, or None for a folder."""
    result: dict[str, str | bytes | None] = {}
    for directory, folders, files in os.walk(root):
        for name in (*folders, *files):
            path = Path(directory) / name
            key = str(path.relative_to(root))
            if path.is_symlink():
                result[key] = f"-> {os.readlink(path)}"
            else:
                result[key] = path.read_bytes() if path.is_file() else None
    return result


def _assert_refused(status: TaskStatus, requests: list[ResponseRequest], source: VPath) -> None:
    assert requests == []
    assert [(item.item, item.reason) for item in status.skipped] == [(source.uri, _REASON)]
    assert status.progress.completed == status.progress.total


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """d/f, d/sub/g, and ``alias`` -> d."""
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "f").write_bytes(b"data-f")
    (tmp_path / "d" / "sub" / "g").write_bytes(b"data-g")
    (tmp_path / "alias").symlink_to("d")
    return tmp_path


@pytest.mark.parametrize("policy", _POLICIES)
@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("destination", ["d", "d/f", "alias", "alias/f"])
async def test_file_onto_itself_is_refused(tree: Path, operation: object, destination: str, policy: OverwritePolicy) -> None:
    fs = LocalFilesystem()
    before = _snapshot(tree)
    source = fs.path(tree / "d" / "f")

    status, requests = await _run(operation, [source], fs.path(tree / destination), policy)

    assert _snapshot(tree) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_cross_device_file_onto_itself_is_refused(tree: Path, monkeypatch: pytest.MonkeyPatch, operation: object) -> None:
    force_cross_device(monkeypatch)
    fs = LocalFilesystem()
    before = _snapshot(tree)
    source = fs.path(tree / "d" / "f")

    status, requests = await _run(operation, [source], fs.path(tree / "alias"))

    assert _snapshot(tree) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("destination", [".", "alias_of_parent"])
async def test_folder_into_its_own_parent_is_refused(tree: Path, operation: object, destination: str) -> None:
    (tree / "alias_of_parent").symlink_to(".")
    fs = LocalFilesystem()
    before = _snapshot(tree / "d")
    source = fs.path(tree / "d")
    # A second source makes copy treat the destination as the folder to copy into.
    (tree / "elsewhere").mkdir()
    (tree / "elsewhere" / "other").write_bytes(b"other")
    other = fs.path(tree / "elsewhere" / "other")

    status, requests = await _run(operation, [source, other], fs.path(tree / destination))

    assert _snapshot(tree / "d") == before
    assert (tree / "other").read_bytes() == b"other"
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_hard_link_met_while_merging_is_refused_and_the_rest_proceeds(tmp_path: Path, operation: object) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "d" / "linked").write_bytes(b"linked")
    (tmp_path / "src" / "d" / "plain").write_bytes(b"plain")
    (tmp_path / "dst" / "d").mkdir(parents=True)
    os.link(tmp_path / "src" / "d" / "linked", tmp_path / "dst" / "d" / "linked")
    fs = LocalFilesystem()

    status, requests = await _run(operation, [fs.path(tmp_path / "src" / "d")], fs.path(tmp_path / "dst"))

    assert requests == []
    assert (tmp_path / "src" / "d" / "linked").read_bytes() == b"linked"
    assert (tmp_path / "dst" / "d" / "linked").read_bytes() == b"linked"
    assert (tmp_path / "dst" / "d" / "plain").read_bytes() == b"plain"
    assert [item.reason for item in status.skipped] == [_REASON]


@pytest.mark.parametrize("cross_device", [False, True], ids=["same-device", "cross-device"])
@pytest.mark.parametrize("follow_links", [False, True], ids=["keep-link", "follow"])
@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("link", ["link", "indirect", "link-in-folder"])
async def test_link_onto_its_own_target_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link: str,
    operation: object,
    follow_links: bool,
    cross_device: bool,
) -> None:
    if cross_device:
        force_cross_device(monkeypatch)
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "x").write_bytes(b"target")
    (tmp_path / "link").mkdir()
    (tmp_path / "link" / "x").symlink_to("../t/x")
    (tmp_path / "indirect").mkdir()
    (tmp_path / "indirect" / "x").symlink_to("../link/x")
    (tmp_path / "link-in-folder").mkdir()
    (tmp_path / "link-in-folder" / "x").symlink_to(tmp_path / "t" / "x")
    fs = LocalFilesystem()
    before = _snapshot(tmp_path)
    source = fs.path(tmp_path / link / "x")

    status, requests = await _run(operation, [source], fs.path(tmp_path / "t"), follow_links=follow_links)

    assert _snapshot(tmp_path) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_link_onto_a_link_it_points_through_is_refused(tmp_path: Path, operation: object) -> None:
    (tmp_path / "real").write_bytes(b"target")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x").symlink_to("../real")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x").symlink_to("../a/x")
    fs = LocalFilesystem()
    before = _snapshot(tmp_path)
    source = fs.path(tmp_path / "b" / "x")

    status, requests = await _run(operation, [source], fs.path(tmp_path / "a"))

    assert _snapshot(tmp_path) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_file_onto_a_link_to_it_keeps_the_data(tmp_path: Path, operation: object) -> None:
    """The link itself is replaced, as for any destination link; nothing is lost, so nothing is refused."""
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "x").write_bytes(b"target")
    (tmp_path / "l").mkdir()
    (tmp_path / "l" / "x").symlink_to("../t/x")
    fs = LocalFilesystem()

    status, _ = await _run(operation, [fs.path(tmp_path / "t" / "x")], fs.path(tmp_path / "l"))

    assert not (tmp_path / "l" / "x").is_symlink()
    assert (tmp_path / "l" / "x").read_bytes() == b"target"
    assert (tmp_path / "t" / "x").exists() == (operation is copy_files)
    assert status.skipped == ()


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("wrapped", ["source", "destination", "both"])
async def test_remote_wrapper_is_the_same_filesystem(tree: Path, operation: object, wrapped: str) -> None:
    local = LocalFilesystem()
    src_fs: Filesystem = RemoteFilesystem("a", local) if wrapped in ("source", "both") else local
    dst_fs: Filesystem = RemoteFilesystem("b", local) if wrapped in ("destination", "both") else local
    before = _snapshot(tree)
    source = src_fs.path(tree / "d" / "f")

    status, requests = await _run(operation, [source], dst_fs.path(tree / "d"))

    assert _snapshot(tree) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("destination", ["d", "alias"])
async def test_file_into_its_own_folder_is_refused_on_every_filesystem(link_fs: Filesystem, tree: Path, operation: object, destination: str) -> None:
    before = _snapshot(tree)
    source = link_fs.path(tree / "d" / "f")

    status, requests = await _run(operation, [source], link_fs.path(tree / destination))

    assert _snapshot(tree) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_two_azure_instances_of_one_container_are_the_same_storage(operation: object) -> None:
    blobs: dict[str, MagicMock] = {}

    def blob_client(name: str) -> MagicMock:
        if name not in blobs:
            blobs[name] = MagicMock()
            if name == "d/f":
                blobs[name].get_blob_properties.return_value = MagicMock(size=4, last_modified=None)
            else:
                blobs[name].get_blob_properties.side_effect = ResourceNotFoundError
        return blobs[name]

    client = MagicMock(spec=ContainerClient)
    client.get_blob_client.side_effect = blob_client
    client.list_blobs.side_effect = lambda name_starts_with: [MagicMock()] if name_starts_with == "d/" else []
    first = AzureFilesystem("https://test.blob.core.windows.net", "box", client=client)
    second = AzureFilesystem("https://test.blob.core.windows.net", "box", client=client)
    source = first.path("/d/f")

    status, requests = await _run(operation, [source], second.path("/d"))

    for blob in blobs.values():
        blob.delete_blob.assert_not_called()
        blob.upload_blob.assert_not_called()
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_file_onto_itself_survives_one_failed_lstat(tree: Path, monkeypatch: pytest.MonkeyPatch, operation: object) -> None:
    target = str(tree / "d" / "f")
    before = _snapshot(tree)
    fs = LocalFilesystem()
    source = fs.path(tree / "d" / "f")
    real_lstat = os.lstat
    failures: list[OSError] = [OSError("transient")]

    def flaky_lstat(path: str | os.PathLike[str], *, dir_fd: int | None = None) -> os.stat_result:
        # One transient failure, only for the comparison made by _is_same_entry on the identical path.
        if os.fspath(path) == target and failures and sys._getframe(1).f_code.co_name == "_is_same_entry":
            raise failures.pop()
        return real_lstat(path, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", flaky_lstat)
        status, requests = await _run(operation, [source], fs.path(tree / "d" / "f"))

    assert _snapshot(tree) == before
    _assert_refused(status, requests, source)


@pytest.mark.parametrize("operation", _OPERATIONS)
async def test_azure_file_onto_itself_survives_one_failed_stat(operation: object, monkeypatch: pytest.MonkeyPatch) -> None:
    blobs: dict[str, MagicMock] = {}

    def blob_client(name: str) -> MagicMock:
        if name not in blobs:
            blobs[name] = MagicMock()
            blobs[name].get_blob_properties.return_value = MagicMock(size=4, last_modified=None)
        return blobs[name]

    client = MagicMock(spec=ContainerClient)
    client.get_blob_client.side_effect = blob_client
    client.list_blobs.return_value = []
    fs = AzureFilesystem("https://test.blob.core.windows.net", "box", client=client)
    source = fs.path("/d/f")
    real_stat = fs.stat
    failures: list[OSError] = [OSError("transient")]

    def flaky_stat(path: VPath) -> Stat:
        # One transient failure, only for the comparison made by _is_same_entry on the identical path.
        if str(path.path) == "/d/f" and failures and sys._getframe(1).f_code.co_name == "_is_same_entry":
            raise failures.pop()
        return real_stat(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "stat", flaky_stat)
        status, requests = await _run(operation, [source], fs.path("/d/f"))

    for blob in blobs.values():
        blob.delete_blob.assert_not_called()
        blob.upload_blob.assert_not_called()
    _assert_refused(status, requests, source)


@pytest.mark.parametrize(
    ("first_account", "second_account", "first_container", "second_container"),
    [
        ("https://test.blob.core.windows.net", "https://test.blob.core.windows.net", "box", "other"),
        ("https://test.blob.core.windows.net", "https://other.blob.core.windows.net", "box", "box"),
    ],
    ids=["other-container", "other-account"],
)
def test_identical_path_on_other_azure_storage_is_not_the_same_entry(first_account: str, second_account: str, first_container: str, second_container: str) -> None:
    client = MagicMock(spec=ContainerClient)
    first = AzureFilesystem(first_account, first_container, client=client)
    second = AzureFilesystem(second_account, second_container, client=client)

    assert not _is_same_entry(first.path("/d/f"), second.path("/d/f"))
    assert not _is_same_entry(second.path("/d/f"), first.path("/d/f"))
