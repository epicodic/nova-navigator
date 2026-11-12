import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.types import Stat
from tests._utils.data import DATA_DIR
from tests._utils.mock_filesystem import MockFilesystem


@pytest.mark.asyncio
async def test_vpath_local_filesystem() -> None:
    local_dir = DATA_DIR / "local"
    fs = LocalFilesystem.singleton()
    vpath = fs.path(local_dir)

    assert vpath.name == local_dir.name
    assert vpath.stat.is_directory is True
    assert vpath.path == local_dir

    dirs = [p async for p in vpath.iterdir()]
    dir_names = sorted([d.name for d in dirs])
    assert dir_names == ["dir1", "dir2", "dir3"]

    dir21 = vpath / "dir2" / "dir21"
    assert dir21.stat.is_directory is True
    files = [p async for p in dir21.iterdir()]
    file_names = sorted([f.name for f in files])
    assert file_names == ["file211.txt", "file212.txt"]


# def test_vpath_ssh_filesystem() -> None:
#     local_dir = DATA_DIR / "local"
#     fs = SSHFilesystem("localhost")
#     vpath = fs.path(local_dir)

#     assert vpath.name == local_dir.name
#     assert vpath.stat.is_directory is True
#     assert vpath.path == local_dir

#     dirs = vpath.iterdir()
#     dir_names = sorted([d.name for d in dirs])
#     assert dir_names == ["dir1", "dir2", "dir3"]

#     dir21 = vpath / "dir2" / "dir21"
#     assert dir21.stat.is_directory is True
#     files = dir21.iterdir()
#     file_names = sorted([f.name for f in files])
#     assert file_names == ["file211.txt", "file212.txt"]

#     file211_ssh = dir21 / "file211.txt"

#     fs = LocalFilesystem.singleton()
#     file211_local = VPath(local_dir / "dir2" / "dir21" / "file211.txt", fs)

#     assert file211_ssh.stat.size == file211_local.stat.size
#     assert floor(file211_ssh.stat.modified) == floor(file211_local.stat.modified)
#     assert file211_ssh.stat.is_hidden == file211_local.stat.is_hidden
#     assert file211_ssh.stat.is_directory == file211_local.stat.is_directory
#     assert file211_ssh.stat.is_executable == file211_local.stat.is_executable
#     assert file211_ssh.stat.is_symlink == file211_local.stat.is_symlink


@pytest.mark.asyncio
async def test_walk() -> None:
    fs = MockFilesystem(
        {
            "/home/user/dir1/file11.txt": b"content11",
            "/home/user/dir1/file12.txt": b"content12",
            "/home/user/dir2/dir21/file211.txt": b"content211",
            "/home/user/dir2/dir21/file212.txt": b"content212",
            "/home/user/dir3/file31.txt": b"content31",
        }
    )
    vpath = fs.path("/home/user")
    walk_result = [item async for item in vpath.walk()]

    assert len(walk_result) == 5

    assert walk_result[0][0] == vpath
    assert [d.name for d in walk_result[0][1]] == ["dir1", "dir2", "dir3"]
    assert walk_result[0][2] == []
    assert walk_result[0][3] == []

    assert walk_result[1][0] == vpath / "dir1"
    assert walk_result[1][1] == []
    assert [f.name for f in walk_result[1][2]] == ["file11.txt", "file12.txt"]
    assert walk_result[1][3] == []

    assert walk_result[2][0] == vpath / "dir2"
    assert [d.name for d in walk_result[2][1]] == ["dir21"]
    assert walk_result[2][2] == []
    assert walk_result[2][3] == []

    assert walk_result[3][0] == vpath / "dir2" / "dir21"
    assert walk_result[3][1] == []
    assert [f.name for f in walk_result[3][2]] == ["file211.txt", "file212.txt"]
    assert walk_result[3][3] == []

    assert walk_result[4][0] == vpath / "dir3"
    assert walk_result[4][1] == []
    assert [f.name for f in walk_result[4][2]] == ["file31.txt"]
    assert walk_result[4][3] == []


