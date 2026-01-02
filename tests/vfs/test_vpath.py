from math import floor

from nova_navigator.vfs2 import VPath
from nova_navigator.vfs2.filesystems import LocalFilesystem
from nova_navigator.vfs2.filesystems.ssh import SSHFilesystem
from tests.test_utils import DATA_DIR


def test_vpath_local_filesystem() -> None:
    local_dir = DATA_DIR / "local"
    fs = LocalFilesystem.singleton()
    vpath = VPath(local_dir, fs)

    assert vpath.name == local_dir.name
    assert vpath.stat.is_directory is True
    assert vpath.path == local_dir

    dirs = vpath.iterdir()
    dir_names = sorted([d.name for d in dirs])
    assert dir_names == ["dir1", "dir2", "dir3"]

    dir21 = vpath / "dir2" / "dir21"
    assert dir21.stat.is_directory is True
    files = dir21.iterdir()
    file_names = sorted([f.name for f in files])
    assert file_names == ["file211.txt", "file212.txt"]


def test_vpath_ssh_filesystem() -> None:
    local_dir = DATA_DIR / "local"
    fs = SSHFilesystem("localhost")
    vpath = VPath(local_dir, fs)

    assert vpath.name == local_dir.name
    assert vpath.stat.is_directory is True
    assert vpath.path == local_dir

    dirs = vpath.iterdir()
    dir_names = sorted([d.name for d in dirs])
    assert dir_names == ["dir1", "dir2", "dir3"]

    dir21 = vpath / "dir2" / "dir21"
    assert dir21.stat.is_directory is True
    files = dir21.iterdir()
    file_names = sorted([f.name for f in files])
    assert file_names == ["file211.txt", "file212.txt"]

    file211_ssh = dir21 / "file211.txt"

    fs = LocalFilesystem.singleton()
    file211_local = VPath(local_dir / "dir2" / "dir21" / "file211.txt", fs)

    assert file211_ssh.stat.size == file211_local.stat.size
    assert floor(file211_ssh.stat.modified) == floor(file211_local.stat.modified)
    assert file211_ssh.stat.is_hidden == file211_local.stat.is_hidden
    assert file211_ssh.stat.is_directory == file211_local.stat.is_directory
    assert file211_ssh.stat.is_executable == file211_local.stat.is_executable
    assert file211_ssh.stat.is_symlink == file211_local.stat.is_symlink
