"""Tests for rm command."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/file.txt": b"content",
            "/home/user/dir/a.txt": b"a",
            "/home/user/dir/b.txt": b"b",
            "/home/user/dir/sub/c.txt": b"c",
        }
    )


@pytest.mark.asyncio
async def test_rm_file(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm file.txt", output.append, output.append)
    assert exit_code == 0
    assert not fs.exists("/home/user/file.txt")


@pytest.mark.asyncio
async def test_rm_directory_without_r_fails(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm dir", output.append, output.append)
    assert exit_code != 0
    assert fs.exists("/home/user/dir")


@pytest.mark.asyncio
async def test_rm_rf_directory(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm -rf dir", output.append, output.append)
    assert exit_code == 0
    assert not fs.exists("/home/user/dir")
    assert not fs.exists("/home/user/dir/a.txt")
    assert not fs.exists("/home/user/dir/sub/c.txt")


@pytest.mark.asyncio
async def test_rm_nonexistent_without_force(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm nonexistent", output.append, output.append)
    assert exit_code != 0


@pytest.mark.asyncio
async def test_rm_nonexistent_with_force(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm -f nonexistent", output.append, output.append)
    assert exit_code == 0


@pytest.mark.asyncio
async def test_rm_r_tree_with_dir_link_removes_only_the_link() -> None:
    fs = MockFilesystem(
        {"/outside/keep.txt": b"keep", "/home/user/tree/f.txt": b"f"},
        links={"/home/user/tree/out": "/outside"},
    )
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("rm -r tree", output.append, output.append) == 0
    assert not fs.exists("/home/user/tree")
    assert fs.exists("/outside/keep.txt")


@pytest.mark.asyncio
async def test_rm_dir_link_without_r_removes_only_the_link() -> None:
    fs = MockFilesystem({"/outside/keep.txt": b"keep"}, links={"/home/user/lnk": "/outside"})
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("rm lnk", output.append, output.append) == 0
    assert not fs.exists("/home/user/lnk")
    assert fs.exists("/outside/keep.txt")


@pytest.mark.asyncio
async def test_rm_broken_link_is_removed() -> None:
    fs = MockFilesystem(links={"/home/user/b": "nope"})
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("rm b", output.append, output.append) == 0
    assert not fs.exists("/home/user/b")


@pytest.mark.asyncio
async def test_rm_r_on_local_filesystem_keeps_link_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "out").symlink_to(outside, target_is_directory=True)
    local = LocalFilesystem()
    interp = VfsShellInterpreter(local, local.path(tmp_path), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("rm -r tree", output.append, output.append) == 0
    assert not tree.exists()
    assert (outside / "keep.txt").read_text() == "keep"
