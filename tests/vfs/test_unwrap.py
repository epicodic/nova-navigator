from __future__ import annotations

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem


def test_local_filesystem_unwrap_returns_self() -> None:
    fs = LocalFilesystem.singleton()
    assert fs.unwrap() is fs


def test_remote_filesystem_unwrap_returns_inner() -> None:
    inner = LocalFilesystem.singleton()
    remote = RemoteFilesystem("test", inner)
    assert remote.unwrap() is inner


def test_nested_remote_filesystem_unwrap_returns_innermost() -> None:
    inner = LocalFilesystem.singleton()
    middle = RemoteFilesystem("middle", inner)
    outer = RemoteFilesystem("outer", middle)
    assert outer.unwrap() is inner