@pytest.mark.asyncio
async def test_vpath_iterdir_async() -> None:
    fs = MockFilesystem(
        {
            "/home/user/a.txt": b"",
            "/home/user/b.txt": b"",
        }
    )
    names = sorted([p.name async for p in fs.path("/home/user").iterdir()])
    assert names == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_vpath_walk_async() -> None:
    fs = MockFilesystem(
        {
            "/home/user/dir1/file11.txt": b"content11",
            "/home/user/dir1/file12.txt": b"content12",
            "/home/user/dir2/dir21/file211.txt": b"content211",
        }
    )
    vpath = fs.path("/home/user")
    result = [(root.name, [d.name for d in dirs], [f.name for f in files], [lk.name for lk in links]) async for root, dirs, files, links in vpath.walk()]
    assert result[0] == ("user", ["dir1", "dir2"], [], [])
    assert result[1] == ("dir1", [], ["file11.txt", "file12.txt"], [])


@pytest.mark.asyncio
async def test_fresh_stat_fetches_updated_stat() -> None:
    fs = MockFilesystem({"/home/user/file.txt": b"original"})
    vp = fs.path("/home/user/file.txt")
    _ = vp.stat
    assert vp.stat.size == len(b"original")
    writer = fs.write(fs.path("/home/user/file.txt"))
    writer.write(b"new longer content here")
    writer.close()
    vp._stat = None
    new_stat = await vp.fresh_stat()
    assert new_stat.size == len(b"new longer content here")


async def _walk_names(root: VPath) -> list[tuple[str, list[str], list[str], list[str]]]:
    return [(r.name, sorted(d.name for d in dirs), sorted(f.name for f in files), sorted(lk.name for lk in links)) async for r, dirs, files, links in root.walk()]


@pytest.mark.asyncio
async def test_walk_links_category(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "f.txt").write_text("x")
    (tmp_path / "flink").symlink_to("f.txt")
    (tmp_path / "dlink").symlink_to("d", target_is_directory=True)
    (tmp_path / "broken").symlink_to("nope")
    result = await _walk_names(LocalFilesystem().path(tmp_path))
    assert result[0] == (tmp_path.name, ["d"], ["f.txt"], ["broken", "dlink", "flink"])


@pytest.mark.asyncio
async def test_walk_does_not_descend_dir_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "sub" / "deep.txt").write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    (root / "out").symlink_to(outside, target_is_directory=True)
    result = await _walk_names(LocalFilesystem().path(root))
    assert result == [("root", [], [], ["out"])]


@pytest.mark.asyncio
async def test_walk_terminates_on_link_loop(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "up").symlink_to("..", target_is_directory=True)
    (root / "d" / "self").symlink_to("self")
    result = await asyncio.wait_for(_walk_names(LocalFilesystem().path(root)), timeout=10)
    assert result == [("root", ["d"], [], []), ("d", [], [], ["self", "up"])]


@pytest.mark.asyncio
async def test_walk_root_symlink_raises(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "lnk").symlink_to("d", target_is_directory=True)
    with pytest.raises(NotADirectoryError):
        await _walk_names(LocalFilesystem().path(tmp_path / "lnk"))


@pytest.mark.asyncio
async def test_walk_cancel_event_stops_listing(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("x")
    cancel = threading.Event()
    cancel.set()
    results = [item async for item in LocalFilesystem().path(tmp_path).walk(cancel=cancel)]
    assert [(files, dirs, links) for _, dirs, files, links in results] == [([], [], [])]


def test_cached_stat_does_no_io() -> None:
    fs = MagicMock()
    fs.stat = MagicMock(return_value=Stat(size=1))
    uncached = VPath("/a", fs)
    assert uncached.cached_stat is None
    cached = VPath("/b", fs)
    stat = Stat(size=5)
    cached._stat = stat
    assert cached.cached_stat is stat
    fs.stat.assert_not_called()


def test_with_stat_returns_new_vpath_with_cached_stat_and_does_no_io() -> None:
    fs = MagicMock()
    original = VPath("/a", fs)
    stat = Stat(size=5)
    view = original.with_stat(stat)
    assert view is not original
    assert view.path == original.path
    assert view.filesystem is fs
    assert view.cached_stat is stat
    assert original.cached_stat is None
    fs.stat.assert_not_called()
