"""Tests for Filesystem.stat_uncached: the default, the local, Azure and SSH backends and the remote wrapper."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import override
from unittest.mock import MagicMock

import paramiko
import pytest
from azure.storage.blob import BlobProperties, ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN_WORD = "testpass"


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


# --- SSH: one SFTP lstat, no listing cache ------------------------------------------------------------------------


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def gnu_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("stat_uncached_gnu_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def refusing_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("stat_uncached_sftp_root"), refuse_exec=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture(params=["gnu", "sftp"], ids=["gnu", "sftp"])
def mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def ssh_fs(mode: str, gnu_server: StubSSHServer, refusing_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    server = gnu_server if mode == "gnu" else refusing_server
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=server.host, port=server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)
    yield filesystem
    client.close()


@pytest.fixture
def gnu_fs(gnu_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=gnu_server.host, port=gnu_server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)
    yield filesystem
    client.close()


def _warm(fs: SSHFilesystem, target: Path, mode: str) -> Stat:
    """Stat *target* through the listing cache and check that the intended listing mode is in use."""
    result = fs.stat(VPath(target, fs))
    assert fs._listing_via_sftp is (mode == "sftp")
    return result


def _remote(fs: SSHFilesystem, target: Path) -> VPath:
    return VPath(target, fs)


def test_ssh_stat_uncached_sees_a_larger_file_while_the_listing_cache_is_warm(ssh_fs: SSHFilesystem, mode: str, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    assert _warm(ssh_fs, target, mode).size == 3
    target.write_bytes(b"abcdefgh")
    assert ssh_fs.stat(_remote(ssh_fs, target)).size == 3  # the listing cache is stale, as in the bug
    assert ssh_fs.stat_uncached(_remote(ssh_fs, target)).size == 8


def test_ssh_stat_uncached_sees_same_size_newer_mtime(ssh_fs: SSHFilesystem, mode: str, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    os.utime(target, (1_000_000, 1_000_000))
    first = _warm(ssh_fs, target, mode)
    os.utime(target, (2_000_000, 2_000_000))
    fresh = ssh_fs.stat_uncached(_remote(ssh_fs, target))
    assert fresh.size == first.size
    assert fresh.modified == 2_000_000


def _make_kind(root: Path, kind: str) -> Path:
    target = root / "item"
    if kind == "file":
        target.write_bytes(b"data")
    elif kind == "executable":
        target.write_bytes(b"#!/bin/sh\n")
        target.chmod(0o755)
    elif kind == "directory":
        target.mkdir()
    elif kind == "link_to_file":
        (root / "real.txt").write_bytes(b"hello")
        target.symlink_to("real.txt")
    elif kind == "link_to_directory":
        (root / "real_dir").mkdir()
        target.symlink_to("real_dir", target_is_directory=True)
    else:
        target.symlink_to("missing-target")
    return target


@pytest.mark.parametrize("kind", ["file", "executable", "directory", "link_to_file", "link_to_directory", "broken_link"])
def test_ssh_stat_uncached_equals_stat_on_a_cold_cache(ssh_fs: SSHFilesystem, tmp_path: Path, kind: str) -> None:
    target = _make_kind(tmp_path, kind)
    cached = ssh_fs.stat(_remote(ssh_fs, target))
    assert ssh_fs.stat_uncached(_remote(ssh_fs, target)) == cached


def test_ssh_stat_uncached_missing_path_raises_file_not_found(ssh_fs: SSHFilesystem, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ssh_fs.stat_uncached(_remote(ssh_fs, tmp_path / "missing"))


def test_ssh_stat_uncached_root_is_a_directory(gnu_fs: SSHFilesystem) -> None:
    assert gnu_fs.stat_uncached(VPath(Path("/"), gnu_fs)).is_directory is True


def test_ssh_stat_uncached_uses_one_lstat_and_runs_no_listing(ssh_fs: SSHFilesystem, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    _warm(ssh_fs, target, mode)
    calls = {"lstat": 0, "stat": 0}
    original_lstat = ssh_fs._sftp_client.lstat
    original_stat = ssh_fs._sftp_client.stat

    def lstat(path: bytes | str) -> paramiko.SFTPAttributes:
        calls["lstat"] += 1
        return original_lstat(path)

    def stat_(path: bytes | str) -> paramiko.SFTPAttributes:
        calls["stat"] += 1
        return original_stat(path)

    def no_listing(*_args: object) -> object:
        pytest.fail("stat_uncached must not run a directory listing")

    commands: list[str] = []
    original_exec = ssh_fs._ssh_client.exec_command

    def spy_exec(command: str) -> object:
        commands.append(command)
        return original_exec(command)

    monkeypatch.setattr(ssh_fs._sftp_client, "lstat", lstat)
    monkeypatch.setattr(ssh_fs._sftp_client, "stat", stat_)
    monkeypatch.setattr(ssh_fs, "_dir_stat", no_listing)
    monkeypatch.setattr(ssh_fs._ssh_client, "exec_command", spy_exec)
    ssh_fs.stat_uncached(_remote(ssh_fs, target))
    assert calls == {"lstat": 1, "stat": 0}
    assert commands == []


def test_ssh_stat_uncached_follows_a_symlink_with_one_extra_stat(gnu_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "real.txt").write_bytes(b"hello")
    (tmp_path / "link").symlink_to("real.txt")
    calls = {"stat": 0}
    original_stat = gnu_fs._sftp_client.stat

    def stat_(path: bytes | str) -> paramiko.SFTPAttributes:
        calls["stat"] += 1
        return original_stat(path)

    monkeypatch.setattr(gnu_fs._sftp_client, "stat", stat_)
    result = gnu_fs.stat_uncached(_remote(gnu_fs, tmp_path / "link"))
    assert calls["stat"] == 1
    assert result.is_symlink is True
    assert result.size == 5


def test_ssh_stat_uncached_leaves_the_listing_cache_untouched(ssh_fs: SSHFilesystem, mode: str, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    _warm(ssh_fs, target, mode)
    before = dict(ssh_fs._stat_cache)
    target.write_bytes(b"abcdef")
    ssh_fs.stat_uncached(_remote(ssh_fs, target))
    assert dict(ssh_fs._stat_cache) == before


def test_ssh_stat_uncached_maps_ssh_exception_to_oserror(gnu_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    raiser: Callable[..., paramiko.SFTPAttributes] = _raise_ssh_exception
    monkeypatch.setattr(gnu_fs._sftp_client, "lstat", raiser)
    with pytest.raises(OSError, match="gone"):
        gnu_fs.stat_uncached(_remote(gnu_fs, target))


def test_ssh_stat_uncached_maps_sftp_error_to_oserror(gnu_fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")
    raiser: Callable[..., paramiko.SFTPAttributes] = _raise_sftp_error
    monkeypatch.setattr(gnu_fs._sftp_client, "lstat", raiser)
    with pytest.raises(OSError, match="Expected name response"):
        gnu_fs.stat_uncached(_remote(gnu_fs, target))


def _raise_sftp_error(*_args: object) -> paramiko.SFTPAttributes:
    raise paramiko.SFTPError("Expected name response")


def _raise_ssh_exception(*_args: object) -> paramiko.SFTPAttributes:
    raise paramiko.SSHException("gone")
