"""Tests for RemoteFilesystem — the named-remote wrapper filesystem."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


def _make(name: str = "prod", files: dict[str, bytes | None] | None = None) -> tuple[MockFilesystem, RemoteFilesystem]:
    inner = MockFilesystem(files)
    return inner, RemoteFilesystem(name, inner)


# ---------------------------------------------------------------------------
# URI generation
# ---------------------------------------------------------------------------


def test_uri_for_path_uses_remote_scheme() -> None:
    _, fs = _make("prod")
    assert fs.uri_for_path(PurePosixPath("/home/user")) == "remote://prod/home/user"


def test_uri_for_path_root() -> None:
    _, fs = _make("box")
    assert fs.uri_for_path(PurePosixPath("/")) == "remote://box/"


def test_vpath_uri_property() -> None:
    _, fs = _make("prod")
    path = VPath("/etc/hosts", fs)
    assert path.uri == "remote://prod/etc/hosts"


# ---------------------------------------------------------------------------
# Path binding — every VPath returned must belong to RemoteFilesystem
# ---------------------------------------------------------------------------


def test_home_bound_to_remote_filesystem() -> None:
    _, fs = _make()
    assert fs.home().filesystem is fs


def test_cwd_bound_to_remote_filesystem() -> None:
    _, fs = _make()
    assert fs.cwd().filesystem is fs


def test_root_bound_to_remote_filesystem() -> None:
    _, fs = _make()
    assert fs.root().filesystem is fs


def test_parent_bound_to_remote_filesystem() -> None:
    _, fs = _make()
    child = VPath("/home/user/file.txt", fs)
    assert fs.parent(child).filesystem is fs


@pytest.mark.asyncio
async def test_iterdir_yields_paths_bound_to_remote_filesystem() -> None:
    _, fs = _make(files={"/dir/a.txt": b"", "/dir/b.txt": b""})
    dir_path = VPath("/dir", fs)
    items = [p async for p in fs.iterdir(dir_path)]
    assert len(items) == 2
    assert all(p.filesystem is fs for p in items)


@pytest.mark.asyncio
async def test_iterdir_preserves_stat() -> None:
    """Stat cache pre-populated by the inner iterdir must survive re-binding."""
    _, fs = _make(files={"/dir/file.txt": b"hello"})
    dir_path = VPath("/dir", fs)
    items = [p async for p in fs.iterdir(dir_path)]
    assert len(items) == 1
    # stat must be accessible without a round-trip (already cached)
    assert items[0]._stat is not None
    assert items[0].stat.size == 5


# ---------------------------------------------------------------------------
# Delegation correctness
# ---------------------------------------------------------------------------


def test_stat_delegates_to_inner() -> None:
    _, fs = _make(files={"/foo.txt": b"hello"})
    path = VPath("/foo.txt", fs)
    stat = fs.stat(path)
    assert stat.size == 5
    assert not stat.is_directory


def test_stat_dir_delegates_to_inner() -> None:
    _, fs = _make(files={"/mydir/x": b""})
    path = VPath("/mydir", fs)
    assert fs.stat(path).is_directory


def test_read_delegates_to_inner() -> None:
    _, fs = _make(files={"/data.bin": b"abc"})
    reader = fs.read(VPath("/data.bin", fs))
    assert reader.read(3) == b"abc"


def test_write_then_read_roundtrip() -> None:
    _, fs = _make()
    writer = fs.write(VPath("/home/user/new.txt", fs))
    writer.write(b"test")
    writer.close()
    reader = fs.read(VPath("/home/user/new.txt", fs))
    assert reader.read(4) == b"test"


def test_remove_delegates_to_inner() -> None:
    inner, fs = _make(files={"/deleted.txt": b""})
    fs.remove(VPath("/deleted.txt", fs))
    with pytest.raises(FileNotFoundError):
        inner.stat(inner.path("/deleted.txt"))


def test_mkdir_delegates_to_inner() -> None:
    inner, fs = _make()
    fs.mkdir(VPath("/home/user/newdir", fs))
    assert inner.stat(inner.path("/home/user/newdir")).is_directory


def test_rmdir_delegates_to_inner() -> None:
    inner, fs = _make(files={"/empty": None})
    fs.rmdir(VPath("/empty", fs))
    with pytest.raises(FileNotFoundError):
        inner.stat(inner.path("/empty"))


def test_rename_delegates_to_inner() -> None:
    inner, fs = _make(files={"/a.txt": b"x"})
    fs.rename(VPath("/a.txt", fs), VPath("/b.txt", fs))
    with pytest.raises(FileNotFoundError):
        inner.stat(inner.path("/a.txt"))
    assert inner.stat(inner.path("/b.txt")).size == 1


def test_readlink_delegates_to_inner() -> None:
    _, fs = _make()
    # MockFilesystem does not support symlinks; readlink raises OSError
    with pytest.raises((OSError, NotImplementedError)):
        fs.readlink(VPath("/home/user", fs))


def test_is_same_device_delegates_to_inner() -> None:
    _, fs = _make()
    p1 = VPath("/home/user", fs)
    p2 = VPath("/etc", fs)
    # Both are on the same RemoteFilesystem, so inner sees same MockFilesystem
    assert fs.is_same_device(p1, p2)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_capabilities_delegated() -> None:
    inner, fs = _make()
    assert fs.capabilities == inner.capabilities


# ---------------------------------------------------------------------------
# Repr
# ---------------------------------------------------------------------------


def test_repr_includes_name() -> None:
    _, fs = _make("staging")
    assert "staging" in repr(fs)
