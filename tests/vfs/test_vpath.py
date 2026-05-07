import pytest

from nova_navigator.vfs.filesystems import LocalFilesystem
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

    assert walk_result[1][0] == vpath / "dir1"
    assert walk_result[1][1] == []
    assert [f.name for f in walk_result[1][2]] == ["file11.txt", "file12.txt"]

    assert walk_result[2][0] == vpath / "dir2"
    assert [d.name for d in walk_result[2][1]] == ["dir21"]
    assert walk_result[2][2] == []

    assert walk_result[3][0] == vpath / "dir2" / "dir21"
    assert walk_result[3][1] == []
    assert [f.name for f in walk_result[3][2]] == ["file211.txt", "file212.txt"]

    assert walk_result[4][0] == vpath / "dir3"
    assert walk_result[4][1] == []
    assert [f.name for f in walk_result[4][2]] == ["file31.txt"]


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
    result = [(root.name, [d.name for d in dirs], [f.name for f in files]) async for root, dirs, files in vpath.walk()]
    assert result[0] == ("user", ["dir1", "dir2"], [])
    assert result[1] == ("dir1", [], ["file11.txt", "file12.txt"])


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
