"""Symlink API of the Filesystem ABC across backends (local, archive, azure, remote, mock)."""

from __future__ import annotations

import errno
import os
import zipfile
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from azure.storage.blob import ContainerClient

from nova_navigator.archive.backing import LocalArchiveBacking
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from tests._utils.mock_filesystem import MockFilesystem


def _zip_fs(tmp_path: Path) -> ArchiveFilesystem:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("keep.txt", b"keep")
    local = LocalFilesystem.singleton()
    return ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")


def _azure_fs() -> AzureFilesystem:
    return AzureFilesystem("https://test.blob.core.windows.net", "testcontainer", client=MagicMock(spec=ContainerClient))


def test_abc_declares_symlink_methods_abstract() -> None:
    assert {"supports_symlinks", "symlink"} <= Filesystem.__abstractmethods__
    assert "real_path" not in Filesystem.__abstractmethods__


def test_abc_real_path_default_is_none() -> None:
    fs = MockFilesystem({"/a": b""})
    assert Filesystem.real_path(fs, fs.path("/a")) is None


def test_local_symlink_round_trip(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    link = fs.path(tmp_path / "l")
    fs.symlink(link, "../nowhere/x")
    assert fs.supports_symlinks() is True
    assert fs.readlink(link) == "../nowhere/x"
    stat = fs.stat(link)
    assert stat.is_symlink
    assert stat.is_broken_symlink


def test_local_symlink_existing_raises(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    (tmp_path / "e").write_text("x")
    with pytest.raises(FileExistsError):
        fs.symlink(fs.path(tmp_path / "e"), "t")


def test_local_real_path_resolves_link_chain(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "l1").symlink_to("real", target_is_directory=True)
    (tmp_path / "l2").symlink_to("l1", target_is_directory=True)
    fs = LocalFilesystem()
    resolved = fs.real_path(fs.path(tmp_path / "l2"))
    assert resolved is not None
    assert resolved.path == PurePosixPath(os.path.realpath(tmp_path / "real"))
    assert resolved.filesystem is fs


def test_local_real_path_broken_and_loop_raise(tmp_path: Path) -> None:
    (tmp_path / "broken").symlink_to("nope")
    (tmp_path / "loop").symlink_to("loop")
    fs = LocalFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.real_path(fs.path(tmp_path / "broken"))
    with pytest.raises(OSError, match="symbolic links") as excinfo:
        fs.real_path(fs.path(tmp_path / "loop"))
    assert excinfo.value.errno == errno.ELOOP


def test_archive_does_not_support_symlinks(tmp_path: Path) -> None:
    fs = _zip_fs(tmp_path)
    assert fs.supports_symlinks() is False
    with pytest.raises(OSError, match="symbolic links") as excinfo:
        fs.symlink(fs.path("/l"), "t")
    assert excinfo.value.errno == errno.ENOTSUP


def test_azure_does_not_support_symlinks() -> None:
    fs = _azure_fs()
    assert fs.supports_symlinks() is False
    with pytest.raises(OSError, match="symbolic links") as excinfo:
        fs.symlink(fs.path("/l"), "t")
    assert excinfo.value.errno == errno.ENOTSUP
    assert fs.real_path(fs.path("/l")) is None


@pytest.mark.parametrize("name", ["local", "archive", "azure"])
def test_supports_symlinks_agrees_with_capabilities(name: str, tmp_path: Path) -> None:
    fs: Filesystem = {"local": LocalFilesystem, "archive": lambda: _zip_fs(tmp_path), "azure": _azure_fs}[name]()
    assert fs.supports_symlinks() == fs.capabilities.symlinks


def test_remote_forwards_symlink_methods(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    remote = RemoteFilesystem("prod", LocalFilesystem())
    assert remote.supports_symlinks() is True
    link = remote.path(tmp_path / "l")
    remote.symlink(link, "real")
    assert (tmp_path / "l").readlink() == Path("real")
    resolved = remote.real_path(link)
    assert resolved is not None
    assert resolved.filesystem is remote
    assert resolved.path == PurePosixPath(os.path.realpath(tmp_path / "real"))


def test_remote_forwards_unsupported_inner_real_path_none() -> None:
    remote = RemoteFilesystem("az", _azure_fs())
    assert remote.supports_symlinks() is False
    assert remote.real_path(remote.path("/x")) is None


def test_remote_overrides_every_vpath_taking_non_abstract_method() -> None:
    for name in ("write_atomic", "version_tag", "real_path", "supports_symlinks", "symlink"):
        assert name in RemoteFilesystem.__dict__, f"RemoteFilesystem must forward {name} explicitly (the __getattr__ trap)"


def test_mock_symlink_round_trip_and_stat() -> None:
    fs = MockFilesystem({"/d/f.txt": b"hello"}, links={"/d/l": "f.txt", "/d/b": "nope", "/d/ld": "."})
    assert fs.supports_symlinks() is True
    assert fs.readlink(fs.path("/d/l")) == "f.txt"
    link_stat = fs.stat(fs.path("/d/l"))
    assert link_stat.is_symlink
    assert not link_stat.is_broken_symlink
    assert link_stat.size == 5
    assert not link_stat.is_directory
    assert fs.stat(fs.path("/d/b")).is_broken_symlink
    dir_stat = fs.stat(fs.path("/d/ld"))
    assert dir_stat.is_symlink
    assert dir_stat.is_directory
    assert fs.read(fs.path("/d/l")).read(10) == b"hello"


def test_mock_symlink_creates_link_and_rejects_existing() -> None:
    fs = MockFilesystem({"/d/f.txt": b"x"})
    fs.symlink(fs.path("/d/new"), "f.txt")
    assert fs.readlink(fs.path("/d/new")) == "f.txt"
    with pytest.raises(FileExistsError):
        fs.symlink(fs.path("/d/new"), "g")
    with pytest.raises(FileNotFoundError):
        fs.symlink(fs.path("/missing/new"), "g")


def test_mock_without_symlink_support_raises_enotsup() -> None:
    fs = MockFilesystem({"/d/f.txt": b"x"}, symlinks=False)
    assert fs.supports_symlinks() is False
    with pytest.raises(OSError, match="symbolic links") as excinfo:
        fs.symlink(fs.path("/d/new"), "f.txt")
    assert excinfo.value.errno == errno.ENOTSUP


@pytest.mark.asyncio
async def test_mock_iterdir_through_dir_link_and_remove_only_removes_link() -> None:
    fs = MockFilesystem({"/real/a.txt": b"a", "/d/x": b"x"}, links={"/d/ld": "/real"})
    names = sorted([child.name async for child in fs.path("/d/ld").iterdir()])
    assert names == ["a.txt"]
    fs.remove(fs.path("/d/ld"))
    assert not fs.exists("/d/ld")
    assert fs.exists("/real/a.txt")


def test_mock_real_path_resolves_and_raises() -> None:
    fs = MockFilesystem({"/real/a.txt": b"a"}, links={"/l1": "/real", "/l2": "l1", "/b": "nope", "/loop": "loop"})
    resolved = fs.real_path(fs.path("/l2/a.txt"))
    assert resolved is not None
    assert resolved.path == PurePosixPath("/real/a.txt")
    with pytest.raises(FileNotFoundError):
        fs.real_path(fs.path("/b"))
    with pytest.raises(OSError, match="symbolic links"):
        fs.real_path(fs.path("/loop"))
    assert fs.stat(fs.path("/loop")).is_broken_symlink
