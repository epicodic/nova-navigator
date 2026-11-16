"""Tests for Filesystem.stat_uncached: the default, the local and Azure backends and the remote wrapper."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import override
from unittest.mock import MagicMock

from azure.storage.blob import BlobProperties, ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


def test_base_stat_uncached_defaults_to_stat() -> None:
    fs = MockFilesystem({"/data/file.txt": b"hello"})
    assert fs.stat_uncached(fs.path("/data/file.txt")).size == 5


def test_local_stat_uncached_equals_stat_and_sees_a_change(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    path = fs.path(str(target))
    assert fs.stat_uncached(path) == fs.stat(path)
    target.write_bytes(b"abcdefgh")
    assert fs.stat_uncached(path).size == 8
    assert fs.stat_uncached(path) == fs.stat(fs.path(str(target)))


def test_azure_stat_uncached_queries_the_client_every_call() -> None:
    mock_client = MagicMock(spec=ContainerClient)
    props = MagicMock(spec=BlobProperties)
    props.size = 42
    props.last_modified = datetime.fromtimestamp(1234.0, tz=UTC)
    mock_client.get_blob_client.return_value.get_blob_properties.return_value = props
    fs = AzureFilesystem("https://test.blob.core.windows.net", "testcontainer", client=mock_client)
    path = fs.path("/data/file.txt")
    assert fs.stat_uncached(path).size == 42
    assert fs.stat_uncached(path).size == 42
    assert mock_client.get_blob_client.call_count == 2


class _RecordingInner(MockFilesystem):
    """Inner filesystem whose stat_uncached records the path it receives."""

    received: list[VPath]

    def __init__(self) -> None:
        super().__init__({"/data/file.txt": b"hello"})
        self.received = []

    @override
    def stat_uncached(self, path: VPath) -> Stat:
        self.received.append(path)
        return Stat(size=99)


def test_remote_stat_uncached_delegates_to_the_inner_filesystem_with_a_rebound_path() -> None:
    inner = _RecordingInner()
    remote = RemoteFilesystem("box", inner)
    result = remote.stat_uncached(remote.path("/data/file.txt"))
    assert result.size == 99
    assert len(inner.received) == 1
    assert inner.received[0].filesystem is inner
    assert str(inner.received[0].path) == "/data/file.txt"
